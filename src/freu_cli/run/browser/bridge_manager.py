"""Supervise the freu-cli HTTP bridge as a subprocess.

The bridge is the same HTTP server that the Chrome extension long-polls. Both
`freu-cli run` and `freu-cli capture` need a running bridge; this manager
starts one on demand, writes its PID to disk, and exposes helpers to check
status and stop it cleanly.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

DEFAULT_BRIDGE_HOST = os.getenv("FREU_BRIDGE_HOST", "127.0.0.1")
DEFAULT_BRIDGE_PORT = int(os.getenv("FREU_BRIDGE_PORT", "8787"))
DEFAULT_IDLE_TIMEOUT_SECONDS = int(os.getenv("FREU_BRIDGE_IDLE_TIMEOUT_SECONDS", "300"))
RUNTIME_DIR = Path(
    os.getenv("FREU_RUNTIME_DIR", str(Path("~/.local/share/freu-cli/run").expanduser()))
).expanduser()
PID_FILE = Path(os.getenv("FREU_BRIDGE_PID_FILE", str(RUNTIME_DIR / "bridge.pid"))).expanduser()
LOG_FILE = Path(os.getenv("FREU_BRIDGE_LOG_FILE", str(RUNTIME_DIR / "bridge.log"))).expanduser()


@dataclass(slots=True)
class BridgeStatus:
    running: bool
    base_url: str
    pid: int | None = None
    payload: dict[str, Any] | None = None


def bridge_base_url() -> str:
    host = os.getenv("FREU_BRIDGE_HOST", DEFAULT_BRIDGE_HOST)
    port = int(os.getenv("FREU_BRIDGE_PORT", str(DEFAULT_BRIDGE_PORT)))
    return f"http://{host}:{port}/bridge"


def bridge_status_url() -> str:
    return bridge_base_url() + "/status"


def get_bridge_status() -> BridgeStatus:
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(Request(bridge_status_url(), method="GET"), timeout=2) as response:
            raw = response.read().decode("utf-8").strip()
    except (HTTPError, URLError, TimeoutError, OSError):
        return BridgeStatus(running=False, base_url=bridge_base_url(), pid=_read_pid_file())

    payload = json.loads(raw) if raw else {}
    return BridgeStatus(
        running=True,
        base_url=bridge_base_url(),
        pid=_read_pid_file(),
        payload=payload if isinstance(payload, dict) else {},
    )


def ensure_bridge_running() -> BridgeStatus:
    status = get_bridge_status()
    if status.running:
        return status
    start_bridge()
    deadline = time.time() + 10
    while time.time() < deadline:
        status = get_bridge_status()
        if status.running:
            return status
        time.sleep(0.2)
    raise RuntimeError("Bridge failed to start before timeout")


@contextmanager
def temporary_bridge():
    previous_status = get_bridge_status()
    started_here = False
    if not previous_status.running:
        ensure_bridge_running()
        started_here = True
    try:
        yield get_bridge_status()
    finally:
        if started_here:
            stop_bridge()


def start_bridge() -> BridgeStatus:
    status = get_bridge_status()
    if status.running:
        return status

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    log_handle = open(LOG_FILE, "a", encoding="utf-8")
    env = os.environ.copy()
    env.setdefault("FREU_BRIDGE_PID_FILE", str(PID_FILE))
    env.setdefault("FREU_BRIDGE_PORT", str(DEFAULT_BRIDGE_PORT))
    env.setdefault("FREU_BRIDGE_HOST", DEFAULT_BRIDGE_HOST)
    env.setdefault("FREU_BRIDGE_IDLE_TIMEOUT_SECONDS", str(DEFAULT_IDLE_TIMEOUT_SECONDS))
    try:
        subprocess.Popen(
            [
                sys.executable, "-m", "freu_cli.capture.bridge", "serve",
                "--host", DEFAULT_BRIDGE_HOST,
                "--port", str(DEFAULT_BRIDGE_PORT),
                "--idle-timeout-seconds", str(DEFAULT_IDLE_TIMEOUT_SECONDS),
                "--pid-file", str(PID_FILE),
            ],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
            close_fds=True,
        )
    finally:
        log_handle.close()
    return BridgeStatus(running=False, base_url=bridge_base_url(), pid=None)


def stop_bridge() -> bool:
    pid = _read_pid_file()
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _remove_pid_file()
        return False
    deadline = time.time() + 5
    while time.time() < deadline:
        if not _process_exists(pid):
            _remove_pid_file()
            return True
        time.sleep(0.2)
    return False


def _read_pid_file() -> int | None:
    try:
        raw = PID_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _remove_pid_file() -> None:
    try:
        PID_FILE.unlink()
    except OSError:
        return


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True

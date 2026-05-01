"""HTTP bridge — the endpoint the freu Chrome extension long-polls.

Two jobs:

1. **Command broker** — `/bridge/next-command`, `/bridge/command-result`,
   `/bridge/cdp`, `/bridge/navigate`, `/bridge/screenshot`. These let
   `freu-cli run` drive the browser by queueing Chrome Debugger commands for
   the extension to execute.

2. **Capture-event ingestion** — `/bridge/capture-event`. When a capture
   session is active, the extension POSTs every user action here. We build an
   `events.json` record and append it to the session's event list (either via
   a registered sink for in-process capture, or dropped when no session is
   active).

Run directly with `python -m freu_cli.capture.bridge serve --pid-file <path>`.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import signal
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_IDLE_TIMEOUT_SECONDS = 300
COMMAND_TIMEOUT_SECONDS = 30.0
EXTENSION_CONNECTION_TTL_SECONDS = 35.0


CaptureSink = Callable[[dict[str, Any]], None]


class CommandBroker:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._queue: deque[dict[str, Any]] = deque()
        self._results: dict[str, dict[str, Any]] = {}
        self._next_command_id = 1

    def enqueue(self, command_type: str, payload: dict[str, Any]) -> str:
        with self._condition:
            command_id = str(self._next_command_id)
            self._next_command_id += 1
            self._queue.append(
                {"id": command_id, "type": command_type, "payload": payload}
            )
            self._condition.notify_all()
            return command_id

    def next_command(self, timeout_seconds: float) -> dict[str, Any] | None:
        deadline = time.time() + timeout_seconds
        with self._condition:
            while not self._queue:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)
            return self._queue.popleft()

    def submit_result(self, command_id: str, result: dict[str, Any]) -> None:
        with self._condition:
            self._results[command_id] = result
            self._condition.notify_all()

    def wait_for_result(self, command_id: str, timeout_seconds: float) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        with self._condition:
            while command_id not in self._results:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Timed out waiting for browser command {command_id}"
                    )
                self._condition.wait(timeout=remaining)
            return self._results.pop(command_id)


@dataclass
class BridgeState:
    idle_timeout_seconds: int
    broker: CommandBroker = field(default_factory=CommandBroker)
    lock: threading.Lock = field(default_factory=threading.Lock)
    extension_last_poll_at: float | None = None
    last_activity_at: float = field(default_factory=time.time)
    capture_sink: CaptureSink | None = None

    def mark_extension_poll(self) -> None:
        with self.lock:
            self.extension_last_poll_at = time.time()

    def mark_activity(self) -> None:
        with self.lock:
            self.last_activity_at = time.time()

    def extension_connected(self) -> bool:
        with self.lock:
            last_poll_at = self.extension_last_poll_at
        return (
            last_poll_at is not None
            and (time.time() - last_poll_at) <= EXTENSION_CONNECTION_TTL_SECONDS
        )

    def last_poll_iso(self) -> str | None:
        with self.lock:
            last_poll_at = self.extension_last_poll_at
        if last_poll_at is None:
            return None
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last_poll_at))

    def last_activity_iso(self) -> str:
        with self.lock:
            last_activity_at = self.last_activity_at
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last_activity_at))

    def idle_seconds(self) -> float:
        with self.lock:
            return max(0.0, time.time() - self.last_activity_at)

    def set_capture_sink(self, sink: CaptureSink | None) -> None:
        with self.lock:
            self.capture_sink = sink

    def get_capture_sink(self) -> CaptureSink | None:
        with self.lock:
            return self.capture_sink


class BridgeRequestHandler(BaseHTTPRequestHandler):
    server_version = "FreuCliBridge/1.0"

    @property
    def state(self) -> BridgeState:
        return self.server.bridge_state  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/bridge/healthz":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "service": "freu-cli-bridge",
                        "idle_timeout_seconds": self.state.idle_timeout_seconds,
                    },
                )
                return
            if parsed.path == "/bridge/status":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "extension_connected": self.state.extension_connected(),
                        "last_poll_at": self.state.last_poll_iso(),
                        "last_activity_at": self.state.last_activity_iso(),
                        "idle_seconds": round(self.state.idle_seconds(), 3),
                        "idle_timeout_seconds": self.state.idle_timeout_seconds,
                        "command_timeout_seconds": COMMAND_TIMEOUT_SECONDS,
                        "capturing": self.state.get_capture_sink() is not None,
                    },
                )
                return
            if parsed.path == "/bridge/page":
                self.state.mark_activity()
                self._send_json(HTTPStatus.OK, _execute_command(self.state, "page-info", {}))
                return
            if parsed.path == "/bridge/next-command":
                self.state.mark_extension_poll()
                timeout = _read_timeout_seconds(
                    parse_qs(parsed.query).get("timeout", ["25"])[0], default=25.0,
                )
                command = self.state.broker.next_command(timeout)
                if command is None:
                    self._send_json(HTTPStatus.NO_CONTENT, {})
                    return
                self._send_json(HTTPStatus.OK, command)
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
        except Exception as exc:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)}
            )

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        payload = _read_json_body(self)
        try:
            if parsed.path == "/bridge/navigate":
                self.state.mark_activity()
                command_id = self.state.broker.enqueue("navigate-url", payload)
                self._send_json(
                    HTTPStatus.ACCEPTED, {"ok": True, "queued": True, "id": command_id}
                )
                return
            if parsed.path == "/bridge/screenshot":
                self.state.mark_activity()
                self._send_json(*_handle_screenshot(self.state, payload))
                return
            if parsed.path == "/bridge/cdp":
                self.state.mark_activity()
                method = str(payload.get("method", "")).strip()
                if not method:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "Missing CDP method"},
                    )
                    return
                result = _execute_command(
                    self.state, "cdp-command",
                    {"method": method, "params": payload.get("params", {})},
                )
                self._send_json(HTTPStatus.OK, result)
                return
            if parsed.path == "/bridge/command-result":
                command_id = str(payload.get("id", "")).strip()
                if not command_id:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "Missing command id"},
                    )
                    return
                self.state.broker.submit_result(command_id, payload)
                self._send_json(HTTPStatus.OK, {"ok": True})
                return
            if parsed.path == "/bridge/capture-event":
                self.state.mark_activity()
                sink = self.state.get_capture_sink()
                if sink is None:
                    self._send_json(HTTPStatus.OK, {"ok": True, "recording": False})
                    return
                try:
                    sink(payload)
                except Exception as exc:  # noqa: BLE001
                    self._send_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"ok": False, "error": f"capture sink failed: {exc}"},
                    )
                    return
                self._send_json(HTTPStatus.OK, {"ok": True, "recording": True})
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
        except Exception as exc:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)}
            )

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    raw_length = handler.headers.get("Content-Length", "0")
    try:
        content_length = max(0, int(raw_length))
    except ValueError:
        content_length = 0
    raw = handler.rfile.read(content_length) if content_length else b""
    if not raw:
        return {}
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    return payload


def _read_timeout_seconds(raw_value: str, default: float) -> float:
    try:
        return max(0.1, float(raw_value))
    except ValueError:
        return default


def _execute_command(
    state: BridgeState, command_type: str, payload: dict[str, Any],
) -> dict[str, Any]:
    command_id = state.broker.enqueue(command_type, payload)
    result = state.broker.wait_for_result(command_id, COMMAND_TIMEOUT_SECONDS)
    if not result.get("ok", False):
        message = str(result.get("error") or f"Browser command failed: {command_type}")
        raise RuntimeError(message)
    return result


def _handle_screenshot(
    state: BridgeState, payload: dict[str, Any],
) -> tuple[HTTPStatus, dict[str, Any]]:
    result = _execute_command(
        state, "cdp-command",
        {"method": "Page.captureScreenshot", "params": {"format": "png"}},
    )
    path = str(payload.get("path", "")).strip()
    if not path:
        return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Missing screenshot path"}
    cdp_result = result.get("result", {})
    b64_data = str(cdp_result.get("data", "")).strip()
    if not b64_data:
        return HTTPStatus.BAD_GATEWAY, {"ok": False, "error": "Invalid screenshot payload"}
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(base64.b64decode(b64_data))
    return HTTPStatus.OK, {"ok": True, "path": str(target)}


@dataclass(slots=True)
class BridgeServerHandle:
    server: ThreadingHTTPServer
    state: BridgeState
    thread: threading.Thread
    pid_file: Path | None

    @property
    def host(self) -> str:
        return self.server.server_address[0]

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        if self.pid_file is not None and self.pid_file.exists():
            try:
                self.pid_file.unlink()
            except OSError:
                pass


def start_bridge_server(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS,
    pid_file: Path | None = None,
    capture_sink: CaptureSink | None = None,
) -> BridgeServerHandle:
    """Start the bridge in a background thread. Returns a handle for shutdown.

    Used by `record_capture()` to run an in-process bridge with a live capture
    sink; and by the `python -m freu_cli.capture.bridge serve` entry point to
    run a standalone bridge for `freu-cli run`.
    """
    state = BridgeState(idle_timeout_seconds=idle_timeout_seconds)
    if capture_sink is not None:
        state.set_capture_sink(capture_sink)

    server = ThreadingHTTPServer((host, port), BridgeRequestHandler)
    server.bridge_state = state  # type: ignore[attr-defined]

    if pid_file is not None:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()), encoding="utf-8")

    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5},
                              daemon=True)
    thread.start()
    return BridgeServerHandle(server=server, state=state, thread=thread, pid_file=pid_file)


def serve_bridge(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS,
    pid_file: Path | None = None,
) -> int:
    """Run the bridge in the foreground until SIGINT/SIGTERM or idle timeout."""
    handle = start_bridge_server(
        host=host, port=port,
        idle_timeout_seconds=idle_timeout_seconds,
        pid_file=pid_file,
    )
    shutdown_event = threading.Event()

    def handle_signal(signum: int, _frame: object) -> None:
        shutdown_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        while not shutdown_event.is_set():
            if handle.state.idle_seconds() >= handle.state.idle_timeout_seconds:
                break
            time.sleep(1.0)
    finally:
        handle.shutdown()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m freu_cli.capture.bridge")
    parser.add_argument("command", choices=["serve"])
    parser.add_argument("--host", default=os.getenv("FREU_BRIDGE_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--port", type=int,
        default=int(os.getenv("FREU_BRIDGE_PORT", str(DEFAULT_PORT))),
    )
    parser.add_argument(
        "--idle-timeout-seconds", type=int,
        default=int(os.getenv("FREU_BRIDGE_IDLE_TIMEOUT_SECONDS", str(DEFAULT_IDLE_TIMEOUT_SECONDS))),
    )
    parser.add_argument("--pid-file", default=os.getenv("FREU_BRIDGE_PID_FILE", ""))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    pid_file = Path(args.pid_file).expanduser() if str(args.pid_file).strip() else None
    return serve_bridge(
        host=args.host, port=args.port,
        idle_timeout_seconds=args.idle_timeout_seconds,
        pid_file=pid_file,
    )


if __name__ == "__main__":
    raise SystemExit(main())

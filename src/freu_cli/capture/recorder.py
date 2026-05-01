"""`freu-cli capture <path>` recorder.

Starts an in-process bridge with an active capture sink, waits until the user
hits Ctrl-C (or the optional `stop_event` fires), then writes the collected
events to `<path>/events.json`.

No intermediate files (no `events_dom.json`, no screenshots, no mouse.json) —
just one `events.json` with a JSON array of DOM event records.
"""

from __future__ import annotations

import json
import signal
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from freu_cli.capture.bridge import (
    DEFAULT_HOST,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_PORT,
    BridgeServerHandle,
    start_bridge_server,
)
from freu_cli.capture.event_record import build_event_record


class CaptureSession:
    """Collect event records in memory; flush a JSON array on stop()."""

    SNAPSHOTS_DIRNAME = "snapshots"

    def __init__(self, output_dir: Path, session_id: str | None = None) -> None:
        self.output_dir = output_dir
        self.session_id = session_id or output_dir.name or str(uuid.uuid4())
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._snapshot_seq: dict[int, int] = {}
        self._event_seq = 0

    def sink(self, payload: dict[str, Any]) -> None:
        ts_ms = payload.get("timestamp")
        if not isinstance(ts_ms, int):
            ts_ms = int(datetime.now().timestamp() * 1000)
        # Pull the snapshot HTML out of the event payload BEFORE building the
        # record. We persist it to a sibling `snapshots/<ts>.html` file and
        # leave only a relative path reference in the event record so
        # events.json stays small and human-scannable.
        event = payload.get("event")
        if isinstance(event, dict):
            html = event.pop("snapshot_html", None)
            if isinstance(html, str) and html:
                snapshot_ref = self._write_snapshot(ts_ms, html)
                if snapshot_ref:
                    event["snapshot"] = snapshot_ref
        with self._lock:
            self._event_seq += 1
            event_id = f"e{self._event_seq}"
        record = build_event_record(
            ts_ms, self.session_id, payload, event_id=event_id,
        )
        with self._lock:
            self._events.append(record)

    def _write_snapshot(self, ts_ms: int, html: str) -> str | None:
        """Persist `html` under `snapshots/<ts_ms>[-N].html` and return the
        relative path. Disambiguates if two clicks share a millisecond
        boundary by appending an incrementing suffix.
        """
        try:
            snapshots_dir = self.output_dir / self.SNAPSHOTS_DIRNAME
            snapshots_dir.mkdir(parents=True, exist_ok=True)
            with self._lock:
                seq = self._snapshot_seq.get(ts_ms, 0)
                self._snapshot_seq[ts_ms] = seq + 1
            name = f"{ts_ms}.html" if seq == 0 else f"{ts_ms}-{seq}.html"
            (snapshots_dir / name).write_text(html, encoding="utf-8")
            return f"{self.SNAPSHOTS_DIRNAME}/{name}"
        except OSError:
            return None

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def flush(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.output_dir / "events.json"
        with self._lock:
            out_path.write_text(
                json.dumps(self._events, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        return out_path


def record_capture(
    output_dir: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS,
    stop_event: threading.Event | None = None,
    on_ready: Callable[[BridgeServerHandle], None] | None = None,
) -> Path:
    """Run a capture session, blocking until stop_event is set or SIGINT.

    Returns the path to the written events.json. When called from the CLI the
    default `stop_event` is None — the function installs a SIGINT handler and
    blocks until Ctrl-C. In tests, pass a `stop_event` to drive shutdown
    deterministically.
    """
    output_dir = Path(output_dir).expanduser().resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(
            f"Capture path already exists and is not empty: {output_dir}. "
            "Pick a fresh path."
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    session = CaptureSession(output_dir)
    handle = start_bridge_server(
        host=host, port=port,
        idle_timeout_seconds=idle_timeout_seconds,
        capture_sink=session.sink,
    )

    local_stop = stop_event or threading.Event()
    prev_sigint = None
    prev_sigterm = None
    installed_signals = stop_event is None
    if installed_signals:
        def _on_signal(_signum: int, _frame: object) -> None:
            local_stop.set()

        prev_sigint = signal.signal(signal.SIGINT, _on_signal)
        prev_sigterm = signal.signal(signal.SIGTERM, _on_signal)

    try:
        if on_ready is not None:
            on_ready(handle)
        while not local_stop.is_set():
            time.sleep(0.2)
    finally:
        handle.state.set_capture_sink(None)
        handle.shutdown()
        if installed_signals:
            if prev_sigint is not None:
                signal.signal(signal.SIGINT, prev_sigint)
            if prev_sigterm is not None:
                signal.signal(signal.SIGTERM, prev_sigterm)

    return session.flush()

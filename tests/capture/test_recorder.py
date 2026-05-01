from __future__ import annotations

import http.client
import json
import socket
import threading
from contextlib import closing
from pathlib import Path

from freu_cli.capture.recorder import CaptureSession, record_capture


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_record_capture_writes_events_json(tmp_path: Path):
    """End-to-end: start recorder, POST an event, stop, assert events.json."""
    out_dir = tmp_path / "capture-1"
    port = _free_port()
    stop_event = threading.Event()
    ready = threading.Event()

    captured_path: list[Path] = []

    def _runner():
        path = record_capture(
            out_dir,
            host="127.0.0.1",
            port=port,
            stop_event=stop_event,
            on_ready=lambda _handle: ready.set(),
        )
        captured_path.append(path)

    thread = threading.Thread(target=_runner)
    thread.start()
    try:
        assert ready.wait(timeout=5), "record_capture never became ready"

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST", "/bridge/capture-event",
            body=json.dumps({
                "event": {
                    "type": "click",
                    "selector": "#go",
                    "ancestors": [{"tag": "button"}],
                },
                "url": "https://demo",
                "timestamp": 42,
            }),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        assert response.status == 200
        response.read()
        conn.close()

        stop_event.set()
    finally:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert captured_path, "recorder did not return the events.json path"
    events_path = captured_path[0]
    assert events_path == out_dir / "events.json"
    payload = json.loads(events_path.read_text())
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["type"] == "click"
    assert payload[0]["selector"] == "#go"
    assert payload[0]["ts"] == 42


def test_record_capture_refuses_nonempty_dir(tmp_path: Path):
    (tmp_path / "stuff.txt").write_text("hi")
    try:
        record_capture(tmp_path, stop_event=threading.Event())
    except FileExistsError as exc:
        assert "not empty" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected FileExistsError")


def test_capture_session_writes_snapshot_html_for_clicks(tmp_path: Path):
    session = CaptureSession(tmp_path)
    html = "<html><body><button>Star</button></body></html>"
    session.sink({
        "event": {
            "type": "click",
            "selector": {"tag": "button", "text": "Star"},
            "ancestors": [{"tag": "body"}],
            "snapshot_html": html,
        },
        "url": "https://demo",
        "timestamp": 12345,
    })
    snapshot_file = tmp_path / "snapshots" / "12345.html"
    assert snapshot_file.exists()
    assert snapshot_file.read_text() == html
    record = session.snapshot()[0]
    assert record["snapshot"] == "snapshots/12345.html"


def test_capture_session_omits_snapshot_field_when_no_html(tmp_path: Path):
    session = CaptureSession(tmp_path)
    session.sink({
        "event": {
            "type": "click",
            "selector": {"tag": "button"},
            "ancestors": [{"tag": "body"}],
        },
        "url": "https://demo",
        "timestamp": 1,
    })
    record = session.snapshot()[0]
    assert "snapshot" not in record
    assert not (tmp_path / "snapshots").exists()


def test_capture_session_disambiguates_snapshots_with_same_timestamp(tmp_path: Path):
    session = CaptureSession(tmp_path)
    for _ in range(3):
        session.sink({
            "event": {
                "type": "click",
                "selector": {"tag": "button"},
                "ancestors": [{"tag": "body"}],
                "snapshot_html": "<html></html>",
            },
            "timestamp": 99,
        })
    refs = [e["snapshot"] for e in session.snapshot()]
    assert refs == [
        "snapshots/99.html",
        "snapshots/99-1.html",
        "snapshots/99-2.html",
    ]
    for ref in refs:
        assert (tmp_path / ref).exists()

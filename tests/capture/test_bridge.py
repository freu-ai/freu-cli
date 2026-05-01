"""Integration tests for the in-process bridge HTTP server.

We pick a free ephemeral port and hit real endpoints via http.client, to
cover request parsing, capture-sink dispatch, and server shutdown.
"""

from __future__ import annotations

import http.client
import json
import socket
from contextlib import closing

import pytest

from freu_cli.capture.bridge import start_bridge_server


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get_json(host: str, port: int, path: str) -> tuple[int, dict]:
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        body = response.read().decode("utf-8")
        return response.status, json.loads(body) if body else {}
    finally:
        conn.close()


def _post_json(host: str, port: int, path: str, payload: dict) -> tuple[int, dict]:
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request(
            "POST", path, body=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        body = response.read().decode("utf-8")
        return response.status, json.loads(body) if body else {}
    finally:
        conn.close()


@pytest.fixture
def bridge():
    port = _free_port()
    sink_events: list[dict] = []
    handle = start_bridge_server(
        host="127.0.0.1", port=port,
        capture_sink=lambda payload: sink_events.append(payload),
    )
    try:
        yield handle, port, sink_events
    finally:
        handle.shutdown()


def test_healthz_returns_ok(bridge):
    _, port, _ = bridge
    status_code, body = _get_json("127.0.0.1", port, "/bridge/healthz")
    assert status_code == 200
    assert body["ok"] is True


def test_status_reports_extension_not_connected_initially(bridge):
    _, port, _ = bridge
    status_code, body = _get_json("127.0.0.1", port, "/bridge/status")
    assert status_code == 200
    assert body["extension_connected"] is False
    assert body["capturing"] is True


def test_capture_event_dispatches_to_sink(bridge):
    _, port, sink_events = bridge
    payload = {
        "event": {"type": "click", "selector": "#foo"},
        "url": "https://example.com",
        "timestamp": 123,
    }
    status_code, body = _post_json("127.0.0.1", port, "/bridge/capture-event", payload)
    assert status_code == 200
    assert body == {"ok": True, "recording": True}
    assert sink_events == [payload]


def test_next_command_marks_extension_connected(bridge):
    handle, port, _ = bridge
    # Long-poll returns 204 No Content when no commands queued.
    status_code, _ = _get_json(
        "127.0.0.1", port, "/bridge/next-command?timeout=0.2",
    )
    assert status_code == 204
    status_code, body = _get_json("127.0.0.1", port, "/bridge/status")
    assert status_code == 200
    assert body["extension_connected"] is True


def test_unknown_path_returns_404(bridge):
    _, port, _ = bridge
    status_code, body = _get_json("127.0.0.1", port, "/bridge/does-not-exist")
    assert status_code == 404
    assert body["ok"] is False


def test_capture_event_returns_recording_false_when_sink_unset(bridge):
    handle, port, _ = bridge
    handle.state.set_capture_sink(None)
    status_code, body = _post_json(
        "127.0.0.1", port, "/bridge/capture-event",
        {"event": {"type": "click"}},
    )
    assert status_code == 200
    assert body == {"ok": True, "recording": False}

"""Shape DOM-event payloads posted by the Chrome extension into records.

The extension posts `{event: {...}, tabId, frameId, url, tabUrl, timestamp}`.
We distill that into a stable record schema that `freu-cli learn` consumes.
"""

from __future__ import annotations

import uuid
from typing import Any


def build_event_record(
    ts_ms: int,
    session_id: str,
    payload: dict[str, Any],
    *,
    event_id: str | None = None,
) -> dict[str, Any]:
    event = payload.get("event", {}) or {}
    event_type = event.get("type", "unknown")
    url = payload.get("url") or payload.get("tabUrl") or ""

    record: dict[str, Any] = {
        "event_id": event_id or str(uuid.uuid4()),
        "source": "dom",
        "session_id": session_id,
        "ts": ts_ms,
        "type": event_type,
        "url": url,
        "tab_id": payload.get("tabId"),
        "frame_id": payload.get("frameId"),
    }

    if event_type in ("click", "input", "keydown", "submit"):
        record["selector"] = event.get("selector", "?")
        if event.get("ancestors"):
            record["ancestors"] = event["ancestors"]
        if event.get("neighbors"):
            record["neighbors"] = event["neighbors"]
        if event.get("children") is not None:
            record["children"] = event["children"]
        if event.get("special"):
            record["special"] = event["special"]
        if event.get("snapshot"):
            record["snapshot"] = event["snapshot"]
        if event_type == "click":
            record["button"] = event.get("button") or "left"
        elif event_type == "input":
            record["value"] = event.get("value", "")
        elif event_type == "keydown":
            mods = ""
            for m in ("ctrl", "shift", "alt", "meta"):
                if event.get(m):
                    mods += m + "+"
            record["key"] = mods + event.get("key", "?")
    elif event_type.startswith("tab_"):
        record["tab_id"] = event.get("tabId")
        record["url"] = event.get("url", "")
        record["title"] = event.get("title", "")
    elif event_type == "page_loaded":
        record["url"] = event.get("url", url)
        record["title"] = event.get("title", "")
        # The settled page_loaded fired by the content script after
        # `load` carries a snapshot of the fully rendered DOM. Preserve
        # the relative path the recorder substitutes for the raw HTML.
        if event.get("snapshot"):
            record["snapshot"] = event["snapshot"]

    return record

from freu_cli.capture.event_record import build_event_record


def test_click_event_record_has_selector_and_ancestors():
    payload = {
        "event": {
            "type": "click",
            "selector": {"tag": "button", "text": "Click me", "x": 10, "y": 20},
            "button": "left",
            "ancestors": [{"tag": "button", "text": "Click me", "x": 10, "y": 20}],
        },
        "url": "https://example.com",
        "tabId": 1,
        "frameId": 0,
    }
    record = build_event_record(12345, "session-a", payload)
    assert record["type"] == "click"
    assert record["selector"] == {"tag": "button", "text": "Click me", "x": 10, "y": 20}
    assert record["button"] == "left"
    assert "pos" not in record
    assert "text" not in record
    assert record["ancestors"] == [{"tag": "button", "text": "Click me", "x": 10, "y": 20}]
    assert record["url"] == "https://example.com"
    assert record["session_id"] == "session-a"
    assert record["ts"] == 12345
    assert "event_id" in record and record["event_id"]


def test_input_event_record_captures_value():
    payload = {"event": {"type": "input", "selector": "input#q", "value": "hello"}}
    record = build_event_record(100, "s", payload)
    assert record["value"] == "hello"


def test_keydown_event_normalizes_modifier_prefix():
    payload = {
        "event": {
            "type": "keydown", "selector": "input#q", "key": "Enter",
            "ctrl": False, "shift": True, "alt": False, "meta": False,
        }
    }
    record = build_event_record(100, "s", payload)
    assert record["key"] == "shift+Enter"


def test_page_loaded_event_has_url_and_title():
    payload = {
        "event": {"type": "page_loaded", "url": "https://x", "title": "X"},
        "url": "https://x",
    }
    record = build_event_record(100, "s", payload)
    assert record["type"] == "page_loaded"
    assert record["url"] == "https://x"
    assert record["title"] == "X"


def test_click_event_record_passes_through_neighbors_children_special():
    payload = {
        "event": {
            "type": "click",
            "selector": {"tag": "button", "text": "Star"},
            "ancestors": [{"tag": "html"}],
            "neighbors": [{"tag": "span", "text": "1234"}],
            "children": [{"tag": "svg"}],
            "special": {"role": "label", "tag": "label", "text": "Star this repo"},
            "button": "left",
        },
    }
    record = build_event_record(1, "s", payload)
    assert record["neighbors"] == [{"tag": "span", "text": "1234"}]
    assert record["children"] == [{"tag": "svg"}]
    assert record["special"] == {"role": "label", "tag": "label", "text": "Star this repo"}


def test_click_event_record_preserves_empty_children_list():
    """An empty children list is a meaningful signal (target has no children)."""
    payload = {
        "event": {
            "type": "click",
            "selector": {"tag": "input"},
            "ancestors": [{"tag": "body"}],
            "children": [],
        },
    }
    record = build_event_record(1, "s", payload)
    assert record["children"] == []


def test_click_event_record_omits_children_when_null():
    """The extension sends children=null when the target has >20 children;
    the passthrough should drop the key entirely so downstream code can
    distinguish 'no children' ([]) from 'too many to record' (missing)."""
    payload = {
        "event": {
            "type": "click",
            "selector": {"tag": "div"},
            "ancestors": [{"tag": "body"}],
            "children": None,
        },
    }
    record = build_event_record(1, "s", payload)
    assert "children" not in record

import json

from freu_cli.learn.models import RawEvent
from freu_cli.learn.stages.normalize import normalize_events


def _raw(events: list[dict]) -> list[RawEvent]:
    return [RawEvent.model_validate(e) for e in events]


def test_normalize_returns_parsed_actions(fake_llm, github_star_events):
    raw = _raw(github_star_events)
    canned = json.dumps([
        {
            "action": "click_element", "event_ids": ["e2"],
            "target": "search input", "description": "focus search",
        },
        {
            "action": "type_text", "event_ids": ["e3"],
            "target": "search input", "description": "type repo name",
            "value": "anthropics/courses",
        },
        {
            "action": "press_key", "event_ids": ["e4"],
            "target": "search input", "description": "submit search",
            "key": "Enter",
        },
        {
            "action": "click_element", "event_ids": ["e6"],
            "target": "Star button", "description": "star the repo",
        },
    ])
    llm = fake_llm([canned])

    result = normalize_events(raw, "star a github repo", llm)
    assert [e.action for e in result] == [
        "click_element", "type_text", "press_key", "click_element",
    ]
    # Hydration: ancestors + url_after pulled from raw events
    assert result[0].target_ancestors is not None
    assert result[0].target_ancestors[-1]["attrs"]["name"] == "q"
    assert result[1].value == "anthropics/courses"
    assert result[2].key == "Enter"
    assert result[3].target_ancestors[-1]["attrs"]["data-action"] == "star"
    # The new target-context fields are hydrated for the star click (e6).
    assert result[3].target_self is not None
    assert result[3].target_self["tag"] == "button"
    assert result[3].target_neighbors == [
        {"tag": "span", "text": "1,234"},
        {"tag": "button", "text": "Watch"},
    ]
    assert result[3].target_children == [
        {"tag": "svg", "attrs": {"aria-label": "star"}},
        {"tag": "span", "text": "Star"},
    ]


def test_normalize_rejects_non_array_output(fake_llm, github_star_events):
    from freu_cli.learn.errors import LLMResponseError
    llm = fake_llm([json.dumps({"action": "click_element", "event_ids": ["e1"]})])
    try:
        normalize_events(_raw(github_star_events), "obj", llm)
    except LLMResponseError as exc:
        assert "array" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected LLMResponseError")

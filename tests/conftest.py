"""Shared pytest fixtures.

Provides:
  - `fake_llm` — a factory for LLMClient whose .call is driven by a list of
    canned JSON strings. Tests pass in what the LLM should return; the fake
    panics if asked to make more calls than planned.
  - `github_star_events` — a synthetic RawEvent list mimicking a star flow.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from freu_cli.learn.llm_client import LLMClient


@pytest.fixture
def fake_llm():
    def _factory(responses: Iterable[str]) -> LLMClient:
        buffer = list(responses)

        def _call(_system_prompt: str, _user_prompt: str) -> str:
            if not buffer:
                raise AssertionError(
                    "fake_llm: call made after canned responses exhausted"
                )
            return buffer.pop(0)

        return LLMClient(model="test-model", _call=_call)

    return _factory


@pytest.fixture
def github_star_events() -> list[dict]:
    """Small synthetic events.json payload mimicking a GitHub 'star' session."""
    return [
        {
            "event_id": "e1",
            "source": "dom",
            "session_id": "s",
            "ts": 1000,
            "type": "page_loaded",
            "url": "https://github.com/",
            "title": "GitHub",
        },
        {
            "event_id": "e2",
            "source": "dom",
            "session_id": "s",
            "ts": 2000,
            "type": "click",
            "url": "https://github.com/",
            "selector": 'input[name="q"]',
            "ancestors": [
                {"tag": "html"},
                {"tag": "body"},
                {"tag": "header"},
                {"tag": "form", "attrs": {"role": "search"}},
                {"tag": "input", "attrs": {"name": "q", "type": "search"}},
            ],
            "button": "left",
        },
        {
            "event_id": "e3",
            "source": "dom",
            "session_id": "s",
            "ts": 2500,
            "type": "input",
            "url": "https://github.com/",
            "selector": 'input[name="q"]',
            "ancestors": [
                {"tag": "form", "attrs": {"role": "search"}},
                {"tag": "input", "attrs": {"name": "q"}},
            ],
            "value": "anthropics/courses",
        },
        {
            "event_id": "e4",
            "source": "dom",
            "session_id": "s",
            "ts": 3000,
            "type": "keydown",
            "url": "https://github.com/",
            "selector": 'input[name="q"]',
            "ancestors": [{"tag": "input", "attrs": {"name": "q"}}],
            "key": "Enter",
        },
        {
            "event_id": "e5",
            "source": "dom",
            "session_id": "s",
            "ts": 4000,
            "type": "page_loaded",
            "url": "https://github.com/anthropics/courses",
            "title": "anthropics/courses",
        },
        {
            "event_id": "e6",
            "source": "dom",
            "session_id": "s",
            "ts": 5000,
            "type": "click",
            "url": "https://github.com/anthropics/courses",
            "selector": {
                "tag": "button",
                "id": None,
                "classes": ["btn-sm", "css-1abc23"],
                "attrs": {"data-action": "star", "aria-label": "Star"},
                "text": "Star",
                "x": 850, "y": 95, "w": 60, "h": 28,
                "x_rel": 0.664, "w_rel": 0.047,
            },
            "ancestors": [
                {"tag": "html"},
                {"tag": "body"},
                {"tag": "main"},
                {"tag": "ul", "classes": ["pagehead-actions"]},
                {"tag": "li"},
                {"tag": "button", "attrs": {"data-action": "star", "aria-label": "Star"}, "text": "Star"},
            ],
            "neighbors": [
                {"tag": "span", "text": "1,234"},
                {"tag": "button", "text": "Watch"},
            ],
            "children": [
                {"tag": "svg", "attrs": {"aria-label": "star"}},
                {"tag": "span", "text": "Star"},
            ],
            "button": "left",
        },
    ]

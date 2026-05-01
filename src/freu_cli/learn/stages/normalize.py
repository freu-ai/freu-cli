from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from freu_cli.learn.errors import LLMResponseError
from freu_cli.learn.llm_client import LLMClient
from freu_cli.learn.models import NormalizedEvent, RawEvent
from freu_cli.learn.stages._prompts import load_prompt

_SYSTEM_PROMPT = load_prompt("normalize.txt")


def _compact_raw_event(event: RawEvent) -> dict[str, Any]:
    """Strip noisy fields before sending to the LLM to keep prompts short."""
    data = event.model_dump()
    keep = {
        "event_id", "type", "ts", "url", "selector", "value",
        "key", "button", "title",
    }
    return {k: v for k, v in data.items() if k in keep and v is not None}


def build_user_prompt(raw_events: list[RawEvent], objective: str) -> str:
    compact = [_compact_raw_event(e) for e in raw_events]
    lines = [
        f"Learning objective: {objective.strip() or '(none — infer from events)'}",
        "",
        "Raw events:",
        json.dumps(compact, indent=2, ensure_ascii=False),
    ]
    return "\n".join(lines)


def normalize_events(
    raw_events: list[RawEvent], objective: str, llm: LLMClient,
) -> list[NormalizedEvent]:
    user_prompt = build_user_prompt(raw_events, objective)
    payload = llm.call_json(_SYSTEM_PROMPT, user_prompt)
    if not isinstance(payload, list):
        raise LLMResponseError(
            f"normalize stage expected a JSON array, got {type(payload).__name__}"
        )
    normalized: list[NormalizedEvent] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise LLMResponseError(
                f"normalize entry {index} must be an object, got {type(item).__name__}"
            )
        try:
            normalized.append(NormalizedEvent.model_validate(item))
        except PydanticValidationError as exc:
            raise LLMResponseError(
                f"normalize entry {index} is invalid: {exc}"
            ) from exc

    _hydrate_from_raw(normalized, raw_events)
    return normalized


def _hydrate_from_raw(
    normalized: list[NormalizedEvent], raw_events: list[RawEvent],
) -> None:
    """Fill in fields the LLM omits so the resolve stage sees full context.

    We look up each normalized entry's first `event_id` in the raw event
    list and copy: target_self, target_ancestors, target_neighbors,
    target_children, target_special, url_before/after, and (if missing)
    the typed value / pressed key.
    """
    raw_by_id: dict[str, RawEvent] = {}
    for raw in raw_events:
        if raw.event_id:
            raw_by_id[raw.event_id] = raw

    prev_url: str | None = None
    for entry in normalized:
        source_raw = None
        for eid in entry.event_ids:
            if eid in raw_by_id:
                source_raw = raw_by_id[eid]
                break
        if source_raw is None:
            continue
        extras = source_raw.model_extra or {}
        selector = extras.get("selector")
        if isinstance(selector, dict):
            entry.target_self = selector
        ancestors = extras.get("ancestors")
        if isinstance(ancestors, list):
            entry.target_ancestors = ancestors
        neighbors = extras.get("neighbors")
        if isinstance(neighbors, list):
            entry.target_neighbors = neighbors
        # `children` is tri-state: a list (possibly empty) means the target
        # had ≤ 20 children; missing means the extension dropped them
        # because the target had too many siblings to bother recording.
        if "children" in extras and isinstance(extras["children"], list):
            entry.target_children = extras["children"]
        special = extras.get("special")
        if isinstance(special, dict):
            entry.target_special = special
        url = extras.get("url")
        if isinstance(url, str) and url:
            entry.url_before = prev_url
            entry.url_after = url
            prev_url = url
        if entry.action == "type_text" and entry.value is None:
            val = extras.get("value")
            if isinstance(val, str):
                entry.value = val
        if entry.action == "press_key" and entry.key is None:
            key = extras.get("key")
            if isinstance(key, str):
                entry.key = key

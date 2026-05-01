"""Identify stage: detect retrieval-style objectives and locate the
value-bearing element(s) on the final page snapshot.

When the learning objective asks for a value back ("find …", "get …",
"look up …"), this stage reads the last DOM snapshot and picks the
element that carries that value. Its output (`RetrievalPlan`) is fed
to synthesize, which appends a read step and declares command outputs.

The stage is best-effort: any LLM error short-circuits to an empty
plan rather than failing the whole pipeline. A retrieval-style command
that ends up with no outputs is a recoverable miss; a hard pipeline
crash here is not.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from freu_cli.learn.errors import LLMResponseError
from freu_cli.learn.llm_client import LLMClient
from freu_cli.learn.models import Constellation, ResolvedEvent, RetrievalPlan
from freu_cli.learn.stages._prompts import load_prompt
from freu_cli.learn.stages.resolve import prune_graph

ProgressCallback = Callable[[str], None]

_SYSTEM_PROMPT = load_prompt("identify.txt")

_SNAPSHOT_BUDGET_BYTES = 120_000

_TEXT_PREVIEW_LIMIT = 40

# Verbs that signal a retrieval-style objective. We use these as a
# deterministic guard so the LLM can't talk itself out of a clearly
# retrieval-shaped request just because the snapshot is incomplete.
_RETRIEVAL_VERBS: frozenset[str] = frozenset({
    "find", "get", "fetch", "lookup", "look",  # "look up" → starts with "look"
    "check", "read", "capture", "extract", "return",
    "what", "which", "show", "tell", "see", "discover",
    "retrieve", "scrape", "obtain", "list",
})


def _looks_retrieval_style(objective: str) -> bool:
    """Cheap verb-prefix check that the LLM can't override."""
    text = (objective or "").strip().lower()
    if not text:
        return False
    first = text.split(maxsplit=1)[0].rstrip(",.:;?")
    return first in _RETRIEVAL_VERBS


# Attributes whose values often embed the recorded record's identifiers
# (issue numbers, slugs, encoded query state). At runtime we want the
# scorer to find a STRUCTURALLY similar element, not the same record —
# so we strip these from retrieval constellations.
_DYNAMIC_VALUE_ATTRS: frozenset[str] = frozenset({
    "href", "src", "action", "srcset", "data-hovercard-url",
    "data-permalink", "data-url", "data-href",
})

# Text-content match becomes a liability when the recorded value is the
# specific value the user wanted captured (an issue title, a price). At
# runtime the value differs by definition, so the scorer would penalize
# every candidate that doesn't match the recorded text. Anything longer
# than this is treated as a value, not a stable label.
_RETRIEVAL_TEXT_LIMIT = 40


def _scrub_retrieval_constellation(raw: dict[str, Any]) -> dict[str, Any]:
    """Aggressively prune a constellation that the runtime will score
    against a different page state.

    Two dangers identify-stage constellations face that resolve-stage
    ones don't:

    1. The captured `text` IS the recorded run's value (issue title,
       price). A different run produces different text, so leaving the
       recorded text in just hurts the scorer.
    2. URL-bearing attrs (`href`, `data-hovercard-url`, …) embed the
       recorded record's identifiers. Same problem.

    We start from `prune_graph` (catches CSS Module / React useId
    noise) and additionally strip these two classes of recording
    leakage.
    """
    cleaned = prune_graph(raw)
    if isinstance(cleaned.get("text"), str) and len(cleaned["text"]) > _RETRIEVAL_TEXT_LIMIT:
        cleaned["text"] = None
    attrs = cleaned.get("attrs")
    if isinstance(attrs, dict):
        cleaned["attrs"] = {
            k: v for k, v in attrs.items() if k not in _DYNAMIC_VALUE_ATTRS
        }
    # Apply the same attr scrub to ancestors — they vote in the score.
    for ancestor in cleaned.get("ancestors") or []:
        a_attrs = ancestor.get("attrs")
        if isinstance(a_attrs, dict):
            ancestor["attrs"] = {
                k: v for k, v in a_attrs.items() if k not in _DYNAMIC_VALUE_ATTRS
            }
    return cleaned

_DROP_TAG_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<script\b[^>]*>.*?</script\s*>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<style\b[^>]*>.*?</style\s*>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<svg\b[^>]*>.*?</svg\s*>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<noscript\b[^>]*>.*?</noscript\s*>", re.DOTALL | re.IGNORECASE),
)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_WHITESPACE_RE = re.compile(r"\s+")


def identify_outputs(
    resolved_events: list[ResolvedEvent],
    objective: str,
    last_snapshot_html: str | None,
    llm: LLMClient,
    *,
    on_progress: ProgressCallback | None = None,
) -> RetrievalPlan:
    """Return a `RetrievalPlan` describing what the synthesized command
    should return. Empty plan when not retrieval-style or when no
    snapshot is available."""
    log: ProgressCallback = on_progress or (lambda _msg: None)

    if last_snapshot_html is None:
        log("  → no DOM snapshot captured; skipping retrieval analysis.")
        return RetrievalPlan(is_retrieval=False)

    stripped = _strip_snapshot(last_snapshot_html)
    user_prompt = _build_user_prompt(resolved_events, objective, stripped)

    try:
        payload = llm.call_json(_SYSTEM_PROMPT, user_prompt)
    except LLMResponseError as exc:
        log(f"  → identify LLM call failed ({exc}); skipping retrieval.")
        return RetrievalPlan(is_retrieval=False)

    if not isinstance(payload, dict):
        log(
            f"  → identify expected a JSON object, got {type(payload).__name__}; "
            "skipping retrieval."
        )
        return RetrievalPlan(is_retrieval=False)

    try:
        plan = RetrievalPlan.model_validate(payload)
    except PydanticValidationError as exc:
        log(f"  → identify output failed validation ({exc}); skipping retrieval.")
        return RetrievalPlan(is_retrieval=False)

    # Re-stamp event ids deterministically so they can't collide with
    # capture-side ids (which start with `e`) and so synthesize sees a
    # stable shape regardless of what the LLM emitted. Also scrub each
    # constellation: drop CSS Module / React useId noise and the
    # recording-specific text / URL-bearing attrs that would otherwise
    # bias the runtime scorer toward the recorded record.
    for index, target in enumerate(plan.targets, start=1):
        target.event_id = f"r{index}"
        target.constellation = Constellation.model_validate(
            _scrub_retrieval_constellation(
                target.constellation.model_dump(exclude_none=True)
            )
        )
    # `browser_get_element_attribute` requires `attribute`; if the
    # LLM forgot it, drop the whole target rather than emit an
    # invalid step downstream.
    plan.targets = [
        t for t in plan.targets
        if t.method != "browser_get_element_attribute" or (t.attribute or "").strip()
    ]

    # The objective's verb is the canonical signal. Override the LLM's
    # `is_retrieval` flag if it disagrees with the verb-prefix check —
    # we'd rather declare an output the user can ignore than miss one
    # the user actually wanted.
    if _looks_retrieval_style(objective) and not plan.is_retrieval:
        plan.is_retrieval = True

    if plan.is_retrieval and plan.targets:
        log(
            f"  → retrieval objective; identified {len(plan.targets)} target(s)."
        )
    elif plan.is_retrieval:
        log(
            "  → retrieval objective, but no clear value-bearing element on "
            "the final snapshot. Re-record so the recording ends with the "
            "value visible on screen."
        )
    else:
        log("  → not a retrieval objective; no outputs declared.")
    return plan


def _build_user_prompt(
    resolved_events: list[ResolvedEvent],
    objective: str,
    final_page_html: str,
) -> str:
    compact = [_compact_event(e) for e in resolved_events]
    lines = [
        f"objective: {objective.strip() or '(none)'}",
        "",
        "events:",
        json.dumps(compact, indent=2, ensure_ascii=False),
        "",
        "final_page_html:",
        final_page_html,
    ]
    return "\n".join(lines)


def _compact_event(event: ResolvedEvent) -> dict[str, Any]:
    out: dict[str, Any] = {
        "action": event.action,
        "target": event.target,
        "description": event.description,
        "event_ids": list(event.event_ids),
    }
    if event.constellation is not None:
        out["target_tag"] = event.constellation.tag
        if event.constellation.text:
            out["text_preview"] = event.constellation.text[:_TEXT_PREVIEW_LIMIT]
    if event.value is not None:
        out["value"] = event.value
    if event.key is not None:
        out["key"] = event.key
    if event.url_after:
        out["url_after"] = event.url_after
    return out


def _strip_snapshot(html: str, max_bytes: int = _SNAPSHOT_BUDGET_BYTES) -> str:
    """Trim an HTML document to the budget the identify LLM call accepts.

    Cheap regex pass — drops scripts/styles/SVG/noscript blocks (large,
    irrelevant) and comments, collapses whitespace, then truncates with
    a marker if the remainder still overflows.
    """
    out = html or ""
    for pattern in _DROP_TAG_PATTERNS:
        out = pattern.sub(" ", out)
    out = _COMMENT_RE.sub(" ", out)
    out = _WHITESPACE_RE.sub(" ", out).strip()
    if len(out.encode("utf-8")) > max_bytes:
        truncated = out.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
        out = truncated + " <!-- truncated -->"
    return out

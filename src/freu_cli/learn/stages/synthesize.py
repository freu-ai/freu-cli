"""Synthesize stage: turn resolved events into a fully-bound Skill.

The LLM never sees full constellations — we send only a compact
projection (action, target label, event_ids, …) and ask it to emit a
DSL whose `target` arguments carry an event-id placeholder. After the
LLM responds, we resolve each placeholder to its captured constellation
in-process. The returned Skill is therefore self-contained: every
target-bearing step has a structured constellation dict in `value`,
ready for emit/validate to consume.

Errors during binding are raised eagerly — a missing event_id or a
target-bearing step with no resolvable constellation is a pipeline
bug, not tolerable drift.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from freu_cli.learn.dsl_primitives import (
    TARGET_BEARING_METHODS,
    render_method_reference,
)
from freu_cli.learn.errors import LearnError, LLMResponseError
from freu_cli.learn.llm_client import LLMClient
from freu_cli.learn.models import (
    Constellation,
    DSLArgument,
    ResolvedEvent,
    RetrievalPlan,
    Skill,
)
from freu_cli.learn.stages._prompts import load_prompt

_SYSTEM_PROMPT_TEMPLATE = load_prompt("synthesize.txt")


def _render_system_prompt() -> str:
    return _SYSTEM_PROMPT_TEMPLATE.replace(
        "{method_reference}", render_method_reference(),
    )


_TEXT_PREVIEW_LIMIT = 40


def _compact_resolved_event(event: ResolvedEvent) -> dict[str, Any]:
    """Projection of a resolved event sent to the synthesize LLM.

    The constellation itself is NOT included — it'll be stuffed into
    each DSL step's `target` argument by `bind_constellations` based
    on `event_ids`. The LLM sees just enough to decide how to split
    events into commands and what prose to write.
    """
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


def _retrieval_plan_section(plan: RetrievalPlan) -> list[dict[str, Any]]:
    """Compact projection of the plan (constellations omitted; bound by id)."""
    return [
        {
            "event_id": target.event_id,
            "output_name": target.output_name,
            "output_description": target.output_description,
            "method": target.method,
            "attribute": target.attribute,
        }
        for target in plan.targets
    ]


def build_user_prompt(
    resolved_events: list[ResolvedEvent],
    objective: str,
    retrieval_plan: RetrievalPlan | None = None,
) -> str:
    compact = [_compact_resolved_event(e) for e in resolved_events]
    lines = [
        f"objective: {objective.strip() or '(none)'}",
        "",
        "events:",
        json.dumps(compact, indent=2, ensure_ascii=False),
    ]
    if retrieval_plan is not None and retrieval_plan.targets:
        lines.extend([
            "",
            "retrieval_plan:",
            json.dumps(
                _retrieval_plan_section(retrieval_plan),
                indent=2, ensure_ascii=False,
            ),
        ])
    return "\n".join(lines)


def synthesize_skill(
    resolved_events: list[ResolvedEvent],
    retrieval_plan: RetrievalPlan,
    objective: str,
    llm: LLMClient,
) -> Skill:
    system_prompt = _render_system_prompt()
    user_prompt = build_user_prompt(resolved_events, objective, retrieval_plan)
    payload = llm.call_json(system_prompt, user_prompt)
    if not isinstance(payload, dict):
        raise LLMResponseError(
            f"synthesize stage expected a JSON object, got {type(payload).__name__}"
        )
    try:
        skill = Skill.model_validate(payload)
    except PydanticValidationError as exc:
        raise LLMResponseError(f"synthesize output is invalid: {exc}") from exc
    bind_constellations(skill, resolved_events, retrieval_plan)
    return skill


# ---------------------------------------------------------------------------
# Constellation binding
# ---------------------------------------------------------------------------


def bind_constellations(
    skill: Skill,
    resolved_events: list[ResolvedEvent],
    retrieval_plan: RetrievalPlan | None = None,
) -> None:
    """Mutate `skill` in place: replace every `target` arg placeholder
    with the learned constellation dict.

    Resolved events bind by their captured `event_id`; retrieval-plan
    targets bind by their synthetic id (e.g. `r1`). Both share the same
    lookup table, so synthesize doesn't need to know which kind of
    target a step references.
    """
    by_event_id: dict[str, Constellation] = {}
    for event in resolved_events:
        if event.constellation is None:
            continue
        for eid in event.event_ids:
            by_event_id[eid] = event.constellation
    if retrieval_plan is not None:
        for target in retrieval_plan.targets:
            by_event_id[target.event_id] = target.constellation

    for command in skill.commands:
        for step_index, step in enumerate(command.dsl):
            if step.method not in TARGET_BEARING_METHODS:
                continue
            target_arg = next(
                (a for a in step.arguments if a.name == "target"), None,
            )
            if target_arg is None:
                raise LearnError(
                    f"{command.command}.dsl[{step_index}] ({step.method}): "
                    "target-bearing step is missing a 'target' argument."
                )
            event_id = _placeholder_event_id(target_arg)
            if not event_id and step.event_ids:
                event_id = step.event_ids[0]
            if not event_id:
                raise LearnError(
                    f"{command.command}.dsl[{step_index}] ({step.method}): "
                    "cannot bind target — no event_id in `source`, `value`, "
                    "or the step's `event_ids` list."
                )
            constellation = by_event_id.get(event_id)
            if constellation is None:
                raise LearnError(
                    f"{command.command}.dsl[{step_index}] ({step.method}): "
                    f"unknown or unresolved event_id '{event_id}'. Known: "
                    f"{sorted(by_event_id)[:5]}…"
                )
            target_arg.value = constellation.model_dump(exclude_none=True)
            target_arg.source = None


def _placeholder_event_id(arg: DSLArgument) -> str | None:
    """Pull the event-id placeholder out of a target argument.

    Synthesize emits either `{source: "<eid>"}` (preferred) or
    `{value: "<eid>"}` (LLMs occasionally confuse the two). Anything
    else — a dict, a number, `None` — means there's no placeholder.
    """
    if arg.source and isinstance(arg.source, str):
        return arg.source
    if isinstance(arg.value, str):
        return arg.value
    return None

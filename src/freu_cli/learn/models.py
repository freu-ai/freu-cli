"""Typed models carrying data between the normalize / resolve / synthesize stages."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

BROWSER_ACTIONS = (
    "click_element",
    "type_text",
    "navigate_web",
    "press_key",
    "scroll",
)


class RawEvent(BaseModel):
    """One entry from events.json. Extra fields are preserved."""

    model_config = ConfigDict(extra="allow")

    event_id: str | None = None
    type: str | None = None
    ts: int | None = None


class NormalizedEvent(BaseModel):
    """The output of stage 1 (normalize): a semantic action with target context."""

    action: Literal[
        "click_element", "type_text", "navigate_web", "press_key", "scroll",
    ]
    event_ids: list[str] = Field(default_factory=list)
    target: str | None = None
    description: str = ""
    # Filled in by the orchestrator (not the LLM) so stage 2 can reason
    # over the element's context without the LLM having to echo it back.
    # `target` (the selector dict itself) is stored alongside the chain.
    target_self: dict[str, Any] | None = None
    target_ancestors: list[dict[str, Any]] | None = None
    target_neighbors: list[dict[str, Any]] | None = None
    # target_children is tri-state: a list (possibly empty) when the target
    # has ≤ 20 children, or None when the extension dropped it (too many).
    target_children: list[dict[str, Any]] | None = None
    target_special: dict[str, Any] | None = None
    url_before: str | None = None
    url_after: str | None = None
    value: str | None = None
    key: str | None = None


class Constellation(BaseModel):
    """Pruned structured description of a target element and its context.

    The resolve stage strips unstable classes/attrs/ids from the raw graph
    (target + ancestors + neighbors + children + special) and emits this.
    The runtime scorer uses every field to pick the best-matching element
    on a live page.
    """

    model_config = ConfigDict(extra="allow")

    tag: str
    id: str | None = None
    classes: list[str] = Field(default_factory=list)
    attrs: dict[str, str] = Field(default_factory=dict)
    text: str | None = None
    x: int | None = None
    y: int | None = None
    w: int | None = None
    h: int | None = None
    x_rel: float | None = None
    w_rel: float | None = None
    ancestors: list[dict[str, Any]] = Field(default_factory=list)
    neighbors: list[dict[str, Any]] = Field(default_factory=list)
    children: list[dict[str, Any]] | None = None
    special: dict[str, Any] | None = None


class ResolvedEvent(NormalizedEvent):
    constellation: Constellation | None = None


class RetrievalTarget(BaseModel):
    """One value the synthesized command should return.

    Emitted by the identify stage when the objective is retrieval-style.
    `event_id` is synthetic (`r1`, `r2`, …) so synthesize can refer to
    the constellation through the same `source: "<event_id>"` mechanism
    used for resolved click/fill targets.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str
    output_name: str
    output_description: str = ""
    method: Literal["browser_get_element_text", "browser_get_element_attribute"]
    attribute: str | None = None
    constellation: Constellation


class RetrievalPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_retrieval: bool = False
    targets: list[RetrievalTarget] = Field(default_factory=list)
    reasoning: str = ""


class DSLArgument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: Any | None = None
    source: str | None = None


class DSLStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str
    description: str = ""
    arguments: list[DSLArgument] = Field(default_factory=list)
    outputs: list[dict[str, str]] | None = None
    # Source-event lineage used by synthesize's in-process binding step
    # to look up the learned target for this step. Required for every
    # target-bearing method; optional otherwise.
    event_ids: list[str] = Field(default_factory=list)


class CommandArg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    description: str = ""
    arguments: list[CommandArg] = Field(default_factory=list)
    outputs: list[CommandArg] = Field(default_factory=list)
    dsl: list[DSLStep]


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill: str
    skill_title: str
    skill_description: str
    commands: list[Command]

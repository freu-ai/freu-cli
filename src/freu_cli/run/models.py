from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

StepType = Literal["action", "for_each", "if"]


@dataclass
class CommandSpec:
    func: Callable[..., Any]
    params: list[str]
    runtime_params: list[str]
    output_fields: list[str]
    description: str
    allow_extra_params: bool = False


@dataclass
class Step:
    line_number: int
    method: str = ""
    description: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    step_type: StepType = "action"
    source: Any = None
    item_name: str = ""
    steps: list[Step] = field(default_factory=list)
    result: str | None = None
    output: str | None = None
    condition: Step | None = None


@dataclass(slots=True)
class SkillDefinition:
    skill_name: str
    command_name: str
    dsl: list[dict[str, Any]]
    arguments: list[str]
    outputs: list[str]
    skill_path: Path

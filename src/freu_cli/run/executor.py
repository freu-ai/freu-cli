"""Top-level skill / file runner.

Entry points:
  - SkillExecutor.run_file(path) — run a single `<Command>.json` file
  - SkillExecutor.run(skill_path=..., command=...) — run a command declared
    inside a SKILL.md file.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from freu_cli.run.browser import create_browser_adapter
from freu_cli.run.browser.bridge_manager import temporary_bridge
from freu_cli.run.dsl_executor import DSLExecutor
from freu_cli.run.errors import DSLExecutionError
from freu_cli.run.models import Step
from freu_cli.run.parser import load_skill_definition
from freu_cli.run.registry import CommandRegistry, build_builtin_registry
from freu_cli.run.renderer import TemplateRenderer
from freu_cli.run.workflow_loader import load_workflow_data

_INTEGER_RE = re.compile(r"-?\d+")
_FLOAT_RE = re.compile(r"-?\d+\.\d+")
_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


StepStartCallback = Callable[[Step, int, int], None]


def _collect_missing_inputs(steps: list[Step], available: set[str]) -> set[str]:
    missing: set[str] = set()
    available = set(available)

    def _scan(value: Any) -> None:
        if isinstance(value, str):
            for name in _TEMPLATE_VAR_RE.findall(value):
                if name not in available:
                    missing.add(name)
        elif isinstance(value, list):
            for v in value:
                _scan(v)
        elif isinstance(value, dict):
            for v in value.values():
                _scan(v)

    for step in steps:
        if step.step_type == "for_each":
            _scan(step.source)
            inner = available | ({step.item_name} if step.item_name else set())
            missing |= _collect_missing_inputs(step.steps or [], inner)
            if step.output:
                available.add(step.output)
            continue
        if step.step_type == "if":
            branch_available = set(available)
            if step.condition is not None:
                for v in (step.condition.params or {}).values():
                    _scan(v)
                branch_available |= set((step.condition.outputs or {}).keys())
            missing |= _collect_missing_inputs(step.steps or [], branch_available)
            continue
        for v in (step.params or {}).values():
            _scan(v)
        for ctx_name in (step.outputs or {}).keys():
            available.add(ctx_name)
    return missing


@dataclass(slots=True)
class SkillExecutor:
    registry: CommandRegistry | None = None
    renderer: TemplateRenderer = field(default_factory=TemplateRenderer)

    def run(
        self,
        *,
        command: str,
        skill_path: str | Path,
        inputs: dict[str, Any] | None = None,
        runtime: dict[str, Any] | None = None,
        on_step_start: StepStartCallback | None = None,
    ) -> dict[str, Any]:
        context = _normalize_inputs(inputs)
        try:
            definition = load_skill_definition(command=command, skill_path=skill_path)
            registry = self.registry or build_builtin_registry()
            loaded = load_workflow_data(
                {"dsl": definition.dsl, "context": {}}, registry=registry,
            )
            missing_inputs = [name for name in definition.arguments if name not in context]
            if missing_inputs:
                raise ValueError(f"Missing required inputs: {', '.join(missing_inputs)}")

            executor = DSLExecutor(registry=registry, renderer=self.renderer)
            runtime_kwargs = dict(runtime or {})
            steps: list[Step] = loaded["steps"]
            if "browser" not in runtime_kwargs:
                with temporary_bridge():
                    with create_browser_adapter() as browser:
                        runtime_kwargs["browser"] = browser
                        executor.execute(
                            steps, context, on_step_start=on_step_start, **runtime_kwargs,
                        )
            else:
                executor.execute(
                    steps, context, on_step_start=on_step_start, **runtime_kwargs,
                )
            return {
                "ok": True,
                "status": "success",
                "skill": definition.skill_name,
                "command": definition.command_name,
                "outputs": list(definition.outputs),
                "context": context,
                "skill_path": str(definition.skill_path),
            }
        except Exception as exc:
            return _failure_result(
                exc, context=context, step=1,
                phase="execute" if isinstance(exc, DSLExecutionError) else "prepare",
                skill=getattr(locals().get("definition"), "skill_name", None),
                command=command,
                executor=locals().get("executor"),
            )

    def run_file(
        self,
        file_path: str | Path,
        *,
        inputs: dict[str, Any] | None = None,
        runtime: dict[str, Any] | None = None,
        on_step_start: StepStartCallback | None = None,
    ) -> dict[str, Any]:
        target = Path(file_path).expanduser().resolve()
        context = _normalize_inputs(inputs)
        try:
            if target.suffix.lower() != ".json":
                raise ValueError(
                    f"Unsupported file type for 'freu-cli run': {target.suffix or '<none>'}"
                )
            registry = self.registry or build_builtin_registry()
            raw_data = json.loads(target.read_text(encoding="utf-8"))
            loaded = load_workflow_data(_normalize_file_dsl(raw_data), registry=registry)
            context = dict(loaded["context"])
            context.update(_normalize_inputs(inputs))
            steps: list[Step] = loaded["steps"]
            missing = _collect_missing_inputs(steps, set(context.keys()))
            if missing:
                missing_list = ", ".join(sorted(missing))
                raise ValueError(
                    f"Missing required inputs: {missing_list}. "
                    f"Pass them on the command line, e.g. "
                    f"--{sorted(missing)[0].replace('_', '-')} <value>"
                )

            executor = DSLExecutor(registry=registry, renderer=self.renderer)
            runtime_kwargs = dict(runtime or {})
            if "browser" not in runtime_kwargs:
                with temporary_bridge():
                    with create_browser_adapter() as browser:
                        runtime_kwargs["browser"] = browser
                        executor.execute(
                            steps, context, on_step_start=on_step_start, **runtime_kwargs,
                        )
            else:
                executor.execute(
                    steps, context, on_step_start=on_step_start, **runtime_kwargs,
                )
            return {
                "ok": True, "status": "success", "file": str(target), "context": context,
            }
        except Exception as exc:
            return _failure_result(
                exc, context=context, step=1,
                phase="execute" if isinstance(exc, DSLExecutionError) else "prepare",
                file=str(target),
                executor=locals().get("executor"),
            )


def run_skill(
    *,
    command: str,
    skill_path: str | Path,
    inputs: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
    registry: CommandRegistry | None = None,
    on_step_start: StepStartCallback | None = None,
) -> dict[str, Any]:
    executor = SkillExecutor(registry=registry)
    return executor.run(
        command=command, skill_path=skill_path,
        inputs=inputs, runtime=runtime, on_step_start=on_step_start,
    )


def run_file(
    file_path: str | Path,
    *,
    inputs: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
    registry: CommandRegistry | None = None,
    on_step_start: StepStartCallback | None = None,
) -> dict[str, Any]:
    executor = SkillExecutor(registry=registry)
    return executor.run_file(
        file_path, inputs=inputs, runtime=runtime, on_step_start=on_step_start,
    )


def _normalize_inputs(inputs: dict[str, Any] | None) -> dict[str, Any]:
    if inputs is None:
        return {}
    if not isinstance(inputs, dict):
        raise TypeError("inputs must be a dict")
    return dict(inputs)


def _normalize_file_dsl(data: Any) -> dict[str, Any]:
    if isinstance(data, list):
        return {"dsl": _normalize_dsl_steps(data), "context": {}}
    if not isinstance(data, dict):
        raise ValueError(
            'Command JSON must be an object with a top-level "steps" array, '
            'e.g. {"steps": [...]}.'
        )
    steps = data.get("steps")
    if not isinstance(steps, list):
        raise ValueError(
            'Command JSON is missing a top-level "steps" array. '
            'Expected shape: {"steps": [...]}.'
        )
    return {"dsl": _normalize_dsl_steps(steps), "context": data.get("context") or {}}


def _normalize_dsl_steps(steps: list[Any]) -> list[Any]:
    normalized_steps: list[Any] = []
    for step in steps:
        if not isinstance(step, dict):
            normalized_steps.append(step)
            continue
        normalized_step = dict(step)
        step_type = normalized_step.get("type")
        if step_type == "for_each":
            normalized_step["source"] = _normalize_source_value(normalized_step.get("source"))
            nested = normalized_step.get("steps")
            if isinstance(nested, list):
                normalized_step["steps"] = _normalize_dsl_steps(nested)
            normalized_steps.append(normalized_step)
            continue
        if step_type == "if":
            condition = normalized_step.get("condition")
            if isinstance(condition, dict):
                normalized_step["condition"] = _normalize_dsl_steps([condition])[0]
            nested = normalized_step.get("steps")
            if isinstance(nested, list):
                normalized_step["steps"] = _normalize_dsl_steps(nested)
            normalized_steps.append(normalized_step)
            continue

        arguments = normalized_step.get("arguments")
        if isinstance(arguments, list):
            normalized_arguments: list[Any] = []
            for argument in arguments:
                if not isinstance(argument, dict):
                    normalized_arguments.append(argument)
                    continue
                normalized_argument = dict(argument)
                if "source" in normalized_argument and normalized_argument.get("source") is not None:
                    normalized_argument["value"] = _normalize_source_value(
                        normalized_argument["source"]
                    )
                    normalized_argument.pop("source", None)
                elif "value" in normalized_argument and normalized_argument.get("value") is not None:
                    normalized_argument["value"] = _coerce_literal_value(
                        normalized_argument["value"]
                    )
                normalized_arguments.append(normalized_argument)
            normalized_step["arguments"] = normalized_arguments

        if "output" in normalized_step and "outputs" not in normalized_step:
            output_name = str(normalized_step.get("output") or "").strip()
            if output_name:
                normalized_step["outputs"] = [
                    {"name": output_name, "value": "__whole_result__"}
                ]
            normalized_step.pop("output", None)
        normalized_steps.append(normalized_step)
    return normalized_steps


def _normalize_source_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    if not normalized:
        return value
    if normalized.startswith("{{") and normalized.endswith("}}"):
        return normalized
    return "{{" + normalized + "}}"


def _coerce_literal_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    if _INTEGER_RE.fullmatch(normalized):
        return int(normalized)
    if _FLOAT_RE.fullmatch(normalized):
        return float(normalized)
    lowered = normalized.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none"}:
        return None
    return value


def _failure_result(
    exc: Exception,
    *,
    context: dict[str, Any],
    step: int | None,
    phase: str,
    file: str | None = None,
    skill: str | None = None,
    command: str | None = None,
    executor: DSLExecutor | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "status": "failed",
        "context": context,
        "phase": phase,
        "error": str(exc),
        "error_type": type(exc).__name__,
        "completed_steps": list(executor.completed_descriptions) if executor is not None else [],
        "failed_step": None,
    }
    if step is not None:
        result["step"] = step
    if isinstance(exc, DSLExecutionError):
        if exc.step_index is not None:
            result["step"] = exc.step_index
        if exc.line_number is not None:
            result["line"] = exc.line_number
        if exc.method:
            result["method"] = exc.method
        if exc.description:
            result["failed_step"] = exc.description
    if file is not None:
        result["file"] = file
    if skill is not None:
        result["skill"] = skill
    if command is not None:
        result["command"] = command
    return result

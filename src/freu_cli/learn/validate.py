"""Validate a synthesized Skill before we emit it to disk.

Checks:
  1. Every DSL method is in `ALLOWED_METHODS`.
  2. Every step's argument names are accepted by the method schema
     (required args present; unknown args flagged).
  3. Every target-bearing step has a `target` arg whose `value` is a
     constellation dict (synthesize binds these in-process) and a
     non-empty `event_ids` list.
  4. Every `{{var}}` / `source` reference resolves to a command argument
     OR a prior-step output in the same command.
"""

from __future__ import annotations

from freu_cli.learn.dsl_primitives import (
    ALLOWED_METHODS,
    TARGET_BEARING_METHODS,
    method_schema,
)
from freu_cli.learn.errors import ValidationError
from freu_cli.learn.models import Command, DSLArgument, Skill


def validate_skill(skill: Skill) -> None:
    errors: list[str] = []
    for command in skill.commands:
        errors.extend(_validate_command(command))
    if errors:
        raise ValidationError("\n".join(errors))


def _validate_command(command: Command) -> list[str]:
    errors: list[str] = []
    declared_args = {arg.name for arg in command.arguments}
    available_vars = set(declared_args)

    for step_index, step in enumerate(command.dsl, start=1):
        prefix = f"[{command.command} step {step_index}] "

        if step.method not in ALLOWED_METHODS:
            errors.append(
                prefix + f"unknown DSL method: {step.method}. "
                f"Allowed: {', '.join(sorted(ALLOWED_METHODS))}"
            )
            continue

        schema = method_schema(step.method)
        assert schema is not None  # ALLOWED_METHODS membership guarantees this

        if step.method in TARGET_BEARING_METHODS and not step.event_ids:
            errors.append(
                prefix + f"{step.method} is missing `event_ids`; every "
                "target-bearing step must record its source events."
            )

        arg_names = {arg.name for arg in step.arguments}
        for required in schema.required_args:
            if required not in arg_names:
                errors.append(
                    prefix + f"{step.method} is missing required argument: {required}"
                )

        allowed_arg_names = set(schema.required_args) | set(schema.optional_args)
        for arg in step.arguments:
            if arg.name not in allowed_arg_names:
                errors.append(
                    prefix
                    + f"{step.method} has unknown argument: {arg.name}. "
                    + f"Allowed: {', '.join(sorted(allowed_arg_names))}"
                )
            has_value = arg.value is not None
            has_source = isinstance(arg.source, str) and bool(arg.source)
            if not has_value and not has_source:
                errors.append(
                    prefix + f"argument {arg.name} must supply 'value' or 'source'"
                )
            if arg.name == "target" and step.method in TARGET_BEARING_METHODS:
                _check_target_arg(prefix, step.method, arg, errors)
                continue  # target never references command args/outputs
            if has_source:
                ref = arg.source.strip()
                ref_name = ref[2:-2].strip() if ref.startswith("{{") and ref.endswith("}}") else ref
                if ref_name not in available_vars:
                    errors.append(
                        prefix
                        + f"argument {arg.name} references '{ref_name}' which is "
                        + "not a command argument or prior-step output"
                    )

        if step.outputs:
            for output in step.outputs:
                name = output.get("name")
                value = output.get("value")
                if not name:
                    errors.append(prefix + "output entry missing 'name'")
                    continue
                if not value:
                    errors.append(prefix + f"output '{name}' missing 'value'")
                    continue
                if value not in schema.output_fields and value != "__whole_result__":
                    errors.append(
                        prefix
                        + f"output '{name}' references unknown field '{value}' "
                        + f"on {step.method}. "
                        + (
                            f"Known fields: {', '.join(schema.output_fields)}"
                            if schema.output_fields
                            else "This method has no output fields."
                        )
                    )
                available_vars.add(name)
    return errors


def _check_target_arg(
    prefix: str, method: str, arg: DSLArgument, errors: list[str],
) -> None:
    """After synthesize binds, every target arg must carry a dict value
    (the learned constellation)."""
    if isinstance(arg.value, dict):
        if "tag" not in arg.value:
            errors.append(
                prefix + f"{method}.target is a dict but missing the 'tag' "
                "key required for a constellation."
            )
        return
    if arg.value is None and arg.source:
        errors.append(
            prefix + f"{method}.target still has an unresolved `source: "
            f"{arg.source!r}`. Synthesize should have substituted the "
            "learned constellation before validation."
        )
        return
    errors.append(
        prefix + f"{method}.target must be a constellation dict, got "
        f"{type(arg.value).__name__}."
    )

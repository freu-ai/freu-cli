"""Parse SKILL.md files in the Freu skill format.

Format:

```
---
name: <skill_name>
description: >
  <one or two sentences>
version: 1.0.0
---

# <Skill Title>

<skill description paragraph>

## <CommandName>

<command description>

### CLI
freu-cli run <SkillName> <CommandName> --<arg-name> <value>

### Arguments
- **<arg_name>** → <description>

### Outputs
- **<output_name>** → <description>
```

The `CommandName.json` that holds the DSL steps sits in the same directory
as `SKILL.md`. The parser resolves it by name (not by parsing the `### CLI`
text).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from freu_cli.run.errors import DSLExecutionError
from freu_cli.run.models import SkillDefinition

_FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_TOP_LEVEL_HEADING_RE = re.compile(r"^#\s+", re.MULTILINE)
_SECOND_LEVEL_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_THIRD_LEVEL_HEADING_RE = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
_BULLET_LINE_RE = re.compile(
    r"^\s*[-*]\s+\*\*(?P<name>[^*]+?)\*\*\s*(?:[→—\-:]\s*(?P<description>.+?))?\s*$",
    re.MULTILINE,
)


def load_skill_definition(
    *,
    command: str,
    skill_path: str | Path,
) -> SkillDefinition:
    """Load a SkillDefinition for a single command within a SKILL.md file."""
    resolved_skill_path = Path(skill_path).expanduser().resolve()
    if not resolved_skill_path.exists():
        raise DSLExecutionError(f"Skill file not found: {resolved_skill_path}")
    content = resolved_skill_path.read_text(encoding="utf-8")

    command_body = _extract_command_body(content, command)
    if command_body is None:
        raise DSLExecutionError(f"Skill command not found: {command}")

    arguments = _extract_bullet_names(_extract_h3_section(command_body, "Arguments"))
    outputs = _extract_bullet_names(_extract_h3_section(command_body, "Outputs"))
    dsl = _load_sibling_command_dsl(resolved_skill_path, command)
    dsl = _normalize_skill_dsl_sources(dsl, arguments)

    return SkillDefinition(
        skill_name=_derive_skill_name(resolved_skill_path, content),
        command_name=command,
        dsl=dsl,
        arguments=arguments,
        outputs=outputs,
        skill_path=resolved_skill_path,
    )


def _extract_command_body(content: str, command_name: str) -> str | None:
    body = _strip_front_matter(content)
    body = _strip_h1_block(body)
    matches = list(_SECOND_LEVEL_HEADING_RE.finditer(body))
    for index, match in enumerate(matches):
        name = str(match.group(1) or "").strip()
        if name != command_name:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        return body[start:end].strip()
    return None


def _strip_front_matter(content: str) -> str:
    text = content.lstrip("﻿")
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return text
    return text[match.end():]


def _strip_h1_block(content: str) -> str:
    h1_match = _TOP_LEVEL_HEADING_RE.search(content)
    if not h1_match:
        return content
    h2_match = _SECOND_LEVEL_HEADING_RE.search(content, h1_match.end())
    if h2_match is None:
        return ""
    return content[h2_match.start():]


def _extract_h3_section(command_body: str, section_name: str) -> str:
    matches = list(_THIRD_LEVEL_HEADING_RE.finditer(command_body))
    target = section_name.strip().lower()
    for index, match in enumerate(matches):
        heading = str(match.group(1) or "").strip().lower()
        if heading != target:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(command_body)
        return command_body[start:end].strip()
    return ""


def _extract_bullet_names(section_text: str) -> list[str]:
    names: list[str] = []
    for match in _BULLET_LINE_RE.finditer(section_text or ""):
        name = str(match.group("name") or "").strip()
        if name:
            names.append(name)
    return names


def extract_bullet_entries(section_text: str) -> list[dict[str, str]]:
    """Return [{"name": ..., "description": ...}, ...] bullets from a section."""
    entries: list[dict[str, str]] = []
    for match in _BULLET_LINE_RE.finditer(section_text or ""):
        name = str(match.group("name") or "").strip()
        description = str(match.group("description") or "").strip()
        if name:
            entries.append({"name": name, "description": description})
    return entries


def _load_sibling_command_dsl(skill_path: Path, command_name: str) -> list[dict[str, Any]]:
    json_path = (skill_path.parent / f"{command_name}.json").resolve()
    if not json_path.exists():
        raise DSLExecutionError(
            f"Command JSON file not found next to {skill_path.name}: {json_path}"
        )
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DSLExecutionError(
            f"Invalid JSON in {json_path} for command '{command_name}': {exc.msg}"
        ) from exc

    if isinstance(raw, dict) and isinstance(raw.get("steps"), list):
        return raw["steps"]
    if isinstance(raw, list):
        return raw
    raise DSLExecutionError(
        f'Command JSON file {json_path} must be {{"steps": [...]}} or a JSON array'
    )


def list_commands(skill_path: str | Path) -> list[str]:
    """Return the ordered list of command names declared in a SKILL.md file."""
    resolved = Path(skill_path).expanduser().resolve()
    content = resolved.read_text(encoding="utf-8")
    body = _strip_front_matter(content)
    body = _strip_h1_block(body)
    return [
        str(match.group(1) or "").strip()
        for match in _SECOND_LEVEL_HEADING_RE.finditer(body)
        if str(match.group(1) or "").strip()
    ]


def _derive_skill_name(skill_path: Path, content: str) -> str:
    match = _FRONT_MATTER_RE.match(content.lstrip("﻿"))
    if match:
        name_match = re.search(r"^name:\s*(.+?)\s*$", match.group(1), re.MULTILINE)
        if name_match:
            return name_match.group(1).strip()
    if skill_path.name == "SKILL.md":
        return skill_path.parent.name
    return skill_path.stem


def _normalize_skill_dsl_sources(
    steps: list[dict[str, Any]], argument_names: list[str],
) -> list[dict[str, Any]]:
    normalized_steps: list[dict[str, Any]] = []
    argument_name_set = {name.strip() for name in argument_names if name.strip()}
    for step in steps:
        if not isinstance(step, dict):
            normalized_steps.append(step)
            continue
        normalized_step = dict(step)
        step_type = normalized_step.get("type")
        if step_type == "for_each":
            normalized_step["source"] = _normalize_source_reference(normalized_step.get("source"))
            nested = normalized_step.get("steps")
            if isinstance(nested, list):
                normalized_step["steps"] = _normalize_skill_dsl_sources(nested, argument_names)
            normalized_steps.append(normalized_step)
            continue
        if step_type == "if":
            condition = normalized_step.get("condition")
            if isinstance(condition, dict):
                normalized_step["condition"] = _normalize_skill_dsl_sources(
                    [condition], argument_names,
                )[0]
            nested = normalized_step.get("steps")
            if isinstance(nested, list):
                normalized_step["steps"] = _normalize_skill_dsl_sources(nested, argument_names)
            normalized_steps.append(normalized_step)
            continue

        raw_arguments = normalized_step.get("arguments")
        if isinstance(raw_arguments, list):
            normalized_arguments: list[dict[str, Any]] = []
            for argument in raw_arguments:
                if not isinstance(argument, dict):
                    normalized_arguments.append(argument)
                    continue
                normalized_argument = dict(argument)
                if "source" in normalized_argument and normalized_argument.get("source") is not None:
                    normalized_argument["value"] = _normalize_source_reference(
                        normalized_argument["source"]
                    )
                    normalized_argument.pop("source", None)
                elif "value" in normalized_argument:
                    normalized_argument["value"] = _normalize_argument_value(
                        normalized_argument.get("value"), argument_name_set,
                    )
                normalized_arguments.append(normalized_argument)
            normalized_step["arguments"] = normalized_arguments

        if "output" in normalized_step and "outputs" not in normalized_step:
            output_name = str(normalized_step.get("output") or "").strip()
            if output_name:
                normalized_step["outputs"] = [{"name": output_name, "value": output_name}]
            normalized_step.pop("output", None)

        normalized_steps.append(normalized_step)
    return normalized_steps


def _normalize_source_reference(source: Any) -> Any:
    """Normalize an argument `source` reference into `{{var}}` form.

    Accepted inputs:
      - `{{var}}` — already wrapped, kept as-is.
      - `var` — a bare identifier; wrapped as `{{var}}`.

    Anything else (dotted paths like `Input.foo` or
    `Skill.Cmd.field`, literal strings with punctuation) is returned
    unchanged so template rendering will surface a clear "Missing
    template variable" error at runtime instead of silently rewriting
    the reference.
    """
    if not isinstance(source, str):
        return source
    binding = source.strip()
    if not binding:
        return source
    if binding.startswith("{{") and binding.endswith("}}"):
        return binding
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", binding):
        return "{{" + binding + "}}"
    return binding


def _normalize_argument_value(value: Any, argument_names: set[str]) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    if normalized in argument_names:
        return "{{" + normalized + "}}"
    return value

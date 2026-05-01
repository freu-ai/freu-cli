"""Write a validated Skill to disk as SKILL.md + one <Command>.json per command.

The emitted SKILL.md follows the Freu skill format:

  - YAML frontmatter with `name`, `description`, `version`.
  - `# <Skill Title>` H1 block with the skill's description paragraph.
  - `## <CommandName>` H2 per command, with a short description paragraph.
  - `### CLI` H3 containing a one-line `freu-cli run ...` invocation that
    includes every argument as a `--kebab-case-arg <value>` flag.
  - `### Arguments` + `### Outputs` H3s with bullet lists of the form
    `- **name** → description`.

If `out_dir/SKILL.md` already exists, new commands are MERGED into the
existing file instead of overwriting it: a command name that already
exists is replaced in place; a new name is appended after the existing
ones. The existing frontmatter + H1 block (skill-level metadata) is
preserved verbatim — learning a new command into an established skill
does not rename or re-describe the skill itself.

The round-trip through `freu_cli.run.parser.load_skill_definition` is
covered by `tests/learn/test_emit.py`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from freu_cli.learn.models import Command, CommandArg, DSLArgument, DSLStep, Skill

_FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_H2_RE = re.compile(r"^## (.+?)\s*$", re.MULTILINE)
_FRONTMATTER_NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)


def write_skill(skill: Skill, out_dir: Path) -> list[Path]:
    """Write (or merge) `skill` into `out_dir`.

    - `<out_dir>/<Command>.json` is always written (and overwritten if it
      already exists).
    - `<out_dir>/SKILL.md` is created fresh when absent. If it exists,
      new commands are merged into it: existing commands with a matching
      name are replaced; new names are appended. Skill-level frontmatter
      and the H1 block are preserved as-is.
    """
    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for command in skill.commands:
        command_path = out_dir / f"{command.command}.json"
        command_path.write_text(
            _dumps_json(_command_to_dict(command)),
            encoding="utf-8",
        )
        written.append(command_path)

    skill_md_path = out_dir / "SKILL.md"
    if skill_md_path.exists():
        existing = skill_md_path.read_text(encoding="utf-8")
        rendered = _merge_skill_md(existing, skill)
    else:
        rendered = _render_skill_md(skill)
    skill_md_path.write_text(rendered, encoding="utf-8")
    written.append(skill_md_path)
    return written


def _dumps_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _command_to_dict(command: Command) -> dict[str, Any]:
    return {
        "arguments": [_command_arg_to_dict(arg) for arg in command.arguments],
        "outputs": [_command_arg_to_dict(arg) for arg in command.outputs],
        "steps": [_step_to_dict(step) for step in command.dsl],
    }


def _command_arg_to_dict(arg: CommandArg) -> dict[str, Any]:
    return {"name": arg.name, "description": arg.description}


def _step_to_dict(step: DSLStep) -> dict[str, Any]:
    out: dict[str, Any] = {"method": step.method}
    if step.description:
        out["description"] = step.description
    if step.arguments:
        out["arguments"] = [_argument_to_dict(argument) for argument in step.arguments]
    if step.outputs:
        out["outputs"] = [dict(output) for output in step.outputs]
    return out


def _argument_to_dict(argument: DSLArgument) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": argument.name}
    if argument.source is not None:
        entry["source"] = argument.source
    elif argument.value is not None:
        entry["value"] = argument.value
    return entry


def _render_skill_md(skill: Skill) -> str:
    parts: list[str] = []
    parts.append(_render_frontmatter(skill))
    parts.append("")
    parts.append(f"# {skill.skill_title}")
    parts.append("")
    parts.append(skill.skill_description.strip())
    parts.append("")
    for command in skill.commands:
        parts.append(_render_command_block(skill.skill, command))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _merge_skill_md(existing: str, skill: Skill) -> str:
    """Merge `skill`'s commands into an existing SKILL.md document.

    The existing frontmatter + H1 block (everything before the first
    `## ` line) is preserved byte-for-byte. The existing skill `name:`
    from the frontmatter is used when rendering the new commands' `### CLI`
    lines, so newly-learned commands use the same skill identifier as the
    ones that were there before.
    """
    header, existing_commands = _split_existing_md(existing)
    skill_name_for_cli = _extract_frontmatter_name(existing) or skill.skill

    command_order: list[str] = [name for name, _ in existing_commands]
    command_blocks: dict[str, str] = dict(existing_commands)

    for command in skill.commands:
        block = _render_command_block(skill_name_for_cli, command)
        if command.command in command_blocks:
            command_blocks[command.command] = block
        else:
            command_order.append(command.command)
            command_blocks[command.command] = block

    header_text = header.rstrip() + "\n\n" if header.strip() else ""
    body = "\n\n".join(command_blocks[name] for name in command_order)
    return (header_text + body).rstrip() + "\n"


def _split_existing_md(content: str) -> tuple[str, list[tuple[str, str]]]:
    """Split SKILL.md content into (header_before_first_H2, [(name, body), ...]).

    `body` for each entry is the full `## <name>` section text up to (but
    not including) the next `## ` heading or end-of-file, right-stripped
    of trailing whitespace.
    """
    matches = list(_H2_RE.finditer(content))
    if not matches:
        return content, []
    header = content[: matches[0].start()]
    commands: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        name = str(match.group(1) or "").strip()
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        body = content[start:end].rstrip()
        if name:
            commands.append((name, body))
    return header, commands


def _extract_frontmatter_name(content: str) -> str:
    match = _FRONT_MATTER_RE.match(content.lstrip("﻿"))
    if not match:
        return ""
    name_match = _FRONTMATTER_NAME_RE.search(match.group(1))
    if not name_match:
        return ""
    return name_match.group(1).strip()


def _render_frontmatter(skill: Skill) -> str:
    lines = ["---", f"name: {skill.skill}"]
    description = skill.skill_description.strip().replace("\n", " ")
    if description:
        lines.append("description: >")
        lines.append(f"  {description}")
    lines.append("version: 1.0.0")
    lines.append("---")
    return "\n".join(lines)


def _render_command_block(skill_name: str, command: Command) -> str:
    return "\n".join(_render_command_section(skill_name, command)).rstrip()


def _render_command_section(skill_name: str, command: Command) -> list[str]:
    lines: list[str] = [f"## {command.command}", ""]
    description = command.description.strip()
    if description:
        lines.append(description)
        lines.append("")

    lines.append("### CLI")
    lines.append(_render_cli_line(skill_name, command))
    lines.append("")

    lines.append("### Arguments")
    if command.arguments:
        for argument in command.arguments:
            lines.append(_render_bullet(argument.name, argument.description))
    else:
        lines.append("-")
    lines.append("")

    lines.append("### Outputs")
    if command.outputs:
        for output in command.outputs:
            lines.append(_render_bullet(output.name, output.description))
    else:
        lines.append("-")
    return lines


def _render_cli_line(skill_name: str, command: Command) -> str:
    parts = [f"freu-cli run {skill_name} {command.command}"]
    for argument in command.arguments:
        flag = "--" + argument.name.replace("_", "-")
        placeholder = f"<{argument.name}>"
        parts.append(f"{flag} {placeholder}")
    return " ".join(parts)


def _render_bullet(name: str, description: str) -> str:
    description = (description or "").strip()
    if description:
        return f"- **{name}** → {description}"
    return f"- **{name}**"

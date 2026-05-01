from __future__ import annotations

import json
from pathlib import Path

import pytest

from freu_cli.run.errors import DSLExecutionError
from freu_cli.run.parser import (
    _normalize_source_reference,
    extract_bullet_entries,
    list_commands,
    load_skill_definition,
)

SKILL_MD = """---
name: Demo
description: >
  A demo skill for tests.
version: 1.0.0
---

# Demo — Agent Skill

A demo skill for tests.

## DoThing

Does a thing.

### CLI
freu-cli run Demo DoThing --repo-url <repo_url>

### Arguments
- **repo_url** → The repository URL.

### Outputs
-

## OpenIssues

Open the issues tab.

### CLI
freu-cli run Demo OpenIssues

### Arguments
-

### Outputs
- **count** → How many issues were shown.
"""


@pytest.fixture
def demo_skill_dir(tmp_path: Path) -> Path:
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(SKILL_MD)
    (tmp_path / "DoThing.json").write_text(json.dumps({
        "steps": [
            {"method": "browser_open_url", "arguments": [{"name": "url", "source": "repo_url"}]},
        ],
    }))
    (tmp_path / "OpenIssues.json").write_text(json.dumps({
        "steps": [
            {"method": "browser_click_element", "arguments": [{"name": "selector", "value": "nav a.issues"}]},
        ],
    }))
    return tmp_path


def test_list_commands_returns_h2_headings(demo_skill_dir: Path):
    assert list_commands(demo_skill_dir / "SKILL.md") == ["DoThing", "OpenIssues"]


def test_load_skill_definition_reads_arguments_and_dsl(demo_skill_dir: Path):
    skill_md = demo_skill_dir / "SKILL.md"
    definition = load_skill_definition(command="DoThing", skill_path=skill_md)
    assert definition.skill_name == "Demo"
    assert definition.arguments == ["repo_url"]
    assert definition.outputs == []
    # `source: repo_url` is rewritten into `value: {{repo_url}}` by the parser.
    assert definition.dsl[0]["arguments"][0]["value"] == "{{repo_url}}"


def test_load_skill_definition_reads_outputs(demo_skill_dir: Path):
    skill_md = demo_skill_dir / "SKILL.md"
    definition = load_skill_definition(command="OpenIssues", skill_path=skill_md)
    assert definition.arguments == []
    assert definition.outputs == ["count"]


def test_load_skill_definition_raises_on_missing_command(demo_skill_dir: Path):
    with pytest.raises(DSLExecutionError):
        load_skill_definition(command="Nope", skill_path=demo_skill_dir / "SKILL.md")


def test_load_skill_definition_raises_on_missing_sibling_json(tmp_path: Path):
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(SKILL_MD)  # no sibling JSON files written
    with pytest.raises(DSLExecutionError) as exc_info:
        load_skill_definition(command="DoThing", skill_path=skill_md)
    assert "DoThing.json" in str(exc_info.value)


def test_extract_bullet_entries_parses_name_and_description():
    section = (
        "- **foo** → The foo arg.\n"
        "- **bar** → Bar's description.\n"
        "- **baz**\n"
    )
    assert extract_bullet_entries(section) == [
        {"name": "foo", "description": "The foo arg."},
        {"name": "bar", "description": "Bar's description."},
        {"name": "baz", "description": ""},
    ]


def test_normalize_source_reference_wraps_bare_identifier():
    assert _normalize_source_reference("repo_url") == "{{repo_url}}"


def test_normalize_source_reference_keeps_explicit_braces():
    assert _normalize_source_reference("{{repo_url}}") == "{{repo_url}}"


def test_normalize_source_reference_does_not_strip_dotted_prefix():
    """Dotted prefixes like `Input.foo` are not part of the source
    reference syntax — pass through unchanged so template rendering
    surfaces a clear error rather than silently rewriting."""
    assert _normalize_source_reference("Input.repo_url") == "Input.repo_url"


def test_normalize_source_reference_does_not_strip_dotted_path():
    """Multi-segment dotted paths are not part of the source reference
    syntax — pass through unchanged."""
    assert _normalize_source_reference("Skill.Command.field") == "Skill.Command.field"


def test_parser_passes_dict_target_values_through_unchanged(tmp_path: Path):
    """Constellation dicts stored as DSL argument values must round-trip
    through the parser without template-wrapping or normalization."""
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(SKILL_MD)
    constellation = {
        "tag": "button",
        "attrs": {"data-action": "star", "aria-label": "Star"},
        "text": "Star",
        "ancestors": [{"tag": "main"}],
    }
    (tmp_path / "DoThing.json").write_text(json.dumps({
        "steps": [
            {
                "method": "browser_click_element",
                "arguments": [{"name": "target", "value": constellation}],
            },
        ],
    }))
    (tmp_path / "OpenIssues.json").write_text(json.dumps({"steps": []}))

    definition = load_skill_definition(command="DoThing", skill_path=skill_md)
    arg = definition.dsl[0]["arguments"][0]
    assert arg["name"] == "target"
    assert arg["value"] == constellation  # unchanged byte-for-byte


def test_dotted_source_prefix_is_not_rewritten_in_skill_dsl(tmp_path: Path):
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(SKILL_MD)
    # `source: Input.repo_url` is not a valid reference shape — the
    # parser should leave it untouched so template rendering fails
    # loudly at runtime rather than silently rewriting.
    (tmp_path / "DoThing.json").write_text(json.dumps({
        "steps": [
            {"method": "browser_open_url", "arguments": [{"name": "url", "source": "Input.repo_url"}]},
        ],
    }))
    (tmp_path / "OpenIssues.json").write_text(json.dumps({
        "steps": [{"method": "browser_click_element", "arguments": [{"name": "selector", "value": "nav"}]}]
    }))
    definition = load_skill_definition(command="DoThing", skill_path=skill_md)
    assert definition.dsl[0]["arguments"][0]["value"] == "Input.repo_url"

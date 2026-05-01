"""emit.write_skill -> run.parser.load_skill_definition round-trip.

This is the critical contract test: the SKILL.md / Command.json files the
learn pipeline emits must be loadable by the run-time parser without any
hand-editing, and must follow the Freu skill format (YAML frontmatter +
H2 commands + H3 subsections + bullet-list arguments).
"""

from __future__ import annotations

import json
from pathlib import Path

from freu_cli.learn.emit import write_skill
from freu_cli.learn.models import (
    Command,
    CommandArg,
    DSLArgument,
    DSLStep,
    Skill,
)
from freu_cli.run.parser import list_commands, load_skill_definition

_STAR_CONSTELLATION = {
    "tag": "button",
    "attrs": {"data-action": "star", "aria-label": "Star"},
    "text": "Star",
    "ancestors": [{"tag": "main"}],
}
_ISSUES_CONSTELLATION = {
    "tag": "a",
    "attrs": {"href": "/anthropics/courses/issues"},
    "text": "Issues",
    "ancestors": [{"tag": "nav"}],
}


def _build_skill() -> Skill:
    return Skill(
        skill="GitHub",
        skill_title="GitHub — Agent Skill",
        skill_description="Star a repo and open issues.",
        commands=[
            Command(
                command="StarRepo",
                description="Star a GitHub repository by URL.",
                arguments=[CommandArg(name="repo_url", description="Full URL of the repository")],
                outputs=[],
                dsl=[
                    DSLStep(
                        method="browser_open_url",
                        description="Open the repository page.",
                        arguments=[DSLArgument(name="url", source="repo_url")],
                        event_ids=["e1"],
                    ),
                    DSLStep(
                        method="browser_click_element",
                        description="Click the Star button on the repository page.",
                        arguments=[DSLArgument(name="target", value=_STAR_CONSTELLATION)],
                        event_ids=["e2"],
                    ),
                ],
            ),
            Command(
                command="OpenIssues",
                description="Open the Issues tab of the current repo.",
                dsl=[
                    DSLStep(
                        method="browser_click_element",
                        arguments=[DSLArgument(name="target", value=_ISSUES_CONSTELLATION)],
                        event_ids=["e3"],
                    ),
                ],
            ),
        ],
    )


def test_write_skill_creates_files(tmp_path: Path):
    skill = _build_skill()
    paths = write_skill(skill, tmp_path)
    files = {p.name for p in paths}
    assert "SKILL.md" in files
    assert "StarRepo.json" in files
    assert "OpenIssues.json" in files


def test_skill_md_has_frontmatter_and_h2_commands(tmp_path: Path):
    write_skill(_build_skill(), tmp_path)
    skill_md = (tmp_path / "SKILL.md").read_text()
    assert skill_md.startswith("---\n")
    assert "\nname: GitHub\n" in skill_md
    assert "\nversion: 1.0.0\n" in skill_md
    assert "# GitHub — Agent Skill" in skill_md
    assert "## StarRepo" in skill_md
    assert "## OpenIssues" in skill_md
    assert "### CLI" in skill_md
    assert "### Arguments" in skill_md
    assert "### Outputs" in skill_md


def test_cli_block_includes_argument_flags(tmp_path: Path):
    write_skill(_build_skill(), tmp_path)
    skill_md = (tmp_path / "SKILL.md").read_text()
    assert "freu-cli run GitHub StarRepo --repo-url <repo_url>" in skill_md
    # Commands with no arguments still render a CLI line (no flags).
    assert "freu-cli run GitHub OpenIssues" in skill_md


def test_arguments_rendered_as_bulleted_name_description(tmp_path: Path):
    write_skill(_build_skill(), tmp_path)
    skill_md = (tmp_path / "SKILL.md").read_text()
    assert "- **repo_url** → Full URL of the repository" in skill_md


def test_command_json_includes_arguments_and_outputs_at_top_level(tmp_path: Path):
    write_skill(_build_skill(), tmp_path)
    star_payload = json.loads((tmp_path / "StarRepo.json").read_text())
    assert star_payload["arguments"] == [
        {"name": "repo_url", "description": "Full URL of the repository"},
    ]
    assert star_payload["outputs"] == []
    assert isinstance(star_payload["steps"], list)

    issues_payload = json.loads((tmp_path / "OpenIssues.json").read_text())
    assert issues_payload["arguments"] == []
    assert issues_payload["outputs"] == []


def test_command_json_round_trips_step_descriptions(tmp_path: Path):
    write_skill(_build_skill(), tmp_path)
    star_payload = json.loads((tmp_path / "StarRepo.json").read_text())
    descriptions = [step.get("description") for step in star_payload["steps"]]
    assert descriptions == [
        "Open the repository page.",
        "Click the Star button on the repository page.",
    ]
    # OpenIssues' step has no description; the field is omitted, not empty.
    issues_payload = json.loads((tmp_path / "OpenIssues.json").read_text())
    assert "description" not in issues_payload["steps"][0]


def test_emitted_skill_round_trips_through_parser(tmp_path: Path):
    skill = _build_skill()
    write_skill(skill, tmp_path)
    skill_md = tmp_path / "SKILL.md"

    assert list_commands(skill_md) == ["StarRepo", "OpenIssues"]

    star_def = load_skill_definition(command="StarRepo", skill_path=skill_md)
    assert star_def.skill_name == "GitHub"
    assert star_def.arguments == ["repo_url"]
    assert star_def.outputs == []
    methods = [step["method"] for step in star_def.dsl]
    assert methods == ["browser_open_url", "browser_click_element"]

    star_open = star_def.dsl[0]
    assert star_open["arguments"][0]["name"] == "url"
    # `source: repo_url` is normalized into `value: {{repo_url}}` by the parser.
    assert star_open["arguments"][0]["value"] == "{{repo_url}}"
    star_click = star_def.dsl[1]
    target_value = star_click["arguments"][0]["value"]
    assert isinstance(target_value, dict)
    assert target_value["tag"] == "button"
    assert target_value["attrs"]["data-action"] == "star"

    issues_def = load_skill_definition(command="OpenIssues", skill_path=skill_md)
    assert issues_def.dsl[0]["method"] == "browser_click_element"
    target_value = issues_def.dsl[0]["arguments"][0]["value"]
    assert target_value["tag"] == "a"
    assert target_value["attrs"]["href"] == "/anthropics/courses/issues"


def _build_skill_with(commands: list[Command]) -> Skill:
    return Skill(
        skill="GitHub",
        skill_title="GitHub — Agent Skill",
        skill_description="Placeholder description.",
        commands=commands,
    )


def _new_command(name: str, description: str = "") -> Command:
    return Command(
        command=name,
        description=description or f"{name} command",
        arguments=[],
        outputs=[],
        dsl=[DSLStep(
            method="browser_open_url",
            arguments=[DSLArgument(name="url", value=f"https://{name.lower()}.example.com")],
            event_ids=["e1"],
        )],
    )


def test_write_skill_appends_new_command_to_existing_skill(tmp_path: Path):
    write_skill(_build_skill(), tmp_path)
    original_md = (tmp_path / "SKILL.md").read_text()

    # A second learn run produces a brand-new command the existing skill
    # didn't have. It should be appended.
    write_skill(_build_skill_with([_new_command("OpenPulls")]), tmp_path)
    updated_md = (tmp_path / "SKILL.md").read_text()

    assert list_commands(tmp_path / "SKILL.md") == ["StarRepo", "OpenIssues", "OpenPulls"]
    assert (tmp_path / "OpenPulls.json").exists()
    # The existing skill metadata block (frontmatter + H1) is preserved byte-for-byte.
    header_end = original_md.find("## StarRepo")
    assert original_md[:header_end] == updated_md[: original_md.find("## StarRepo")]


def test_write_skill_replaces_existing_command_by_name(tmp_path: Path):
    write_skill(_build_skill(), tmp_path)
    # Overwrite StarRepo with a simpler single-step DSL.
    new_star = Command(
        command="StarRepo",
        description="Refreshed StarRepo command.",
        arguments=[],
        outputs=[],
        dsl=[DSLStep(
            method="browser_open_url",
            arguments=[DSLArgument(name="url", value="https://refreshed.example.com")],
            event_ids=["e1"],
        )],
    )
    write_skill(_build_skill_with([new_star]), tmp_path)

    assert list_commands(tmp_path / "SKILL.md") == ["StarRepo", "OpenIssues"]
    # The StarRepo JSON now has exactly one step pointing at the refreshed URL.
    star_def = load_skill_definition(command="StarRepo", skill_path=tmp_path / "SKILL.md")
    assert len(star_def.dsl) == 1
    assert star_def.dsl[0]["arguments"][0]["value"] == "https://refreshed.example.com"

    updated_md = (tmp_path / "SKILL.md").read_text()
    assert "Refreshed StarRepo command." in updated_md
    # OpenIssues stays untouched.
    issues_def = load_skill_definition(command="OpenIssues", skill_path=tmp_path / "SKILL.md")
    assert issues_def.dsl[0]["method"] == "browser_click_element"


def test_write_skill_merge_preserves_existing_frontmatter_name(tmp_path: Path):
    """Merging uses the EXISTING skill's frontmatter name for new CLI lines."""
    write_skill(_build_skill(), tmp_path)
    # New synthesis carries a totally different skill name; merge should ignore
    # it and use the existing frontmatter's "GitHub".
    new_skill = Skill(
        skill="SomeOtherName",
        skill_title="Different title (ignored on merge)",
        skill_description="Different description (ignored on merge)",
        commands=[_new_command("OpenDiscussions")],
    )
    write_skill(new_skill, tmp_path)
    updated_md = (tmp_path / "SKILL.md").read_text()
    # Existing frontmatter is preserved.
    assert "\nname: GitHub\n" in updated_md
    assert "SomeOtherName" not in updated_md
    # New command's CLI line uses "GitHub", not "SomeOtherName".
    assert "freu-cli run GitHub OpenDiscussions" in updated_md

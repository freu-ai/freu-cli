"""End-to-end learn pipeline using a mocked LLM — no network, no browser."""

from __future__ import annotations

import json
from pathlib import Path

from freu_cli.learn.pipeline import run_learn
from freu_cli.run.parser import load_skill_definition


def _search_constellation() -> dict:
    return {
        "tag": "input",
        "id": None,
        "classes": [],
        "attrs": {"name": "q", "type": "search"},
        "text": None,
        "ancestors": [
            {"tag": "html"},
            {"tag": "body"},
            {"tag": "header"},
            {"tag": "form", "attrs": {"role": "search"}},
            {"tag": "input", "attrs": {"name": "q", "type": "search"}},
        ],
        "neighbors": [],
        "children": None,
        "special": None,
    }


def _star_constellation() -> dict:
    return {
        "tag": "button",
        "id": None,
        "classes": ["btn-sm"],
        "attrs": {"data-action": "star", "aria-label": "Star"},
        "text": "Star",
        "ancestors": [
            {"tag": "html"},
            {"tag": "body"},
            {"tag": "main"},
            {"tag": "ul", "classes": ["pagehead-actions"]},
            {"tag": "li"},
            {"tag": "button", "attrs": {"data-action": "star"}, "text": "Star"},
        ],
        "neighbors": [{"tag": "span", "text": "1,234"}],
        "children": [{"tag": "svg"}, {"tag": "span", "text": "Star"}],
        "special": None,
    }


def _canned_responses() -> list[str]:
    normalize_out = json.dumps([
        {"action": "click_element", "event_ids": ["e2"], "target": "search", "description": "focus search"},
        {"action": "type_text", "event_ids": ["e3"], "target": "search", "description": "type repo", "value": "anthropics/courses"},
        {"action": "press_key", "event_ids": ["e4"], "target": "search", "description": "enter", "key": "Enter"},
        {"action": "click_element", "event_ids": ["e6"], "target": "Star", "description": "click star"},
    ])
    search = json.dumps(_search_constellation())
    star = json.dumps(_star_constellation())
    synthesize_out = json.dumps({
        "skill": "GitHubStar",
        "skill_title": "GitHub — Star",
        "skill_description": "Search for a repo then star it.",
        "commands": [
            {
                "command": "StarRepo",
                "description": "Search for a repo by name and star it.",
                "arguments": [{"name": "repo", "description": "owner/name"}],
                "outputs": [],
                "dsl": [
                    {
                        "method": "browser_open_url",
                        "description": "Open the GitHub home page.",
                        "arguments": [{"name": "url", "value": "https://github.com/"}],
                        "event_ids": ["e1"],
                    },
                    {
                        "method": "browser_click_element",
                        "description": "Focus the global search input.",
                        "arguments": [{"name": "target", "source": "e2"}],
                        "event_ids": ["e2"],
                    },
                    {
                        "method": "browser_fill_element",
                        "description": "Type the repository name into search.",
                        "arguments": [
                            {"name": "target", "source": "e3"},
                            {"name": "text", "source": "repo"},
                        ],
                        "event_ids": ["e3"],
                    },
                    {
                        "method": "browser_press_key",
                        "description": "Submit the search with Enter.",
                        "arguments": [
                            {"name": "target", "source": "e4"},
                            {"name": "key", "value": "Enter"},
                        ],
                        "event_ids": ["e4"],
                    },
                    {
                        "method": "browser_click_element",
                        "description": "Click the Star button on the repository page.",
                        "arguments": [{"name": "target", "source": "e6"}],
                        "event_ids": ["e6"],
                    },
                ],
            },
        ],
    })
    # 4 target-bearing events -> 4 resolve calls.
    return [
        normalize_out,
        search, search, search, star,
        synthesize_out,
    ]


def test_learn_end_to_end_mocked_llm(tmp_path: Path, fake_llm, github_star_events):
    events_path = tmp_path / "events.json"
    events_path.write_text(json.dumps(github_star_events))
    out_dir = tmp_path / "skill"
    llm = fake_llm(_canned_responses())

    skill = run_learn(events_path, "search for a github repo and star it", out_dir, llm)
    assert skill.skill == "GitHubStar"
    assert len(skill.commands) == 1

    # The binder filled the target args with constellation dicts.
    star_step = skill.commands[0].dsl[-1]
    assert star_step.method == "browser_click_element"
    target_arg = star_step.arguments[0]
    assert isinstance(target_arg.value, dict)
    assert target_arg.value["tag"] == "button"
    assert target_arg.value["attrs"]["data-action"] == "star"

    # Round-trip through the run-time parser.
    definition = load_skill_definition(command="StarRepo", skill_path=out_dir / "SKILL.md")
    assert definition.arguments == ["repo"]
    assert [step["method"] for step in definition.dsl] == [
        "browser_open_url",
        "browser_click_element",
        "browser_fill_element",
        "browser_press_key",
        "browser_click_element",
    ]


def test_learn_writes_intermediates_when_log_dir_set(
    tmp_path: Path, fake_llm, github_star_events,
):
    events_path = tmp_path / "events.json"
    events_path.write_text(json.dumps(github_star_events))
    out_dir = tmp_path / "skill"
    log_dir = tmp_path / "log"
    llm = fake_llm(_canned_responses())

    run_learn(events_path, "obj", out_dir, llm, log_dir=log_dir)

    for name in ("normalized.json", "resolved.json", "identified.json", "synthesized.json"):
        path = log_dir / name
        assert path.exists(), f"expected log intermediate {name} to be written"
        payload = json.loads(path.read_text())
        assert payload is not None, f"{name} is empty"

    # No snapshot in the synthetic capture, so identify is a no-op and
    # writes a plan with no targets — that still round-trips through the
    # JSON dump and proves the stage ran.
    identified = json.loads((log_dir / "identified.json").read_text())
    assert identified["is_retrieval"] is False
    assert identified["targets"] == []

    normalized = json.loads((log_dir / "normalized.json").read_text())
    assert isinstance(normalized, list) and len(normalized) == 4
    assert normalized[0]["action"] == "click_element"

    resolved = json.loads((log_dir / "resolved.json").read_text())
    assert isinstance(resolved, list)
    assert resolved[0]["constellation"]["tag"] == "input"
    assert resolved[3]["constellation"]["attrs"]["data-action"] == "star"

    # synthesize now returns a fully-bound skill — target args carry
    # constellation dicts, not event-id placeholders.
    synthesized = json.loads((log_dir / "synthesized.json").read_text())
    assert synthesized["skill"] == "GitHubStar"
    bound_star = synthesized["commands"][0]["dsl"][-1]
    assert bound_star["method"] == "browser_click_element"
    assert bound_star["arguments"][0]["value"]["tag"] == "button"
    assert bound_star["description"] == "Click the Star button on the repository page."


def test_learn_without_log_dir_writes_no_intermediates(
    tmp_path: Path, fake_llm, github_star_events,
):
    events_path = tmp_path / "events.json"
    events_path.write_text(json.dumps(github_star_events))
    out_dir = tmp_path / "skill"
    llm = fake_llm(_canned_responses())

    run_learn(events_path, "obj", out_dir, llm)

    assert (out_dir / "SKILL.md").exists()
    # There's no log directory when log_dir is None.
    assert not (tmp_path / "log").exists()

import json

import pytest

from freu_cli.learn.errors import LearnError
from freu_cli.learn.models import (
    Command,
    Constellation,
    DSLArgument,
    DSLStep,
    NormalizedEvent,
    ResolvedEvent,
    RetrievalPlan,
    RetrievalTarget,
    Skill,
)
from freu_cli.learn.stages.synthesize import (
    _compact_resolved_event,
    bind_constellations,
    synthesize_skill,
)


def _empty_plan() -> RetrievalPlan:
    return RetrievalPlan(is_retrieval=False)


def _resolved(**overrides) -> ResolvedEvent:
    base = dict(
        action="click_element",
        event_ids=["e1"],
        target="target",
        description="did a thing",
    )
    base.update(overrides)
    return ResolvedEvent.model_validate(NormalizedEvent(**base).model_dump())


def _constellation(**overrides) -> Constellation:
    defaults = dict(
        tag="button", text="Star",
        attrs={"data-action": "star"}, classes=["btn-primary"],
    )
    defaults.update(overrides)
    return Constellation.model_validate(defaults)


def _skill_with_step(step: DSLStep) -> Skill:
    return Skill(
        skill="X",
        skill_title="X",
        skill_description="x",
        commands=[
            Command(
                command="Go",
                description="go",
                arguments=[],
                outputs=[],
                dsl=[step],
            ),
        ],
    )


def test_compact_resolved_event_omits_full_constellation_but_keeps_preview():
    event = _resolved()
    event.constellation = _constellation(text="Star this repository to bookmark it for later")
    compact = _compact_resolved_event(event)
    assert "constellation" not in compact  # full graph is NOT sent to LLM
    assert compact["target_tag"] == "button"
    assert compact["text_preview"] == "Star this repository to bookmark it for "
    assert len(compact["text_preview"]) == 40
    assert compact["event_ids"] == ["e1"]


def test_synthesize_returns_skill_with_bound_target(fake_llm):
    """synthesize_skill returns a fully-bound skill: target args carry
    the resolved constellation dict, not the LLM's event-id placeholder.
    """
    events = [_resolved()]
    events[0].constellation = _constellation()
    payload = json.dumps({
        "skill": "DemoSkill",
        "skill_title": "Demo",
        "skill_description": "A small demo skill.",
        "commands": [
            {
                "command": "DoThing",
                "description": "does a thing",
                "arguments": [],
                "outputs": [],
                "dsl": [
                    {
                        "method": "browser_open_url",
                        "description": "Open the demo landing page.",
                        "arguments": [{"name": "url", "value": "https://example.com"}],
                        "event_ids": ["e0"],
                    },
                    {
                        "method": "browser_click_element",
                        "description": "Click the primary call-to-action button.",
                        "arguments": [{"name": "target", "source": "e1"}],
                        "event_ids": ["e1"],
                    },
                ],
            }
        ],
    })
    llm = fake_llm([payload])
    skill = synthesize_skill(events, _empty_plan(), "do a demo", llm)
    assert skill.skill == "DemoSkill"
    click_step = skill.commands[0].dsl[1]
    assert click_step.method == "browser_click_element"
    assert click_step.description == "Click the primary call-to-action button."
    assert click_step.arguments[0].name == "target"
    assert click_step.arguments[0].source is None
    assert isinstance(click_step.arguments[0].value, dict)
    assert click_step.arguments[0].value["tag"] == "button"
    assert click_step.event_ids == ["e1"]


def test_synthesize_supports_multi_command_split(fake_llm):
    events = [_resolved(), _resolved(event_ids=["e2"])]
    events[0].constellation = _constellation()
    events[1].constellation = _constellation(text="Issues")
    payload = json.dumps({
        "skill": "GitHub",
        "skill_title": "GitHub",
        "skill_description": "Star a repo then open issues.",
        "commands": [
            {
                "command": "StarRepo", "description": "star",
                "arguments": [{"name": "repo_url", "description": "repo url"}],
                "dsl": [
                    {
                        "method": "browser_open_url",
                        "description": "Open the repository page.",
                        "arguments": [{"name": "url", "source": "repo_url"}],
                        "event_ids": ["e0"],
                    },
                    {
                        "method": "browser_click_element",
                        "description": "Click the Star button.",
                        "arguments": [{"name": "target", "source": "e1"}],
                        "event_ids": ["e1"],
                    },
                ],
            },
            {
                "command": "OpenIssues", "description": "issues",
                "dsl": [
                    {
                        "method": "browser_click_element",
                        "description": "Click the Issues tab.",
                        "arguments": [{"name": "target", "source": "e2"}],
                        "event_ids": ["e2"],
                    },
                ],
            },
        ],
    })
    llm = fake_llm([payload])
    skill = synthesize_skill(events, _empty_plan(), "star a repo and open issues", llm)
    assert [c.command for c in skill.commands] == ["StarRepo", "OpenIssues"]
    assert skill.commands[0].arguments[0].name == "repo_url"


def test_synthesize_with_retrieval_plan_binds_synthetic_constellation(fake_llm):
    """When the plan declares a retrieval target, synthesize emits a
    read step whose `source` references the synthetic event id; the
    binder substitutes the planned constellation into that step's
    `target` arg, and the command declares the planned output.
    """
    events = [_resolved(event_ids=["e1"])]
    events[0].constellation = _constellation()
    plan = RetrievalPlan(
        is_retrieval=True,
        targets=[
            RetrievalTarget(
                event_id="r1",
                output_name="repo_title",
                output_description="The repository title shown on the page.",
                method="browser_get_element_text",
                attribute=None,
                constellation=Constellation(
                    tag="bdi", text="some-repo", attrs={}, classes=[],
                ),
            ),
        ],
    )
    payload = json.dumps({
        "skill": "GitHub",
        "skill_title": "GitHub",
        "skill_description": "Look up a repository title.",
        "commands": [
            {
                "command": "GetRepoTitle",
                "description": "fetch the repo title",
                "arguments": [],
                "outputs": [
                    {"name": "repo_title", "description": "The repository title shown on the page."},
                ],
                "dsl": [
                    {
                        "method": "browser_click_element",
                        "description": "Click the link to navigate to the repo page.",
                        "arguments": [{"name": "target", "source": "e1"}],
                        "event_ids": ["e1"],
                    },
                    {
                        "method": "browser_get_element_text",
                        "description": "Read the repo title.",
                        "arguments": [{"name": "target", "source": "r1"}],
                        "outputs": [{"name": "repo_title", "value": "text"}],
                        "event_ids": ["r1"],
                    },
                ],
            }
        ],
    })
    llm = fake_llm([payload])
    skill = synthesize_skill(events, plan, "find the repo title", llm)

    command = skill.commands[0]
    assert [out.name for out in command.outputs] == ["repo_title"]

    read_step = command.dsl[-1]
    assert read_step.method == "browser_get_element_text"
    target_arg = read_step.arguments[0]
    assert target_arg.source is None
    assert isinstance(target_arg.value, dict)
    assert target_arg.value["tag"] == "bdi"
    assert target_arg.value["text"] == "some-repo"
    assert read_step.outputs == [{"name": "repo_title", "value": "text"}]


def test_build_user_prompt_renders_retrieval_plan(fake_llm):
    """The plan section is appended to the synthesize prompt only when
    the plan has at least one target."""
    from freu_cli.learn.stages.synthesize import build_user_prompt

    plan = RetrievalPlan(
        is_retrieval=True,
        targets=[
            RetrievalTarget(
                event_id="r1",
                output_name="price",
                output_description="The order total",
                method="browser_get_element_attribute",
                attribute="data-price",
                constellation=Constellation(tag="span", text="$42"),
            ),
        ],
    )
    rendered = build_user_prompt([_resolved()], "find the price", plan)
    assert "retrieval_plan:" in rendered
    assert "price" in rendered
    assert "data-price" in rendered

    rendered_empty = build_user_prompt([_resolved()], "do a thing", _empty_plan())
    assert "retrieval_plan:" not in rendered_empty


# ---------------------------------------------------------------------------
# Constellation binding (called internally at the end of synthesize_skill)
# ---------------------------------------------------------------------------


def test_bind_replaces_source_placeholder_with_constellation_dict():
    event = _resolved(event_ids=["e6"])
    event.constellation = Constellation(tag="button", text="Star", attrs={"data-action": "star"})
    step = DSLStep(
        method="browser_click_element",
        arguments=[DSLArgument(name="target", source="e6")],
        event_ids=["e6"],
    )
    skill = _skill_with_step(step)

    bind_constellations(skill, [event])

    bound_arg = skill.commands[0].dsl[0].arguments[0]
    assert bound_arg.source is None
    assert isinstance(bound_arg.value, dict)
    assert bound_arg.value["tag"] == "button"
    assert bound_arg.value["attrs"] == {"data-action": "star"}


def test_bind_uses_step_event_ids_fallback_when_target_has_none():
    """Some models forget the `source` on the target arg; fall back to the
    step's event_ids list so we can still bind."""
    event = _resolved(event_ids=["e3"])
    event.constellation = Constellation(tag="input", text="q")
    step = DSLStep(
        method="browser_fill_element",
        arguments=[
            DSLArgument(name="target"),  # no source, no value
            DSLArgument(name="text", value="hello"),
        ],
        event_ids=["e3"],
    )
    skill = _skill_with_step(step)

    bind_constellations(skill, [event])

    assert skill.commands[0].dsl[0].arguments[0].value["tag"] == "input"


def test_bind_skips_non_target_bearing_methods():
    step = DSLStep(
        method="browser_open_url",
        arguments=[DSLArgument(name="url", value="https://x.com")],
        event_ids=["e1"],
    )
    skill = _skill_with_step(step)
    bind_constellations(skill, [])  # no events, fine
    assert skill.commands[0].dsl[0].arguments[0].value == "https://x.com"


def test_bind_raises_on_unknown_event_id():
    event = _resolved(event_ids=["e3"])
    event.constellation = Constellation(tag="input")
    step = DSLStep(
        method="browser_click_element",
        arguments=[DSLArgument(name="target", source="e999")],
        event_ids=["e999"],
    )
    skill = _skill_with_step(step)
    with pytest.raises(LearnError) as exc:
        bind_constellations(skill, [event])
    assert "e999" in str(exc.value)


def test_bind_raises_when_target_arg_is_missing():
    step = DSLStep(
        method="browser_click_element",
        arguments=[],
        event_ids=["e1"],
    )
    skill = _skill_with_step(step)
    with pytest.raises(LearnError) as exc:
        bind_constellations(skill, [])
    assert "missing a 'target' argument" in str(exc.value)


def test_bind_raises_when_event_has_no_constellation():
    event = _resolved(event_ids=["e4"])  # constellation left as None
    step = DSLStep(
        method="browser_click_element",
        arguments=[DSLArgument(name="target", source="e4")],
        event_ids=["e4"],
    )
    skill = _skill_with_step(step)
    with pytest.raises(LearnError) as exc:
        bind_constellations(skill, [event])
    # An event without a resolved constellation isn't registered in the
    # binder's lookup, so the error reads like an unknown event id.
    assert "e4" in str(exc.value)


def test_bind_resolves_retrieval_plan_targets():
    """Synthetic ids from the retrieval plan share the binder's lookup
    table with resolved-event ids, so a step referencing `r1` picks up
    the planned constellation."""
    plan = RetrievalPlan(
        is_retrieval=True,
        targets=[
            RetrievalTarget(
                event_id="r1",
                output_name="title",
                output_description="page title",
                method="browser_get_element_text",
                attribute=None,
                constellation=Constellation(tag="h1", text="Hello"),
            ),
        ],
    )
    step = DSLStep(
        method="browser_get_element_text",
        arguments=[DSLArgument(name="target", source="r1")],
        event_ids=["r1"],
    )
    skill = _skill_with_step(step)

    bind_constellations(skill, [], plan)

    bound_arg = skill.commands[0].dsl[0].arguments[0]
    assert bound_arg.source is None
    assert bound_arg.value["tag"] == "h1"
    assert bound_arg.value["text"] == "Hello"

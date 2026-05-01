import pytest

from freu_cli.learn.errors import ValidationError
from freu_cli.learn.models import (
    Command,
    CommandArg,
    DSLArgument,
    DSLStep,
    Skill,
)
from freu_cli.learn.validate import validate_skill


def _skill(commands):
    return Skill(
        skill="S", skill_title="T", skill_description="d", commands=commands,
    )


def _open_url_step():
    return DSLStep(
        method="browser_open_url",
        arguments=[DSLArgument(name="url", value="https://x")],
    )


def test_validate_passes_minimal_skill():
    skill = _skill([
        Command(command="Go", description="", dsl=[_open_url_step()]),
    ])
    validate_skill(skill)


def test_validate_rejects_unknown_method():
    skill = _skill([
        Command(
            command="Oops", description="",
            dsl=[DSLStep(
                method="browser_fill_form",
                arguments=[DSLArgument(name="foo", value="bar")],
            )],
        ),
    ])
    with pytest.raises(ValidationError) as exc_info:
        validate_skill(skill)
    assert "unknown DSL method" in str(exc_info.value)


def test_validate_rejects_missing_required_arg():
    skill = _skill([
        Command(
            command="NoUrl", description="",
            dsl=[DSLStep(method="browser_open_url", arguments=[])],
        ),
    ])
    with pytest.raises(ValidationError) as exc_info:
        validate_skill(skill)
    assert "missing required argument: url" in str(exc_info.value)


def test_validate_rejects_unknown_arg_name():
    skill = _skill([
        Command(
            command="Bad", description="",
            dsl=[DSLStep(
                method="browser_open_url",
                arguments=[
                    DSLArgument(name="url", value="https://x"),
                    DSLArgument(name="nope", value="extra"),
                ],
            )],
        ),
    ])
    with pytest.raises(ValidationError) as exc_info:
        validate_skill(skill)
    assert "unknown argument: nope" in str(exc_info.value)


def test_validate_rejects_dangling_source_reference():
    skill = _skill([
        Command(
            command="Dangle", description="",
            dsl=[DSLStep(
                method="browser_open_url",
                arguments=[DSLArgument(name="url", source="missing_var")],
            )],
        ),
    ])
    with pytest.raises(ValidationError) as exc_info:
        validate_skill(skill)
    assert "references 'missing_var'" in str(exc_info.value)


def test_validate_accepts_source_for_known_argument():
    skill = _skill([
        Command(
            command="Param", description="",
            arguments=[CommandArg(name="repo_url")],
            dsl=[DSLStep(
                method="browser_open_url",
                arguments=[DSLArgument(name="url", source="repo_url")],
            )],
        ),
    ])
    validate_skill(skill)


def test_validate_chains_prior_step_outputs():
    skill = _skill([
        Command(
            command="Chain", description="",
            dsl=[
                DSLStep(
                    method="browser_get_page_info",
                    arguments=[],
                    outputs=[{"name": "page_title", "value": "title"}],
                ),
                DSLStep(
                    method="browser_open_url",
                    arguments=[DSLArgument(name="url", source="page_title")],
                ),
            ],
        ),
    ])
    validate_skill(skill)


def test_validate_rejects_unknown_output_field():
    skill = _skill([
        Command(
            command="Bad", description="",
            dsl=[DSLStep(
                method="browser_get_page_info",
                arguments=[],
                outputs=[{"name": "foo", "value": "not_a_field"}],
            )],
        ),
    ])
    with pytest.raises(ValidationError) as exc_info:
        validate_skill(skill)
    assert "unknown field 'not_a_field'" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Target-argument shape enforcement (synthesize binds constellations in)
# ---------------------------------------------------------------------------


def _click_step(target_value=None, target_source=None, *, event_ids=("e1",)):
    return DSLStep(
        method="browser_click_element",
        arguments=[DSLArgument(name="target", value=target_value, source=target_source)],
        event_ids=list(event_ids),
    )


def test_validate_accepts_target_with_constellation_dict():
    skill = _skill([
        Command(
            command="Click", description="",
            dsl=[_click_step(target_value={"tag": "button", "attrs": {"data-action": "star"}})],
        ),
    ])
    validate_skill(skill)


def test_validate_rejects_target_when_value_is_a_string():
    skill = _skill([
        Command(
            command="Star", description="",
            dsl=[_click_step(target_value="button.star")],
        ),
    ])
    with pytest.raises(ValidationError) as exc:
        validate_skill(skill)
    assert "must be a constellation dict" in str(exc.value)


def test_validate_rejects_target_dict_missing_tag():
    skill = _skill([
        Command(
            command="Bad", description="",
            dsl=[_click_step(target_value={"attrs": {}})],
        ),
    ])
    with pytest.raises(ValidationError) as exc:
        validate_skill(skill)
    assert "'tag'" in str(exc.value)


def test_validate_rejects_unresolved_source_on_target():
    skill = _skill([
        Command(
            command="Unbound", description="",
            dsl=[_click_step(target_source="e123")],
        ),
    ])
    with pytest.raises(ValidationError) as exc:
        validate_skill(skill)
    assert "unresolved" in str(exc.value)


def test_validate_rejects_target_bearing_step_without_event_ids():
    skill = _skill([
        Command(
            command="NoIds", description="",
            dsl=[_click_step(target_value={"tag": "button"}, event_ids=())],
        ),
    ])
    with pytest.raises(ValidationError) as exc:
        validate_skill(skill)
    assert "event_ids" in str(exc.value)

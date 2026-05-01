import json

from freu_cli.learn.errors import LLMResponseError
from freu_cli.learn.llm_client import LLMClient
from freu_cli.learn.models import (
    Constellation,
    NormalizedEvent,
    ResolvedEvent,
)
from freu_cli.learn.stages.identify import (
    _looks_retrieval_style,
    _strip_snapshot,
    identify_outputs,
)


def _resolved(**overrides) -> ResolvedEvent:
    base = dict(
        action="click_element",
        event_ids=["e1"],
        target="target",
        description="did a thing",
    )
    base.update(overrides)
    return ResolvedEvent.model_validate(NormalizedEvent(**base).model_dump())


def _raising_llm() -> LLMClient:
    def _call(_sys: str, _user: str) -> str:
        raise LLMResponseError("boom")

    return LLMClient(model="test", _call=_call)


def test_identify_skips_when_no_snapshot(fake_llm):
    """No snapshot = no LLM call, plan is empty and is_retrieval is False."""
    llm = fake_llm([])  # would raise if called
    plan = identify_outputs([_resolved()], "find the price", None, llm)
    assert plan.is_retrieval is False
    assert plan.targets == []


def test_identify_returns_empty_plan_for_non_retrieval(fake_llm):
    payload = json.dumps({
        "is_retrieval": False,
        "targets": [],
        "reasoning": "objective is action-style (star a repo)",
    })
    llm = fake_llm([payload])
    plan = identify_outputs(
        [_resolved()], "star a repo", "<html><body>x</body></html>", llm,
    )
    assert plan.is_retrieval is False
    assert plan.targets == []


def test_identify_builds_targets_and_renumbers_event_ids(fake_llm):
    """The LLM may emit any event_id; identify deterministically rewrites
    them to r1, r2, … so synthesize sees a stable shape."""
    payload = json.dumps({
        "is_retrieval": True,
        "reasoning": "title-fetch objective",
        "targets": [
            {
                "event_id": "whatever",
                "output_name": "repo_title",
                "output_description": "Repo title",
                "method": "browser_get_element_text",
                "attribute": None,
                "constellation": {
                    "tag": "bdi",
                    "text": "anthropics/courses",
                    "attrs": {},
                    "classes": [],
                },
            },
            {
                "event_id": "x",
                "output_name": "repo_url",
                "output_description": "Repo URL",
                "method": "browser_get_element_attribute",
                "attribute": "href",
                "constellation": {
                    "tag": "a",
                    "attrs": {"href": "/anthropics/courses"},
                },
            },
        ],
    })
    llm = fake_llm([payload])
    plan = identify_outputs(
        [_resolved()], "find the repo title and url",
        "<html><body><h1><bdi>anthropics/courses</bdi></h1></body></html>",
        llm,
    )
    assert plan.is_retrieval is True
    assert [t.event_id for t in plan.targets] == ["r1", "r2"]
    assert plan.targets[0].method == "browser_get_element_text"
    assert isinstance(plan.targets[0].constellation, Constellation)
    assert plan.targets[1].attribute == "href"


def test_identify_drops_attribute_targets_missing_attribute(fake_llm):
    """A `browser_get_element_attribute` target without `attribute` would
    fail downstream; identify silently drops it rather than emit junk."""
    payload = json.dumps({
        "is_retrieval": True,
        "reasoning": "links are interesting",
        "targets": [
            {
                "event_id": "x",
                "output_name": "good_text",
                "output_description": "has text",
                "method": "browser_get_element_text",
                "constellation": {"tag": "h1", "text": "Hi"},
            },
            {
                "event_id": "y",
                "output_name": "bad_attr",
                "output_description": "missing attribute",
                "method": "browser_get_element_attribute",
                "attribute": None,
                "constellation": {"tag": "a"},
            },
        ],
    })
    llm = fake_llm([payload])
    plan = identify_outputs([_resolved()], "find x", "<html></html>", llm)
    assert [t.output_name for t in plan.targets] == ["good_text"]


def test_identify_falls_back_on_llm_error():
    """LLM errors short-circuit to an empty plan rather than failing the
    pipeline. Best-effort behavior — a missed retrieval is recoverable;
    a failed pipeline is not."""
    plan = identify_outputs(
        [_resolved()], "find the price",
        "<html><body>x</body></html>",
        _raising_llm(),
    )
    assert plan.is_retrieval is False
    assert plan.targets == []


def test_identify_falls_back_when_payload_is_not_an_object(fake_llm):
    llm = fake_llm([json.dumps(["not", "an", "object"])])
    plan = identify_outputs(
        [_resolved()], "find x", "<html></html>", llm,
    )
    assert plan.is_retrieval is False


def test_strip_snapshot_drops_scripts_styles_svg_and_comments():
    raw = (
        "<html><head>"
        "<style>body{color:red}</style>"
        "<script>alert('boom')</script>"
        "</head><body>"
        "<!-- ignore me -->"
        "<svg><path d='M0 0'/></svg>"
        "<h1>Hello</h1>"
        "</body></html>"
    )
    out = _strip_snapshot(raw)
    assert "alert" not in out
    assert "color:red" not in out
    assert "ignore me" not in out
    assert "<path" not in out
    assert "<h1>Hello</h1>" in out


def test_strip_snapshot_truncates_to_byte_budget():
    blob = "<div>" + "a" * 5000 + "</div>"
    out = _strip_snapshot(blob, max_bytes=1024)
    assert len(out.encode("utf-8")) <= 1024 + len(" <!-- truncated -->")
    assert out.endswith("<!-- truncated -->")


def test_looks_retrieval_style_recognizes_common_verbs():
    for objective in (
        "find the most-commented issue",
        "Get the order total",
        "Look up the repo's website",
        "what is the price of XYZ",
        "Check the inbox count",
        "extract the article title",
        "fetch the email subject",
    ):
        assert _looks_retrieval_style(objective), objective

    for objective in (
        "star a github repository",
        "send an email",
        "book a hotel",
        "submit the form",
        "",
    ):
        assert not _looks_retrieval_style(objective), objective


def test_identify_overrides_llm_when_objective_is_retrieval_style(fake_llm):
    """The LLM may misjudge a retrieval objective if the snapshot doesn't
    contain the value (e.g. recording stopped on the wrong page). The
    verb in the objective is the canonical signal — the orchestrator
    flips is_retrieval back on so synthesize knows to declare an output."""
    payload = json.dumps({
        "is_retrieval": False,
        "targets": [],
        "reasoning": "value not visible on this snapshot",
    })
    llm = fake_llm([payload])
    plan = identify_outputs(
        [_resolved()],
        "find the most-commented issue",
        "<html><body><h1>Code</h1></body></html>",
        llm,
    )
    assert plan.is_retrieval is True
    # The LLM didn't pick a target, so synthesize gets nothing to wire,
    # but the flag is preserved for the log line.
    assert plan.targets == []


def test_identify_scrubs_recording_specific_noise_from_constellation(fake_llm):
    """The runtime scorer rewards exact id, attr, and text matches. If we
    leave the recorded record's identifiers (a specific issue's href,
    its title, the React useId of its list slot) in the constellation,
    the scorer locks onto the *recorded* element on every run instead
    of finding the equivalent slot on the live page. Identify scrubs
    these aggressively."""
    payload = json.dumps({
        "is_retrieval": True,
        "reasoning": "title-fetch objective",
        "targets": [
            {
                "event_id": "x",
                "output_name": "issue_title",
                "output_description": "Title of the most-commented issue",
                "method": "browser_get_element_text",
                "attribute": None,
                "constellation": {
                    "tag": "a",
                    "id": "_r_1b_-list-view-node-_r_20_",
                    "classes": [
                        "Title-module__anchor__dBbYy",
                        "IssuePullRequestTitle-module__ListItemTitle_1__HZYnd",
                    ],
                    "attrs": {
                        "href": "/owner/repo/issues/18010",
                        "data-hovercard-url": "/owner/repo/issues/18010/hovercard",
                        "data-hovercard-type": "issue",
                        "data-testid": "issue-pr-title-link",
                    },
                    "text": (
                        "QQ Bot InlineKeyboard button UI for approval "
                        "(/approve /deny)"
                    ),
                    "ancestors": [
                        {
                            "tag": "li",
                            "id": "_r_1b_-list-view-node-_r_20_",
                            "classes": ["ListItem-module__listItem__wBJcm"],
                            "attrs": {"role": "listitem"},
                        },
                    ],
                },
            },
        ],
    })
    llm = fake_llm([payload])
    plan = identify_outputs(
        [_resolved()],
        "find the most-commented issue",
        "<html><body><a href='/owner/repo/issues/18010'>X</a></body></html>",
        llm,
    )
    target = plan.targets[0]
    constellation = target.constellation

    # React useId segments are redacted, not dropped — the surrounding
    # `-list-view-node-` tokens are stable structure worth keeping.
    # The runtime scorer glob-matches the `*` against any hash.
    assert constellation.id == "_r_*_-list-view-node-_r_*_"
    # CSS Module class names keep their stable `<Component>-module__<part>__`
    # prefix; only the trailing build-hash is wildcarded.
    assert constellation.classes == [
        "Title-module__anchor__*",
        "IssuePullRequestTitle-module__ListItemTitle_1__*",
    ]
    # Record-specific URL attrs scrubbed; stable data-testid kept.
    assert "href" not in constellation.attrs
    assert "data-hovercard-url" not in constellation.attrs
    assert constellation.attrs.get("data-testid") == "issue-pr-title-link"
    # The recorded specific issue title is too long to be a stable
    # label, so the scrubber drops it.
    assert constellation.text is None
    # Same redaction on the ancestor.
    assert constellation.ancestors[0]["id"] == "_r_*_-list-view-node-_r_*_"
    assert constellation.ancestors[0]["classes"] == ["ListItem-module__listItem__*"]
    assert constellation.ancestors[0]["attrs"] == {"role": "listitem"}


def test_identify_keeps_short_stable_labels_as_text(fake_llm):
    """A short text like a button caption is a stable label, not a
    captured value — keep it so the scorer can use it."""
    payload = json.dumps({
        "is_retrieval": True,
        "reasoning": "fetch the visible label",
        "targets": [
            {
                "event_id": "x",
                "output_name": "tab_label",
                "output_description": "Active tab label",
                "method": "browser_get_element_text",
                "constellation": {
                    "tag": "button",
                    "attrs": {"role": "tab"},
                    "text": "Comments",
                },
            },
        ],
    })
    llm = fake_llm([payload])
    plan = identify_outputs(
        [_resolved()], "get the tab label",
        "<html><body><button role='tab'>Comments</button></body></html>",
        llm,
    )
    assert plan.targets[0].constellation.text == "Comments"


def test_identify_does_not_invent_retrieval_for_action_objective(fake_llm):
    """When the verb is action-style, the LLM's `is_retrieval=false`
    stands. We don't want a 'star a repo' skill to grow an output."""
    payload = json.dumps({
        "is_retrieval": False,
        "targets": [],
        "reasoning": "action objective",
    })
    llm = fake_llm([payload])
    plan = identify_outputs(
        [_resolved()],
        "star a github repository",
        "<html><body><button>Star</button></body></html>",
        llm,
    )
    assert plan.is_retrieval is False
    assert plan.targets == []

import json

import pytest

from freu_cli.learn.models import NormalizedEvent
from freu_cli.learn.stages.resolve import (
    _is_hashed_class,
    _is_hashed_id,
    _prune_graph,
    _redact_hashed_class,
    _redact_hashed_id,
    resolve_constellations,
)


def _event_with_graph(**overrides) -> NormalizedEvent:
    defaults = dict(
        action="click_element",
        event_ids=["e1"],
        target="Star button",
        description="star it",
        target_self={
            "tag": "button",
            "id": None,
            "classes": ["btn-primary", "css-1abc23"],
            "attrs": {"data-action": "star", "aria-label": "Star"},
            "text": "Star",
            "x": 800, "y": 100, "w": 60, "h": 28, "x_rel": 0.6, "w_rel": 0.05,
        },
        target_ancestors=[
            {"tag": "html"},
            {"tag": "body"},
            {"tag": "main"},
            {"tag": "button", "attrs": {"data-action": "star"}, "text": "Star"},
        ],
        target_neighbors=[{"tag": "span", "text": "1,234"}],
        target_children=[{"tag": "svg"}],
        target_special={
            "role": "list", "tag": "ul", "classes": ["pagehead-actions"],
        },
    )
    defaults.update(overrides)
    return NormalizedEvent(**defaults)


# ---------------------------------------------------------------------------
# Deterministic pruning helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [
    "css-1abc23",
    "sc-AbCdEf",
    "jsx-12345",
    "_foo_bar",
    "MuiButton-root-abcdef1234",
    "reallyreallyreallylongclassnamethatwillneverbestablelol",
    # CSS Modules `<Component>-module__<part>__<hash>` shape — the
    # trailing 5-char alphanumeric is a Vite/Webpack content hash.
    "Title-module__anchor__dBbYy",
    "IssuePullRequestTitle-module__ListItemTitle_1__HZYnd",
    "ListView-module__ul__uMK30",
])
def test_prune_drops_obvious_hashed_classes(name):
    assert _is_hashed_class(name)


@pytest.mark.parametrize("name", [
    ":r3:",
    "radix-abc123",
    "headlessui-tab-1",
    "12345678-1234-1234-1234-123456789012",
    # React useId can also leak in `_r_<hex>_` segments anywhere in
    # the id (not just as the full string).
    "_r_1b_-list-view-container",
    "_r_1b_-list-view-node-_r_20_",
])
def test_prune_drops_react_useid_substring_ids(name):
    assert _is_hashed_id(name)


@pytest.mark.parametrize("name,expected", [
    # CSS Modules: keep the semantic prefix, wildcard the build hash.
    ("Title-module__anchor__dBbYy", "Title-module__anchor__*"),
    (
        "IssuePullRequestTitle-module__ListItemTitle_1__HZYnd",
        "IssuePullRequestTitle-module__ListItemTitle_1__*",
    ),
    ("ListView-module__ul__uMK30", "ListView-module__ul__*"),
    # Material-UI / styled-components-with-prefix shape.
    ("MuiButton-root-abcdef1234", "MuiButton-root-*"),
    # Stable class — unchanged.
    ("btn-primary", "btn-primary"),
    ("data-testid-anchor", "data-testid-anchor"),
])
def test_redact_class_keeps_stable_prefix(name, expected):
    """Redaction preserves the `<component>__<part>__` prefix and only
    wildcards the trailing build-hash. The runtime scorer treats `*` as
    a glob, so the redacted class still matches across rebuilds."""
    assert _redact_hashed_class(name) == expected


@pytest.mark.parametrize("name", [
    "css-1abc23",
    "sc-AbCdEf",
    "jsx-12345",
    "_foo_bar",
    "reallyreallyreallylongclassnamethatwillneverbestablelol",
])
def test_redact_class_drops_pure_hash(name):
    """When the entire class is a hash with no semantic prefix, drop it
    entirely — there's nothing left to wildcard against."""
    assert _redact_hashed_class(name) is None


@pytest.mark.parametrize("name,expected", [
    ("_r_1b_-list-view-container", "_r_*_-list-view-container"),
    ("_r_1b_-list-view-node-_r_20_", "_r_*_-list-view-node-_r_*_"),
    # Stable id — unchanged.
    ("repo-content-turbo-frame", "repo-content-turbo-frame"),
])
def test_redact_id_wildcards_react_useid_segments(name, expected):
    assert _redact_hashed_id(name) == expected


@pytest.mark.parametrize("name", [
    "btn-primary", "pagehead-actions", "tmp-mb-3", "flex-nowrap", "d-none",
])
def test_prune_keeps_semantic_classes(name):
    assert not _is_hashed_class(name)


@pytest.mark.parametrize("value", [
    ":r3:",
    "radix-xyz-123",
    "headlessui-button-42",
    "550e8400-e29b-41d4-a716-446655440000",
])
def test_prune_drops_hashed_ids(value):
    assert _is_hashed_id(value)


@pytest.mark.parametrize("value", ["js-repo-pjax-container", "main-content"])
def test_prune_keeps_stable_ids(value):
    assert not _is_hashed_id(value)


def test_prune_graph_strips_classes_ids_attrs_across_every_node():
    raw = {
        "tag": "button",
        "id": ":r5:",
        "classes": ["btn-primary", "css-1abc23"],
        "attrs": {"data-action": "star", "style": "color:red", "tabindex": "0"},
        "ancestors": [
            {"tag": "div", "classes": ["jsx-123"], "id": "main-content"},
        ],
        "neighbors": [
            {"tag": "span", "classes": ["text-sm", "sc-AbCdEf"]},
        ],
        "children": [
            {"tag": "svg", "classes": ["css-deadbeef"]},
        ],
        "special": {"role": "list", "tag": "ul", "classes": ["list-unstyled"]},
    }
    pruned = _prune_graph(raw)
    assert pruned["id"] is None
    assert pruned["classes"] == ["btn-primary"]
    assert pruned["attrs"] == {"data-action": "star"}
    assert pruned["ancestors"][0]["classes"] == []
    assert pruned["ancestors"][0]["id"] == "main-content"
    assert pruned["neighbors"][0]["classes"] == ["text-sm"]
    assert pruned["children"][0]["classes"] == []
    assert pruned["special"]["classes"] == ["list-unstyled"]


def test_prune_preserves_children_null_sentinel():
    raw = {"tag": "div", "classes": [], "attrs": {}, "children": None}
    pruned = _prune_graph(raw)
    assert pruned["children"] is None


# ---------------------------------------------------------------------------
# resolve_constellations
# ---------------------------------------------------------------------------


def test_resolve_sends_llm_pre_pruned_graph_and_keeps_llm_output(fake_llm):
    event = _event_with_graph()
    # LLM returns an even more aggressively pruned constellation: it
    # strips `btn-primary` (say the model judged it unstable in context).
    llm_response = {
        "tag": "button",
        "id": None,
        "classes": [],
        "attrs": {"data-action": "star", "aria-label": "Star"},
        "text": "Star",
        "ancestors": [
            {"tag": "html"}, {"tag": "body"}, {"tag": "main"},
            {"tag": "button", "attrs": {"data-action": "star"}, "text": "Star"},
        ],
        "neighbors": [{"tag": "span", "text": "1,234"}],
        "children": [{"tag": "svg"}],
        "special": {"role": "list", "tag": "ul", "classes": ["pagehead-actions"]},
    }
    llm = fake_llm([json.dumps(llm_response)])

    resolved = resolve_constellations([event], llm)
    c = resolved[0].constellation
    assert c is not None
    assert c.tag == "button"
    assert c.classes == []
    assert c.attrs == {"data-action": "star", "aria-label": "Star"}
    assert c.children == [{"tag": "svg"}]
    assert c.special == {
        "role": "list", "tag": "ul", "classes": ["pagehead-actions"],
    }


def test_resolve_round_trips_wildcarded_text(fake_llm):
    """LLM may rewrite `text` with `*` for runtime-varying spans —
    those wildcards must survive into the Constellation unchanged so
    the runtime scorer can honor them.
    """
    event = _event_with_graph()
    llm_response = {
        "tag": "button",
        "id": None,
        "classes": [],
        "attrs": {"data-action": "star"},
        "text": "Total: $* due",
        "ancestors": [
            {"tag": "html"}, {"tag": "body"}, {"tag": "main"},
            {"tag": "button", "attrs": {"data-action": "star"}},
        ],
        "neighbors": [{"tag": "span", "text": "Parcel #*"}],
        "children": [{"tag": "svg"}],
        "special": {"role": "list", "tag": "ul", "text": "*"},
    }
    llm = fake_llm([json.dumps(llm_response)])

    resolved = resolve_constellations([event], llm)
    c = resolved[0].constellation
    assert c is not None
    assert c.text == "Total: $* due"
    assert c.neighbors[0]["text"] == "Parcel #*"
    assert c.special["text"] == "*"


def test_resolve_falls_back_to_prepruned_graph_when_llm_errors(fake_llm):
    event = _event_with_graph()
    # LLM returns non-JSON. call_json retries once then raises LLMResponseError
    # on its second failure.
    llm = fake_llm(["not json", "still not json"])

    resolved = resolve_constellations([event], llm)
    c = resolved[0].constellation
    assert c is not None
    # Pre-prune already stripped css-1abc23 so we should see btn-primary only.
    assert c.classes == ["btn-primary"]
    assert c.attrs == {"data-action": "star", "aria-label": "Star"}


def test_resolve_falls_back_when_llm_returns_invalid_shape(fake_llm):
    event = _event_with_graph()
    llm = fake_llm([json.dumps({"not_a_constellation": True})])
    resolved = resolve_constellations([event], llm)
    c = resolved[0].constellation
    assert c is not None
    assert c.tag == "button"  # pre-pruned fallback preserved


def test_resolve_skips_non_target_bearing_actions(fake_llm):
    events = [
        NormalizedEvent(
            action="navigate_web", event_ids=["e2"],
            target="home", description="home page",
        ),
    ]
    llm = fake_llm([])  # would panic if called
    resolved = resolve_constellations(events, llm)
    assert resolved[0].constellation is None


def test_resolve_skips_target_bearing_events_with_no_context(fake_llm):
    events = [
        NormalizedEvent(
            action="click_element", event_ids=["e1"],
            target="orphan", description="no context",
        ),
    ]
    llm = fake_llm([])
    resolved = resolve_constellations(events, llm)
    assert resolved[0].constellation is None


def test_resolve_derives_target_from_ancestors_when_target_self_missing(fake_llm):
    """Back-fill path for capture dumps that only carry ancestors."""
    event = NormalizedEvent(
        action="click_element", event_ids=["e1"],
        target="button", description="",
        target_ancestors=[
            {"tag": "html"},
            {"tag": "button", "attrs": {"data-action": "star"}},
        ],
    )
    # LLM echoes the pre-pruned graph back unchanged.
    llm = fake_llm([json.dumps({
        "tag": "button",
        "attrs": {"data-action": "star"},
        "ancestors": [
            {"tag": "html"},
            {"tag": "button", "attrs": {"data-action": "star"}},
        ],
        "neighbors": [],
        "children": None,
        "special": None,
    })])
    resolved = resolve_constellations([event], llm)
    assert resolved[0].constellation is not None
    assert resolved[0].constellation.tag == "button"

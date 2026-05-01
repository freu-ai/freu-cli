"""DSL executor against a stub BrowserAdapter.

These tests cover the execute loop: parameter validation, output capture,
template rendering, for_each aggregation, and if-branch gating. No bridge,
no real browser.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from freu_cli.run.browser.base import BrowserAdapter, Target
from freu_cli.run.browser.browser_models import (
    BrowserDomNode,
    BrowserElementState,
    BrowserPageInfo,
)
from freu_cli.run.dsl_executor import DSLExecutor
from freu_cli.run.executor import run_file
from freu_cli.run.registry import build_builtin_registry
from freu_cli.run.renderer import TemplateRenderer
from freu_cli.run.workflow_loader import load_workflow_data


def _target(tag: str, **extras) -> dict:
    return {"tag": tag, **extras}


def _target_json(tag: str, **extras) -> str:
    """JSON-encoded constellation literal for embedding in DSL fixtures."""
    import json
    return json.dumps(_target(tag, **extras))


class StubBrowser(BrowserAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, tuple]] = []
        self._page_info = BrowserPageInfo(url="https://start", title="Start")
        self._text_map: dict[str, str] = {}

    def start(self) -> None: ...
    def close(self) -> None: ...

    def page_info(self) -> BrowserPageInfo:
        self.calls.append(("page_info", ()))
        return self._page_info

    def list_dom_nodes(self) -> Sequence[BrowserDomNode]:
        return []

    def open_url(self, url: str) -> None:
        self.calls.append(("open_url", (url,)))
        self._page_info = BrowserPageInfo(url=url, title=f"Title: {url}")

    def click(self, target: Target) -> None:
        self.calls.append(("click", (_freeze_target(target),)))

    def fill(self, target: Target, text: str) -> None:
        self.calls.append(("fill", (_freeze_target(target), text)))

    def screenshot(self, path: str) -> None: ...

    def element_state(self, target: Target) -> BrowserElementState:
        return BrowserElementState(exists=True, visible=True)

    def element_text(self, target: Target) -> str:
        tag = target.get("tag") if isinstance(target, Mapping) else None
        return self._text_map.get(tag or "", "fallback text")

    def element_attribute(self, target: Target, attribute: str) -> str:
        return "attr-value"

    def collect_hrefs(self, target: Target, href_contains: str) -> list[str]:
        return []

    def scroll(self, x: int, y: int) -> None:
        self.calls.append(("scroll", (x, y)))

    def wait_for_element_count_stable(
        self, target: Target, timeout_ms: int, settle_ms: int,
    ) -> int:
        return 0

    def press_key(self, target: Target, key: str) -> None:
        self.calls.append(("press_key", (_freeze_target(target), key)))


def _freeze_target(target: Target) -> tuple:
    """Convert a constellation dict into a hashable tuple for call logs."""
    if not isinstance(target, Mapping):
        return (target,)
    return tuple(sorted(target.items(), key=lambda kv: kv[0]))


def test_executor_runs_simple_open_click(capsys):
    browser = StubBrowser()
    registry = build_builtin_registry()
    loaded = load_workflow_data({
        "dsl": [
            {
                "method": "browser_open_url",
                "description": "Open the demo page.",
                "arguments": [{"name": "url", "value": "https://x"}],
            },
            {
                "method": "browser_click_element",
                "description": "Click the primary button.",
                "arguments": [{"name": "target", "value": {"tag": "button"}}],
            },
        ],
    }, registry=registry)

    executor = DSLExecutor(registry=registry, renderer=TemplateRenderer())
    executor.execute(loaded["steps"], {}, browser=browser)

    assert browser.calls == [
        ("open_url", ("https://x",)),
        ("click", (_freeze_target({"tag": "button"}),)),
    ]


def test_executor_captures_outputs_from_stub_browser():
    browser = StubBrowser()
    browser._text_map["h1"] = "Hello"
    registry = build_builtin_registry()
    loaded = load_workflow_data({
        "dsl": [
            {
                "method": "browser_get_element_text",
                "description": "Read the greeting heading.",
                "arguments": [{"name": "target", "value": {"tag": "h1"}}],
                "outputs": [{"name": "greeting", "value": "text"}],
            },
        ],
    }, registry=registry)
    context = {}
    executor = DSLExecutor(registry=registry, renderer=TemplateRenderer())
    executor.execute(loaded["steps"], context, browser=browser)
    assert context["greeting"] == "Hello"


def test_executor_renders_template_variables():
    browser = StubBrowser()
    registry = build_builtin_registry()
    loaded = load_workflow_data({
        "dsl": [
            {
                "method": "browser_open_url",
                "description": "Open the repository page.",
                "arguments": [{"name": "url", "value": "{{repo_url}}"}],
            },
        ],
    }, registry=registry)
    executor = DSLExecutor(registry=registry, renderer=TemplateRenderer())
    executor.execute(loaded["steps"], {"repo_url": "https://github.com"}, browser=browser)
    assert browser.calls == [("open_url", ("https://github.com",))]


def test_run_file_missing_input_reports_failure(tmp_path: Path):
    command_json = tmp_path / "X.json"
    command_json.write_text(
        '{"steps": [{"method": "browser_open_url",'
        ' "description": "Open the requested page.",'
        ' "arguments": [{"name": "url", "source": "missing"}]}]}'
    )
    result = run_file(command_json, inputs={})
    assert result["ok"] is False
    assert "Missing required inputs" in result["error"]


def test_run_file_with_browser_runtime_injected(tmp_path: Path):
    """run_file with an externally-supplied browser avoids the bridge."""
    command_json = tmp_path / "Y.json"
    command_json.write_text(
        '{"steps": ['
        '{"method": "browser_open_url",'
        ' "description": "Open the demo page.",'
        ' "arguments": [{"name": "url", "value": "https://demo"}]},'
        '{"method": "browser_click_element",'
        ' "description": "Click the primary button.",'
        ' "arguments": [{"name": "target", "value":'
        f' {_target_json("button")}'
        "}]}"
        "]}"
    )
    browser = StubBrowser()
    result = run_file(command_json, runtime={"browser": browser})
    assert result["ok"] is True
    assert browser.calls == [
        ("open_url", ("https://demo",)),
        ("click", (_freeze_target({"tag": "button"}),)),
    ]


def test_executor_for_each_iterates_items_and_aggregates(capsys):
    browser = StubBrowser()
    registry = build_builtin_registry()
    loaded = load_workflow_data({
        "dsl": [
            {
                "type": "for_each",
                "item_name": "url",
                "source": "{{urls}}",
                "result": "page_title",
                "output": "titles",
                "steps": [
                    {
                        "method": "browser_open_url",
                        "description": "Open the next URL in the list.",
                        "arguments": [{"name": "url", "value": "{{url}}"}],
                    },
                    {
                        "method": "browser_get_page_info",
                        "description": "Read the page title.",
                        "arguments": [],
                        "outputs": [{"name": "page_title", "value": "title"}],
                    },
                ],
            },
        ],
    }, registry=registry)
    context: dict = {"urls": ["https://a", "https://b"]}
    executor = DSLExecutor(registry=registry, renderer=TemplateRenderer())
    executor.execute(loaded["steps"], context, browser=browser)
    assert context["titles"] == ["Title: https://a", "Title: https://b"]


def test_executor_if_skips_when_condition_false():
    browser = StubBrowser()
    registry = build_builtin_registry()
    loaded = load_workflow_data({
        "dsl": [
            {
                "type": "if",
                "condition": {
                    "method": "value_is_true",
                    "description": "Check the gating flag.",
                    "arguments": [{"name": "value", "value": False}],
                    "outputs": [{"name": "ok", "value": "ok"}],
                },
                "steps": [
                    {
                        "method": "browser_open_url",
                        "description": "Open the gated page.",
                        "arguments": [{"name": "url", "value": "https://unreached"}],
                    },
                ],
            },
        ],
    }, registry=registry)
    executor = DSLExecutor(registry=registry, renderer=TemplateRenderer())
    executor.execute(loaded["steps"], {}, browser=browser)
    assert all(call[0] != "open_url" for call in browser.calls)


# ---------------------------------------------------------------------------
# Per-step descriptions: log output + failure reporting
# ---------------------------------------------------------------------------


def test_parser_extracts_description_onto_step():
    registry = build_builtin_registry()
    loaded = load_workflow_data({
        "dsl": [
            {
                "method": "browser_open_url",
                "description": "Open the demo page.",
                "arguments": [{"name": "url", "value": "https://x"}],
            },
        ],
    }, registry=registry)
    assert loaded["steps"][0].description == "Open the demo page."


def test_print_step_merges_description_and_action_detail(capsys):
    """The executor renders the description and the action's
    parameter-aware detail on a single line so the reader sees both
    domain intent and concrete call."""
    browser = StubBrowser()
    registry = build_builtin_registry()
    loaded = load_workflow_data({
        "dsl": [
            {
                "method": "browser_open_url",
                "description": "Open the GitHub home page.",
                "arguments": [{"name": "url", "value": "https://github.com"}],
            },
        ],
    }, registry=registry)
    executor = DSLExecutor(registry=registry, renderer=TemplateRenderer())
    executor.execute(loaded["steps"], {}, browser=browser)
    out = capsys.readouterr().out
    assert "Step 1: Open the GitHub home page. (Opening https://github.com)" in out
    assert out.count("Opening https://github.com") == 1


def test_loader_rejects_missing_description():
    import pytest

    from freu_cli.run.errors import DSLExecutionError

    registry = build_builtin_registry()
    with pytest.raises(DSLExecutionError, match="description"):
        load_workflow_data({
            "dsl": [
                {
                    "method": "browser_open_url",
                    "arguments": [{"name": "url", "value": "https://x"}],
                },
            ],
        }, registry=registry)


def test_executor_tracks_completed_descriptions_in_order():
    browser = StubBrowser()
    registry = build_builtin_registry()
    loaded = load_workflow_data({
        "dsl": [
            {
                "method": "browser_open_url",
                "description": "Open the home page.",
                "arguments": [{"name": "url", "value": "https://x"}],
            },
            {
                "method": "browser_click_element",
                "description": "Click the primary button.",
                "arguments": [{"name": "target", "value": {"tag": "button"}}],
            },
        ],
    }, registry=registry)
    executor = DSLExecutor(registry=registry, renderer=TemplateRenderer())
    executor.execute(loaded["steps"], {}, browser=browser)
    assert executor.completed_descriptions == [
        "Open the home page.",
        "Click the primary button.",
    ]


def test_failure_result_reports_completed_and_failed_descriptions(tmp_path: Path):
    """When a mid-flight step blows up, the result dict carries the
    descriptions of completed steps and the description of the step that
    failed — so a calling agent can recover with context."""
    command_json = tmp_path / "Boom.json"
    # Step 2 references an unknown variable, which fails param validation.
    command_json.write_text(
        '{"steps": ['
        '{"method": "browser_open_url",'
        ' "description": "Open the demo page.",'
        ' "arguments": [{"name": "url", "value": "https://demo"}]},'
        '{"method": "browser_click_element",'
        ' "description": "Click the missing button.",'
        ' "arguments": [{"name": "target", "value": {"tag": "button"}}]}'
        "]}"
    )
    browser = StubBrowser()

    # Make the second step blow up by replacing the click adapter.
    def boom(*_args, **_kwargs):
        raise RuntimeError("element not found")
    browser.click = boom  # type: ignore[method-assign]

    result = run_file(command_json, runtime={"browser": browser})
    assert result["ok"] is False
    assert result["completed_steps"] == ["Open the demo page."]
    assert result["failed_step"] == "Click the missing button."
    assert "element not found" in result["error"]


def test_failure_result_completed_steps_empty_when_first_step_fails(tmp_path: Path):
    command_json = tmp_path / "Boom.json"
    command_json.write_text(
        '{"steps": ['
        '{"method": "browser_open_url",'
        ' "description": "Open the demo page.",'
        ' "arguments": [{"name": "url", "value": "https://demo"}]}'
        "]}"
    )
    browser = StubBrowser()

    def boom(*_args, **_kwargs):
        raise RuntimeError("network down")
    browser.open_url = boom  # type: ignore[method-assign]

    result = run_file(command_json, runtime={"browser": browser})
    assert result["ok"] is False
    assert result["completed_steps"] == []
    assert result["failed_step"] == "Open the demo page."

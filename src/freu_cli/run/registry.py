from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from freu_cli.run.errors import DSLExecutionError
from freu_cli.run.models import CommandSpec


class CommandRegistry:
    """Browser-only command registry.

    Every registered method is implicitly browser-bound; there is no
    `execution_mode` field because vision/desktop/script methods are
    not part of this codebase.
    """

    def __init__(self) -> None:
        self._commands: dict[str, CommandSpec] = {}

    def register(
        self,
        name: str,
        func: Callable[..., Any],
        params: list[str],
        output_fields: list[str],
        description: str,
        allow_extra_params: bool = False,
    ) -> None:
        signature = inspect.signature(func)
        runtime_params = [
            parameter_name
            for parameter_name in signature.parameters
            if parameter_name not in params
        ]
        self._commands[name] = CommandSpec(
            func=func,
            params=params,
            runtime_params=runtime_params,
            output_fields=output_fields,
            description=description,
            allow_extra_params=allow_extra_params,
        )

    def get(self, name: str) -> CommandSpec:
        try:
            return self._commands[name]
        except KeyError as exc:
            raise DSLExecutionError(f"Unknown action: {name}") from exc

    def names(self) -> list[str]:
        return list(self._commands.keys())

    def items(self) -> list[tuple[str, CommandSpec]]:
        return list(self._commands.items())


def build_builtin_registry() -> CommandRegistry:
    """Register every browser-only built-in."""
    from freu_cli.run.actions.browser_actions import (
        browser_click_element,
        browser_collect_attribute,
        browser_fill_element,
        browser_get_element_attribute,
        browser_get_element_text,
        browser_get_page_info,
        browser_open_url,
        browser_press_key,
        browser_screenshot,
        browser_scroll,
        browser_verify_element,
        browser_verify_element_negated,
        browser_wait_for_element,
        browser_wait_for_element_count_stable,
        browser_wait_for_url_contains,
    )
    from freu_cli.run.actions.logic_actions import value_is_true

    registry = CommandRegistry()

    registry.register(
        "browser_open_url",
        browser_open_url,
        ["url"],
        output_fields=[],
        description="Open the target URL and wait until domcontentloaded",
    )
    registry.register(
        "browser_wait_for_element",
        browser_wait_for_element,
        ["target", "timeout"],
        output_fields=[],
        description="Wait until the best-matching element for a constellation becomes visible",
    )
    registry.register(
        "browser_verify_element",
        browser_verify_element,
        ["target"],
        output_fields=[],
        description="Assert the constellation resolves to a DOM node",
    )
    registry.register(
        "browser_verify_element_negated",
        browser_verify_element_negated,
        ["target"],
        output_fields=[],
        description="Assert the constellation does NOT resolve to a DOM node",
    )
    registry.register(
        "browser_fill_element",
        browser_fill_element,
        ["target", "text"],
        output_fields=[],
        description="Fill text into the element best matching a constellation",
    )
    registry.register(
        "browser_click_element",
        browser_click_element,
        ["target"],
        output_fields=[],
        description="Click the element best matching a constellation",
    )
    registry.register(
        "browser_press_key",
        browser_press_key,
        ["target", "key"],
        output_fields=[],
        description="Send a key press on the element best matching a constellation",
    )
    registry.register(
        "browser_wait_for_url_contains",
        browser_wait_for_url_contains,
        ["text", "timeout"],
        output_fields=[],
        description="Wait until the current URL contains the specified substring",
    )
    registry.register(
        "browser_scroll",
        browser_scroll,
        ["x", "y", "times"],
        output_fields=[],
        description="Scroll the page by the given pixel offsets",
    )
    registry.register(
        "browser_wait_for_element_count_stable",
        browser_wait_for_element_count_stable,
        ["target", "timeout", "settle_time"],
        output_fields=[],
        description="Wait until the number of matches for a constellation stops changing for a settle period",
    )
    registry.register(
        "browser_screenshot",
        browser_screenshot,
        ["path"],
        output_fields=[],
        description="Save a full-page screenshot; directories are created automatically",
    )
    registry.register(
        "browser_get_element_text",
        browser_get_element_text,
        ["target"],
        output_fields=["text"],
        description="Return the text content of the best-matching element",
    )
    registry.register(
        "browser_get_page_info",
        browser_get_page_info,
        [],
        output_fields=["title", "url"],
        description="Return the current page title and URL",
    )
    registry.register(
        "browser_collect_attribute",
        browser_collect_attribute,
        ["target", "attribute", "value_contains", "resolve_urls"],
        output_fields=["values"],
        description="Collect attribute values from every element matching a constellation",
    )
    registry.register(
        "browser_get_element_attribute",
        browser_get_element_attribute,
        ["target", "attribute"],
        output_fields=["value"],
        description="Return the value of a named attribute from the best-matching element",
    )
    registry.register(
        "value_is_true",
        value_is_true,
        ["value"],
        output_fields=["ok"],
        description="Normalize a boolean-like value into a boolean result",
    )
    return registry

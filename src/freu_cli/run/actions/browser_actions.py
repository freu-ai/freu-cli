from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from freu_cli.run.browser.base import BrowserAdapter, Target
from freu_cli.run.errors import DSLExecutionError

_POST_ACTION_DELAY_S = 0.5


def _coerce_target(target: Any) -> Target:
    """Every target-bearing action receives its `target` argument as a
    constellation dict. Anything else is a pipeline bug and we fail
    fast with a clean message."""
    if isinstance(target, Mapping):
        tag = target.get("tag")
        if not isinstance(tag, str) or not tag.strip():
            raise DSLExecutionError(
                "target is a dict but missing a non-empty 'tag' key — "
                "constellations must have `tag`."
            )
        return target
    raise DSLExecutionError(
        f"target must be a constellation dict, got {type(target).__name__}."
    )


def _target_label(target: Any) -> str:
    if not isinstance(target, Mapping):
        return f"<{type(target).__name__}>"
    tag = str(target.get("tag") or "?")
    text = target.get("text")
    if isinstance(text, str) and text:
        return f"<{tag}> '{text[:40]}'"
    return f"<{tag}>"


def describe_action(method: str, params: dict[str, Any]) -> str | None:
    """Return a one-line, parameter-aware summary of a browser action.

    The DSL executor prints this in parentheses after the step's
    `description`, so the operator sees both *what the step is doing in
    domain terms* and *what concrete call is about to fire*. Returning
    `None` opts out (the executor falls back to method-name formatting).
    """
    target = params.get("target")
    if method == "browser_open_url":
        return f"Opening {params.get('url')}"
    if method == "browser_wait_for_element":
        return f"Waiting for {_target_label(target)}"
    if method == "browser_verify_element":
        return f"Verifying {_target_label(target)}"
    if method == "browser_verify_element_negated":
        return f"Verifying absent {_target_label(target)}"
    if method == "browser_fill_element":
        return f"Filling {_target_label(target)} with {params.get('text')}"
    if method == "browser_click_element":
        return f"Clicking {_target_label(target)}"
    if method == "browser_press_key":
        return f"Pressing {params.get('key')} on {_target_label(target)}"
    if method == "browser_wait_for_url_contains":
        return f"Waiting for URL to contain {params.get('text')}"
    if method == "browser_screenshot":
        return f"Saving screenshot to {params.get('path')}"
    if method == "browser_get_page_info":
        return "Reading current page info"
    if method == "browser_get_element_text":
        return f"Reading text from {_target_label(target)}"
    if method == "browser_wait_for_element_count_stable":
        return (
            f"Waiting for match count to stabilize: {_target_label(target)} "
            f"(timeout={params.get('timeout')}ms, settle={params.get('settle_time')}ms)"
        )
    if method == "browser_scroll":
        return f"Scrolling by ({params.get('x')}, {params.get('y')}) x{params.get('times', 1)}"
    if method == "browser_get_element_attribute":
        return f"Getting attribute {params.get('attribute')!r} from {_target_label(target)}"
    if method == "browser_collect_attribute":
        return f"Collecting {params.get('attribute')!r} values from {_target_label(target)}"
    return None


def browser_open_url(browser: BrowserAdapter, url: str) -> None:
    browser.open_url(url)
    time.sleep(_POST_ACTION_DELAY_S)


def browser_wait_for_element(
    browser: BrowserAdapter, target: Any, timeout: int,
) -> None:
    target = _coerce_target(target)
    browser.wait_for_element(target, int(timeout))


def browser_verify_element(browser: BrowserAdapter, target: Any) -> None:
    target = _coerce_target(target)
    if not browser.element_state(target).exists:
        raise DSLExecutionError(f"Element not found: {_target_label(target)}")


def browser_verify_element_negated(browser: BrowserAdapter, target: Any) -> None:
    target = _coerce_target(target)
    if browser.element_state(target).exists:
        raise DSLExecutionError(f"Element unexpectedly present: {_target_label(target)}")


def browser_fill_element(browser: BrowserAdapter, target: Any, text: str) -> None:
    target = _coerce_target(target)
    browser.fill(target, text)
    time.sleep(_POST_ACTION_DELAY_S)


def browser_click_element(browser: BrowserAdapter, target: Any) -> None:
    target = _coerce_target(target)
    browser.click(target)
    time.sleep(_POST_ACTION_DELAY_S)


def browser_press_key(browser: BrowserAdapter, target: Any, key: str) -> None:
    target = _coerce_target(target)
    browser.press_key(target, key)
    time.sleep(_POST_ACTION_DELAY_S)


def browser_wait_for_url_contains(
    browser: BrowserAdapter, text: str, timeout: int,
) -> None:
    browser.wait_for_url_contains(text, int(timeout))


def browser_screenshot(browser: BrowserAdapter, path: str) -> None:
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    browser.screenshot(str(target_path))


def browser_get_page_info(browser: BrowserAdapter) -> dict[str, str]:
    page_info = browser.page_info()
    title = page_info.title.strip()
    url = page_info.url.strip()
    if not url:
        raise DSLExecutionError("Current page URL is empty")
    return {"title": title, "url": url}


def browser_get_element_text(browser: BrowserAdapter, target: Any) -> dict[str, str]:
    target = _coerce_target(target)
    text = browser.element_text(target).strip()
    normalized_text = " ".join(text.split()).strip()
    if not normalized_text:
        raise DSLExecutionError(f"Element text is empty: {_target_label(target)}")
    return {"text": normalized_text}


def browser_wait_for_element_count_stable(
    browser: BrowserAdapter, target: Any, timeout: int, settle_time: int,
) -> None:
    target = _coerce_target(target)
    timeout_ms = int(timeout)
    settle_ms = int(settle_time)
    count = browser.wait_for_element_count_stable(target, timeout_ms, settle_ms)
    print(f"Match count stabilized at {count}")


def browser_scroll(browser: BrowserAdapter, x: int, y: int, times: int = 1) -> None:
    count = max(1, int(times))
    for _ in range(count):
        browser.scroll(int(x), int(y))
        time.sleep(0.3)
    time.sleep(_POST_ACTION_DELAY_S)


def browser_collect_attribute(
    browser: BrowserAdapter,
    target: Any,
    attribute: str,
    value_contains: str = "",
    resolve_urls: bool = False,
) -> dict[str, list[str]]:
    target = _coerce_target(target)
    normalized_attribute = str(attribute or "").strip()
    normalized_filter = str(value_contains or "").strip()
    if not normalized_attribute:
        raise DSLExecutionError("attribute is required")

    base_url = browser.page_info().url.strip() if resolve_urls else ""

    if normalized_attribute == "href":
        raw_values = list(browser.collect_hrefs(target, normalized_filter))
    else:
        # Generic path: use element_attribute on each matched element —
        # but collect_hrefs is the only adapter-level bulk op for now, so
        # fall through to a conservative single-element read when the
        # caller asks for a non-href attribute. (Future: add a bulk
        # `collect_attribute` adapter method.)
        value = browser.element_attribute(target, normalized_attribute).strip()
        raw_values = [value] if value else []

    collected: list[str] = []
    for raw_value in raw_values:
        if normalized_filter and normalized_filter not in raw_value:
            continue
        value = urljoin(base_url, raw_value) if base_url else raw_value
        if value and value not in collected:
            collected.append(value)
    return {"values": collected}


def browser_get_element_attribute(
    browser: BrowserAdapter, target: Any, attribute: str,
) -> dict[str, str]:
    target = _coerce_target(target)
    normalized_attribute = str(attribute or "").strip()
    if not normalized_attribute:
        raise DSLExecutionError("attribute is required")
    value = browser.element_attribute(target, normalized_attribute).strip()
    if not value:
        raise DSLExecutionError(
            f"No attribute {normalized_attribute!r} on element matching {_target_label(target)}"
        )
    return {"value": value}

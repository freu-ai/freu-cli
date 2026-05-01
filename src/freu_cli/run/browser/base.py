from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any

from freu_cli.run.browser.browser_models import (
    BrowserDomNode,
    BrowserElementState,
    BrowserPageInfo,
    BrowserSessionConfig,
)

# A constellation is an opaque (to the adapter) dict describing the target
# element and its surroundings; the page-context scorer picks the best
# live match.
Target = Mapping[str, Any]


class BrowserAdapter(AbstractContextManager["BrowserAdapter"], ABC):
    """Backend-agnostic browser operations for skill execution."""

    def __init__(self, config: BrowserSessionConfig | None = None) -> None:
        self.config = config or BrowserSessionConfig()

    def __enter__(self) -> BrowserAdapter:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def page_info(self) -> BrowserPageInfo: ...

    @abstractmethod
    def list_dom_nodes(self) -> Sequence[BrowserDomNode]: ...

    @abstractmethod
    def open_url(self, url: str) -> None: ...

    @abstractmethod
    def click(self, target: Target) -> None: ...

    @abstractmethod
    def fill(self, target: Target, text: str) -> None: ...

    @abstractmethod
    def screenshot(self, path: str) -> None: ...

    @abstractmethod
    def element_state(self, target: Target) -> BrowserElementState: ...

    @abstractmethod
    def element_text(self, target: Target) -> str: ...

    @abstractmethod
    def element_attribute(self, target: Target, attribute: str) -> str: ...

    @abstractmethod
    def collect_hrefs(self, target: Target, href_contains: str) -> list[str]: ...

    @abstractmethod
    def scroll(self, x: int, y: int) -> None: ...

    @abstractmethod
    def wait_for_element_count_stable(
        self, target: Target, timeout_ms: int, settle_ms: int,
    ) -> int: ...

    @abstractmethod
    def press_key(self, target: Target, key: str) -> None: ...

    def wait_for_element(self, target: Target, timeout_ms: int) -> None:
        deadline = time.time() + (timeout_ms / 1000)
        while time.time() < deadline:
            state = self.element_state(target)
            if state.visible:
                return
            time.sleep(0.2)
        tag = target.get("tag") if isinstance(target, Mapping) else "?"
        raise RuntimeError(f"Element not visible before timeout: <{tag}>")

    def wait_for_url_contains(self, text: str, timeout_ms: int) -> None:
        deadline = time.time() + (timeout_ms / 1000)
        while time.time() < deadline:
            if text in self.page_info().url:
                return
            time.sleep(0.2)
        raise RuntimeError(f"URL did not contain '{text}' before timeout")

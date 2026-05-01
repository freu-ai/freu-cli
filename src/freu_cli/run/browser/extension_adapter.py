from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from freu_cli.run.browser.base import BrowserAdapter, Target
from freu_cli.run.browser.bridge_manager import bridge_base_url, ensure_bridge_running
from freu_cli.run.browser.browser_models import (
    BrowserDomNode,
    BrowserElementState,
    BrowserPageInfo,
)
from freu_cli.run.browser.resolve_js import RESOLVE_JS


class ChromeExtensionBrowserAdapter(BrowserAdapter):
    """Browser adapter that drives Chrome via the freu bridge + extension.

    Commands are sent over HTTP to the local bridge; the extension long-polls
    for commands, executes them via the Chrome Debugger API, and posts the
    result back. No Playwright, no CDP socket — just HTTP.

    Every element-touching method injects `RESOLVE_JS` so the page-context
    scorer can pick the best match from a learned constellation.
    """

    def __init__(self, config=None) -> None:
        super().__init__(config=config)
        self._bridge_url = bridge_base_url()
        self._opener = build_opener(ProxyHandler({}))

    def start(self) -> None:
        ensure_bridge_running()
        self._request("GET", "/healthz")
        self._wait_for_extension_connection()

    def close(self) -> None:
        return

    def page_info(self) -> BrowserPageInfo:
        payload = self._request("GET", "/page")
        return BrowserPageInfo(
            url=str(payload.get("url", "")),
            title=str(payload.get("title", "")),
        )

    def list_dom_nodes(self) -> Sequence[BrowserDomNode]:
        return []

    def open_url(self, url: str) -> None:
        self._request("POST", "/navigate", {"url": url})

    def click(self, target: Target) -> None:
        time.sleep(0.5)
        # Dispatch the full pointerdown/mousedown → pointerup/mouseup → click
        # sequence. `el.click()` alone fires only the click event, which
        # silently misses pages whose handlers bind to mousedown — common
        # in autocomplete dropdowns that commit the selection before the
        # input's blur handler tears the menu down.
        self._resolve_and_eval(
            target,
            """
            const r = el.getBoundingClientRect();
            const cx = r.left + r.width / 2;
            const cy = r.top + r.height / 2;
            const opts = {
              bubbles: true, cancelable: true, composed: true,
              view: window, button: 0, buttons: 1,
              clientX: cx, clientY: cy,
            };
            const PointerEv = window.PointerEvent || window.MouseEvent;
            try { el.dispatchEvent(new PointerEv("pointerdown", opts)); } catch (_e) {}
            el.dispatchEvent(new MouseEvent("mousedown", opts));
            try { el.dispatchEvent(new PointerEv("pointerup", opts)); } catch (_e) {}
            el.dispatchEvent(new MouseEvent("mouseup", opts));
            el.dispatchEvent(new MouseEvent("click", { ...opts, buttons: 0 }));
            return { ok: true };
            """,
        )

    def fill(self, target: Target, text: str) -> None:
        self._resolve_and_eval(
            target,
            """
            el.focus();
            el.value = arguments[1];
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
            return { ok: true };
            """,
            text,
        )

    def screenshot(self, path: str) -> None:
        self._request("POST", "/screenshot", {"path": path})

    def element_state(self, target: Target) -> BrowserElementState:
        result = self._cdp_eval(
            RESOLVE_JS + """
            const el = window.__freuResolve(arguments[0]);
            if (!el) return { exists: false, visible: false };
            const visible = !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
            return { exists: true, visible };
            """,
            dict(target),
        )
        return BrowserElementState(
            exists=bool(result.get("exists", False)) if isinstance(result, dict) else False,
            visible=bool(result.get("visible", False)) if isinstance(result, dict) else False,
        )

    def element_text(self, target: Target) -> str:
        result = self._resolve_and_eval(
            target,
            "return { text: (el.innerText || el.textContent || \"\") };",
        )
        return str(result.get("text", "")) if isinstance(result, dict) else ""

    def element_attribute(self, target: Target, attribute: str) -> str:
        result = self._resolve_and_eval(
            target,
            "return { value: (el.getAttribute(arguments[1]) || \"\") };",
            attribute,
        )
        return str(result.get("value", "")) if isinstance(result, dict) else ""

    def collect_hrefs(self, target: Target, href_contains: str) -> list[str]:
        result = self._cdp_eval(
            RESOLVE_JS + """
            const els = window.__freuResolveAll(arguments[0], { limit: 128 }) || [];
            const text = String(arguments[1] || "");
            const urls = els
              .map((el) => el.getAttribute("href") || "")
              .filter((href) => href && href.includes(text));
            return { urls };
            """,
            dict(target), href_contains,
        )
        urls = result.get("urls") if isinstance(result, dict) else None
        if not isinstance(urls, list):
            return []
        return [str(item).strip() for item in urls if str(item).strip()]

    def scroll(self, x: int, y: int) -> None:
        self._cdp_eval(
            "window.scrollBy(arguments[0], arguments[1]); return { ok: true };",
            x, y,
        )

    def wait_for_element_count_stable(
        self, target: Target, timeout_ms: int, settle_ms: int,
    ) -> int:
        timeout = max(timeout_ms, 0) / 1000
        settle = max(settle_ms, 0) / 1000
        deadline = time.time() + timeout
        last_count = -1
        stable_since = time.time()
        while time.time() < deadline:
            count = self._cdp_eval(
                RESOLVE_JS + "return (window.__freuResolveAll(arguments[0], { limit: 512 }) || []).length;",
                dict(target),
            )
            count = int(count) if isinstance(count, (int, float)) else 0
            if count != last_count:
                last_count = count
                stable_since = time.time()
            elif time.time() - stable_since >= settle:
                return count
            time.sleep(0.3)
        return max(last_count, 0)

    def press_key(self, target: Target, key: str) -> None:
        self._resolve_and_eval(
            target,
            """
            el.focus();
            const key = String(arguments[1] || "");
            el.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
            el.dispatchEvent(new KeyboardEvent("keyup", { key, bubbles: true }));
            if (key.toLowerCase() === "enter") {
              const form = el.form;
              if (form) form.requestSubmit ? form.requestSubmit() : form.submit();
            }
            return { ok: true };
            """,
            key,
        )

    # ----- internals -----

    # How long actions like click/fill/press_key will poll for the target
    # to resolve before giving up. Page transitions — especially on SPAs —
    # commonly take > 0.5s; without this implicit wait every action would
    # race the DOM and fail on first use.
    _RESOLVE_POLL_TIMEOUT_MS = 3000
    _RESOLVE_POLL_STEP_S = 0.2

    def _wait_for_resolve(self, target: Target, timeout_ms: int | None = None) -> None:
        """Poll until `window.__freuResolve(target)` returns non-null or
        the timeout expires. Silent on timeout — the following eval will
        throw a descriptive 'no matching element' error if nothing showed.
        """
        budget_ms = self._RESOLVE_POLL_TIMEOUT_MS if timeout_ms is None else int(timeout_ms)
        if budget_ms <= 0:
            return
        deadline = time.time() + budget_ms / 1000
        while time.time() < deadline:
            result = self._cdp_eval(
                RESOLVE_JS + "return { hit: !!window.__freuResolve(arguments[0]) };",
                dict(target),
            )
            if isinstance(result, dict) and result.get("hit"):
                return
            time.sleep(self._RESOLVE_POLL_STEP_S)

    def _resolve_and_eval(self, target: Target, body_after_el: str, *args: Any) -> Any:
        """Run `body_after_el` in page context with a preset `el` bound to
        the best-matching constellation candidate, or throw a descriptive
        error when no candidate meets the minimum score. Polls briefly
        before acting so SPA render delays don't cause spurious failures.
        """
        self._wait_for_resolve(target)
        js = (
            RESOLVE_JS
            + "const el = window.__freuResolve(arguments[0]);"
            + "if (!el) throw new Error("
            + "'No matching element for <' + (arguments[0].tag || '?') + '>'"
            + " + (arguments[0].text ? (\" with text \\\"\" + String(arguments[0].text).slice(0, 40) + \"\\\"\") : \"\"));\n"
            + body_after_el
        )
        return self._cdp_eval(js, dict(target), *args)

    def _cdp(self, method: str, params: dict | None = None) -> dict:
        payload = self._request("POST", "/cdp", {"method": method, "params": params or {}})
        return payload.get("result", {}) if isinstance(payload, dict) else {}

    def _cdp_eval(self, body: str, *args: object) -> object:
        # json.dumps a Mapping->dict so constellation objects serialize cleanly.
        args_payload = [dict(a) if isinstance(a, Mapping) else a for a in args]
        args_json = json.dumps(args_payload, ensure_ascii=False)
        expression = f"(function() {{ const arguments = {args_json};\n{body}\n}})()"
        result = self._cdp("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": False,
        })
        if "exceptionDetails" in result:
            desc = result["exceptionDetails"].get("text", "")
            exception = result["exceptionDetails"].get("exception", {})
            message = exception.get("description", "") or exception.get("value", "") or desc
            raise RuntimeError(f"CDP eval error: {message}")
        inner = result.get("result", {})
        return inner.get("value")

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = None
        headers: dict[str, str] = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(f"{self._bridge_url}{path}", data=body, method=method, headers=headers)
        try:
            with self._opener.open(request, timeout=30) as response:
                raw = response.read().decode("utf-8").strip()
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError(
                "Chrome extension bridge is unavailable. "
                f"Request: {method} {self._bridge_url}{path}. "
                f"Original error: {exc}"
            ) from exc
        return json.loads(raw) if raw else {}

    def _wait_for_extension_connection(self) -> None:
        deadline = time.time() + 30
        last_poll_at = ""
        while time.time() < deadline:
            status = self._request("GET", "/status")
            if status.get("extension_connected"):
                return
            last_poll_at = str(status.get("last_poll_at") or "").strip()
            time.sleep(0.5)
        details = f" Last poll: {last_poll_at}." if last_poll_at else ""
        raise RuntimeError(
            "Chrome extension bridge is running, but the Chrome extension is not connected."
            " Load the freu Chrome extension; it should reconnect automatically."
            f"{details}"
        )

from freu_cli.run.registry import build_builtin_registry

EXPECTED_BROWSER_METHODS = {
    "browser_open_url",
    "browser_wait_for_element",
    "browser_verify_element",
    "browser_verify_element_negated",
    "browser_fill_element",
    "browser_click_element",
    "browser_press_key",
    "browser_wait_for_url_contains",
    "browser_scroll",
    "browser_wait_for_element_count_stable",
    "browser_screenshot",
    "browser_get_element_text",
    "browser_get_page_info",
    "browser_collect_attribute",
    "browser_get_element_attribute",
    "value_is_true",
}


def test_registry_contains_only_browser_and_logic_methods():
    registry = build_builtin_registry()
    names = set(registry.names())
    assert names == EXPECTED_BROWSER_METHODS


def test_registry_rejects_vision_or_desktop_methods():
    """Sanity check: none of the stripped executor methods leaked back in."""
    registry = build_builtin_registry()
    for forbidden in ("vision_left_click", "app_activate", "run_script", "download_url"):
        try:
            registry.get(forbidden)
        except Exception:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"{forbidden} should not be registered")

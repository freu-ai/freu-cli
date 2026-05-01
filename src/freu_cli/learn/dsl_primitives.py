"""Allowed DSL methods and their required argument names.

Single source of truth for:
  - `learn/validate.py` (verify the LLM did not hallucinate a method)
  - `learn/prompts/synthesize.txt` (render an inline reference in the prompt)
  - documentation
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MethodSchema:
    name: str
    required_args: tuple[str, ...]
    optional_args: tuple[str, ...] = ()
    output_fields: tuple[str, ...] = ()
    description: str = ""


METHOD_SCHEMAS: tuple[MethodSchema, ...] = (
    MethodSchema(
        "browser_open_url", ("url",),
        description="Open a URL in the active tab.",
    ),
    MethodSchema(
        "browser_click_element", ("target",),
        description="Click the element best matching a constellation.",
    ),
    MethodSchema(
        "browser_fill_element", ("target", "text"),
        description="Fill a form field with text.",
    ),
    MethodSchema(
        "browser_press_key", ("target", "key"),
        description="Send a keydown/keyup on a target. Useful for Enter to submit.",
    ),
    MethodSchema(
        "browser_wait_for_element", ("target", "timeout"),
        description="Wait until a constellation resolves to a visible element; timeout is milliseconds.",
    ),
    MethodSchema(
        "browser_verify_element", ("target",),
        description="Assert a constellation resolves to a DOM node; fail if not.",
    ),
    MethodSchema(
        "browser_verify_element_negated", ("target",),
        description="Assert a constellation resolves to no matching DOM node.",
    ),
    MethodSchema(
        "browser_wait_for_url_contains", ("text", "timeout"),
        description="Wait until the current URL contains a substring.",
    ),
    MethodSchema(
        "browser_wait_for_element_count_stable",
        ("target", "timeout", "settle_time"),
        description="Wait until the number of matches for a constellation stops changing.",
    ),
    MethodSchema(
        "browser_scroll", ("x", "y"), ("times",),
        description="Scroll the page by (x, y) pixels, optionally `times` times.",
    ),
    MethodSchema(
        "browser_get_element_text", ("target",), output_fields=("text",),
        description="Return the visible text of the best-matching element.",
    ),
    MethodSchema(
        "browser_get_page_info", (), output_fields=("title", "url"),
        description="Return the current page title and URL.",
    ),
    MethodSchema(
        "browser_get_element_attribute", ("target", "attribute"),
        output_fields=("value",),
        description="Return the value of a DOM attribute on the best-matching element.",
    ),
    MethodSchema(
        "browser_collect_attribute",
        ("target", "attribute"), ("value_contains", "resolve_urls"),
        output_fields=("values",),
        description="Collect attribute values from every matching element (filtered/resolved).",
    ),
)


ALLOWED_METHODS: frozenset[str] = frozenset(m.name for m in METHOD_SCHEMAS)

# Methods whose DSL step carries a `target` (constellation) argument.
# Synthesize binds the constellation in after the LLM call; the validator
# requires it; the runtime registry dispatches it to the browser adapter.
TARGET_BEARING_METHODS: frozenset[str] = frozenset(
    m.name for m in METHOD_SCHEMAS if "target" in m.required_args
)


def method_schema(name: str) -> MethodSchema | None:
    for schema in METHOD_SCHEMAS:
        if schema.name == name:
            return schema
    return None


def render_method_reference() -> str:
    """Produce the short method reference block injected into the synthesize prompt."""
    lines: list[str] = []
    for schema in METHOD_SCHEMAS:
        args = ", ".join(schema.required_args)
        if schema.optional_args:
            args += " [" + ", ".join(schema.optional_args) + "]"
        outputs = (" -> {" + ", ".join(schema.output_fields) + "}") if schema.output_fields else ""
        lines.append(f"- {schema.name}({args}){outputs}: {schema.description}")
    return "\n".join(lines)

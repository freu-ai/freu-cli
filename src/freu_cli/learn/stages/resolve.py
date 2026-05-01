"""Resolve stage: prune a target's raw graph into a stable Constellation.

Each target-bearing NormalizedEvent carries the full captured context for
its click / input: the target element itself plus its ancestor chain,
nearby neighbors, direct children (when few), and a tag-specific special
anchor (label for inputs, select for options, …).

This stage's job is NOT to pick a CSS selector; it's to *prune* every
node in the graph down to signals that survive across page loads. We do
this in two passes:

1. Deterministic pre-prune — drop classes/ids/attrs whose shape is
   obviously auto-generated (hashed framework classes, React useId ids,
   inline styles, …).
2. LLM prune — have the language model remove anything the regexes
   missed (idiosyncratic per-deploy classes, noisy attrs) while keeping
   semantic signals (`role`, `aria-label`, `data-action`, `href`, …).

If the LLM call or its parse fails, we fall back to the pre-pruned graph
rather than failing the pipeline. The runtime scorer is tolerant of
residual noise — a bit of junk in the constellation just lowers the
winning candidate's absolute score, not its relative lead.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from freu_cli.learn.errors import LLMResponseError
from freu_cli.learn.llm_client import LLMClient
from freu_cli.learn.models import Constellation, NormalizedEvent, ResolvedEvent
from freu_cli.learn.stages._prompts import load_prompt

ProgressCallback = Callable[[str], None]

_SYSTEM_PROMPT = load_prompt("resolve.txt")

# Actions that reference a DOM target — same set as before.
_TARGET_BEARING_ACTIONS = {
    "click_element", "type_text", "press_key",
}

# Obvious auto-generated class/id shapes whose ENTIRE string is hash —
# nothing semantic to keep, drop them.
_PURE_HASH_CLASS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^css-[a-z0-9]{4,}$"),
    re.compile(r"^sc-[A-Za-z0-9]+$"),
    re.compile(r"^jsx-\d+$"),
    re.compile(r"^_[A-Za-z0-9]+_[A-Za-z0-9]+$"),  # legacy CSS Modules `_foo_bar`
)
_HEX_RUN_RE = re.compile(r"[0-9a-fA-F]{5,}")

_PURE_HASH_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^:r[0-9a-z]+:$"),  # React useId classic shape `:r3:`
    re.compile(r"^radix-[a-z0-9-]+$"),
    re.compile(r"^headlessui-[a-z0-9-]+$"),
    re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
        r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    ),  # UUID
)

# Patterns whose hash portion can be REDACTED to a `*` wildcard while
# keeping the stable semantic prefix. The runtime scorer treats class /
# id / attr values containing `*` as glob patterns, so a redacted
# `Title-module__anchor__*` still matches the same component on a live
# page where the bundler emitted a different content hash.
#
# Each entry is `(regex, group_template)` — `regex` must capture the
# stable prefix in group 1; `group_template` is the redacted output
# in re.sub-style backref syntax.
_REDACT_CLASS_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # CSS Modules: `<Component>-module__<part>__<hash>` → keep through `__`.
    (re.compile(r"^([A-Za-z][\w-]*-module__[\w-]+__)[A-Za-z0-9_-]{4,8}$"), r"\1*"),
    # Material-UI / styled-components: `<Component>-<part>-<hashlooking>`.
    # The hash MUST mix letters and digits (a pure-letter trailing token like
    # `data-testid-anchor` is a real class name, not a hash).
    (
        re.compile(
            r"^([A-Za-z]\w+-[a-z][\w-]*-)"
            r"(?=[A-Za-z]*\d)(?=\d*[A-Za-z])[A-Za-z0-9]{6,}$"
        ),
        r"\1*",
    ),
)

# Substrings inside ids that can be redacted to `_r_*_`.
_REDACT_ID_SUBSTRING_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"_r_[0-9a-z]+_"), "_r_*_"),  # React useId segments
)

# Attrs we always drop because their value is inherently per-session.
_DROP_ATTRS: frozenset[str] = frozenset({"style", "aria-activedescendant"})


def resolve_constellations(
    normalized_events: list[NormalizedEvent],
    llm: LLMClient,
    *,
    on_progress: ProgressCallback | None = None,
) -> list[ResolvedEvent]:
    """Return a ResolvedEvent for each input, with constellation filled
    in for target-bearing events that carry enough context to describe
    the target.

    `on_progress`, when set, is called once per target-bearing event with
    a one-line summary of how it resolved — used by the CLI to narrate
    the (LLM-bound) per-event prune.
    """
    log: ProgressCallback = on_progress or (lambda _msg: None)
    target_total = sum(
        1 for event in normalized_events if event.action in _TARGET_BEARING_ACTIONS
    )
    seen = 0

    resolved_list: list[ResolvedEvent] = []
    for index, event in enumerate(normalized_events):
        resolved_event = ResolvedEvent.model_validate(event.model_dump())
        if event.action not in _TARGET_BEARING_ACTIONS:
            resolved_list.append(resolved_event)
            continue
        seen += 1
        raw_graph = _build_graph(event)
        if raw_graph is None:
            log(
                f"  [{seen}/{target_total}] {event.action} {_event_target_label(event)} "
                "→ no DOM graph captured; skipped"
            )
            resolved_list.append(resolved_event)
            continue
        pre_pruned = _prune_graph(raw_graph)
        final_graph = _llm_prune(pre_pruned, event, llm, index)
        try:
            resolved_event.constellation = Constellation.model_validate(final_graph)
        except PydanticValidationError as exc:
            raise LLMResponseError(
                f"resolve entry {index} produced invalid constellation: {exc}"
            ) from exc
        log(
            f"  [{seen}/{target_total}] {event.action} {_event_target_label(event)} "
            f"→ {_constellation_label(resolved_event.constellation)}"
        )
        resolved_list.append(resolved_event)
    return resolved_list


def _event_target_label(event: NormalizedEvent) -> str:
    target = (event.target or "").strip()
    if target:
        return f"'{target[:40]}'"
    return ""


def _constellation_label(constellation: Constellation | None) -> str:
    if constellation is None:
        return "(none)"
    tag = constellation.tag or "?"
    text = (constellation.text or "").strip()
    label = f"<{tag}>"
    if text:
        label += f" '{text[:40]}'"
    attrs = constellation.attrs or {}
    aria = attrs.get("aria-label") or attrs.get("data-action") or attrs.get("role")
    if aria and not text:
        label += f" [{aria}]"
    return label


# Public alias — some call sites still use the old name.
resolve_selectors = resolve_constellations


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


def _build_graph(event: NormalizedEvent) -> dict[str, Any] | None:
    """Stitch the hydrated normalize-stage fields into a single graph dict.

    When the extension sent a full selector object, use it verbatim.
    Fall back to ancestors[-1] (which is structurally identical to the
    selector) when target_self is missing — e.g., older capture dumps
    or tests that only populate ancestors.
    """
    if event.target_self:
        base = copy.deepcopy(event.target_self)
    elif event.target_ancestors:
        base = copy.deepcopy(event.target_ancestors[-1])
    else:
        return None
    if "tag" not in base or not base["tag"]:
        return None
    base["ancestors"] = copy.deepcopy(event.target_ancestors or [])
    base["neighbors"] = copy.deepcopy(event.target_neighbors or [])
    base["children"] = (
        copy.deepcopy(event.target_children)
        if event.target_children is not None
        else None
    )
    base["special"] = copy.deepcopy(event.target_special) if event.target_special else None
    return base


# ---------------------------------------------------------------------------
# Deterministic pre-prune
# ---------------------------------------------------------------------------


def _prune_graph(graph: dict[str, Any]) -> dict[str, Any]:
    out = _prune_node(graph)
    out["ancestors"] = [_prune_node(a) for a in graph.get("ancestors") or []]
    out["neighbors"] = [_prune_node(n) for n in graph.get("neighbors") or []]
    children = graph.get("children")
    out["children"] = (
        [_prune_node(c) for c in children]
        if isinstance(children, list)
        else None
    )
    special = graph.get("special")
    out["special"] = _prune_node(special) if isinstance(special, dict) else None
    return out


def _prune_node(node: dict[str, Any]) -> dict[str, Any]:
    out = dict(node)
    classes = out.get("classes")
    if isinstance(classes, list):
        redacted: list[str] = []
        for c in classes:
            if not isinstance(c, str):
                continue
            r = _redact_hashed_class(c)
            if r is not None:
                redacted.append(r)
        out["classes"] = redacted
    node_id = out.get("id")
    if isinstance(node_id, str):
        out["id"] = _redact_hashed_id(node_id)
    attrs = out.get("attrs")
    if isinstance(attrs, dict):
        out["attrs"] = {
            k: v for k, v in attrs.items()
            if isinstance(k, str) and _keep_attr(k, v)
        }
    return out


def _redact_hashed_class(name: str) -> str | None:
    """Return a wildcard-redacted form of `name`, or None to drop it.

    - When the class has a stable semantic prefix (CSS Modules, etc.),
      redact only the trailing hash to `*`. The runtime scorer treats
      `*` as a wildcard, so `Title-module__anchor__*` still matches the
      same component after a bundler rebuild changes the hash.
    - When the class is purely auto-generated noise (`css-1abc23`, `:r3:`,
      a UUID, a >40-char string with no recognizable prefix, an embedded
      ≥5-char hex run), drop it.

    Redaction is attempted first so a class like
    `IssuePullRequestTitle-module__ListItemTitle_1__HZYnd` (51 chars, but
    informative once the trailing hash is wildcarded) survives.
    """
    for pattern, template in _REDACT_CLASS_RULES:
        if pattern.match(name):
            return pattern.sub(template, name)
    if len(name) > 40:
        return None
    if any(p.match(name) for p in _PURE_HASH_CLASS_PATTERNS):
        return None
    if _HEX_RUN_RE.search(name):
        return None
    return name


def _redact_hashed_id(value: str) -> str | None:
    """Same idea as `_redact_hashed_class`, applied to an `id`.

    Mostly catches React useId leakage (`_r_1b_-list-view-node-_r_20_`)
    where each `_r_<hex>_` segment is dynamic but the surrounding tokens
    (`-list-view-node-`) carry the actual semantic identity.
    """
    redacted = value
    for pattern, replacement in _REDACT_ID_SUBSTRING_RULES:
        redacted = pattern.sub(replacement, redacted)
    if redacted != value:
        # Ensure something semantic survives the redaction; an id that
        # collapses to just wildcards/separators is no better than dropped.
        if redacted.replace("_r_*_", "").strip("-_") == "":
            return None
        return redacted
    if any(p.match(value) for p in _PURE_HASH_ID_PATTERNS):
        return None
    return value


def _is_hashed_class(name: str) -> bool:
    """True if redaction would either drop the class or rewrite it to
    a wildcard form. Used by tests."""
    redacted = _redact_hashed_class(name)
    return redacted is None or redacted != name


def _is_hashed_id(value: str) -> bool:
    redacted = _redact_hashed_id(value)
    return redacted is None or redacted != value


def _keep_attr(name: str, value: Any) -> bool:
    if name in _DROP_ATTRS:
        return False
    if name == "tabindex":
        try:
            int(str(value).strip())
        except (TypeError, ValueError):
            return True
        return False
    return True


# ---------------------------------------------------------------------------
# LLM prune
# ---------------------------------------------------------------------------


def build_user_prompt(event: NormalizedEvent, graph: dict[str, Any]) -> str:
    payload: dict[str, Any] = {
        "action": event.action,
        "target_description": event.target,
        "constellation": graph,
    }
    if event.value is not None:
        payload["typed_text"] = event.value
    if event.key is not None:
        payload["key"] = event.key
    if event.url_after:
        payload["page_url"] = event.url_after
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _llm_prune(
    graph: dict[str, Any], event: NormalizedEvent, llm: LLMClient, index: int,
) -> dict[str, Any]:
    user_prompt = build_user_prompt(event, graph)
    try:
        payload = llm.call_json(_SYSTEM_PROMPT, user_prompt)
    except LLMResponseError:
        return graph
    if not isinstance(payload, dict) or "tag" not in payload:
        return graph
    try:
        Constellation.model_validate(payload)
    except PydanticValidationError:
        return graph
    return payload


# Public aliases — the identify stage runs the same deterministic prune
# on its LLM-emitted constellation, so noise the LLM forgot to drop
# (CSS Module hashes, React useId, etc.) doesn't bias the runtime
# scorer toward the recording-specific element.
prune_graph = _prune_graph
prune_node = _prune_node

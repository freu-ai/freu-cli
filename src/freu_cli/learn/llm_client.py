"""Provider-agnostic LLM wrapper used by the learn stages.

- Reads `LLM_MODEL` from the environment (provider-qualified when
  needed, e.g. `gpt-5.1`, `claude-sonnet-4-5`, `gemini/gemini-2.5-pro`,
  `xai/grok-4`, `minimax/MiniMax-M2`). Falls back to `DEFAULT_MODEL`
  when unset.
- Reads the **provider-specific** API key env var (e.g. `OPENAI_API_KEY`,
  `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY`, …) — LiteLLM
  routes the call based on the model prefix.
- Validates that the required key(s) for the chosen model are set
  before any LLM call is made, so `freu-cli learn` can fail fast
  before capture starts instead of midway through the pipeline.
- Normalizes the chat-completions call into a (system_prompt,
  user_prompt) signature so the stages can be unit-tested by passing
  a fake callable.
- Retries once on JSON-decode failure with a terse corrective follow-up.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from freu_cli.learn.errors import LLMResponseError

LLMCallable = Callable[[str, str], str]
"""A function (system_prompt, user_prompt) -> raw_assistant_text."""


DEFAULT_MODEL = "gpt-5.1"


@dataclass(slots=True)
class LLMClient:
    model: str
    _call: LLMCallable

    def call(self, system_prompt: str, user_prompt: str) -> str:
        return self._call(system_prompt, user_prompt)

    def call_json(
        self, system_prompt: str, user_prompt: str,
    ) -> Any:
        raw = self._call(system_prompt, user_prompt)
        try:
            return parse_llm_json(raw)
        except ValueError:
            retry = self._call(
                system_prompt,
                user_prompt
                + "\n\nYour previous response was not valid JSON. "
                + "Return ONLY the JSON document, no prose, no markdown fences.",
            )
            try:
                return parse_llm_json(retry)
            except ValueError as exc:
                raise LLMResponseError(
                    f"LLM returned invalid JSON after retry: {exc}\n"
                    f"---\n{retry[:2000]}"
                ) from exc


def build_llm_client() -> LLMClient:
    """Build an LLMClient that dispatches to the provider implied by `LLM_MODEL`.

    `LLM_MODEL` defaults to `DEFAULT_MODEL` (`gpt-5.1`) when unset. The
    caller must also have exported the API-key env var for that
    provider (e.g. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
    `GEMINI_API_KEY`, `XAI_API_KEY`, `MINIMAX_API_KEY`). LiteLLM is
    asked up front which key(s) are needed for the chosen model, so
    the builder fails immediately if any are missing.
    """
    model = os.getenv("LLM_MODEL", "").strip() or DEFAULT_MODEL

    # Lazy import so the rest of the package (including stage unit
    # tests that inject a fake LLMClient) doesn't force litellm onto
    # every caller.
    import litellm

    env_check = litellm.validate_environment(model=model)
    missing = [key for key in env_check.get("missing_keys") or [] if key]
    if missing:
        raise LLMResponseError(
            f"Missing env var(s) for model '{model}': {', '.join(missing)}. "
            "Export the matching provider API key before running "
            "`freu-cli learn`."
        )

    def _call(system_prompt: str, user_prompt: str) -> str:
        response = litellm.completion(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content or ""

    return LLMClient(model=model, _call=_call)


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def parse_llm_json(raw: str) -> Any:
    """Best-effort extraction of a JSON document from a chat-completion response.

    Handles three cases:
      1. Raw JSON.
      2. A fenced ```json ... ``` block.
      3. A response with prose + a JSON object/array (finds first `{...}` or `[...]`).
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty response")

    fence_match = _JSON_FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    first_obj = _find_first_json_span(text, "{", "}")
    if first_obj is not None:
        try:
            return json.loads(first_obj)
        except json.JSONDecodeError:
            pass
    first_arr = _find_first_json_span(text, "[", "]")
    if first_arr is not None:
        try:
            return json.loads(first_arr)
        except json.JSONDecodeError as exc:
            raise ValueError(f"could not parse JSON span: {exc}") from exc
    raise ValueError("no JSON object or array found in response")


def _find_first_json_span(text: str, open_ch: str, close_ch: str) -> str | None:
    start = text.find(open_ch)
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None

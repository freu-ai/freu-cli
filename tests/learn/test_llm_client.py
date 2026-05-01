import pytest

from freu_cli.learn.errors import LLMResponseError
from freu_cli.learn.llm_client import (
    DEFAULT_MODEL,
    LLMClient,
    build_llm_client,
    parse_llm_json,
)

# --------------------------------------------------------------------------
# parse_llm_json
# --------------------------------------------------------------------------

def test_parse_llm_json_raw_object():
    assert parse_llm_json('{"a": 1}') == {"a": 1}


def test_parse_llm_json_fenced_block():
    text = "Sure, here you go:\n```json\n{\"a\": 2}\n```\nhope that helps"
    assert parse_llm_json(text) == {"a": 2}


def test_parse_llm_json_extracts_first_object_with_prose():
    text = "Here is the answer: {\"a\": 3, \"b\": [1, 2]} done."
    assert parse_llm_json(text) == {"a": 3, "b": [1, 2]}


def test_parse_llm_json_extracts_first_array_with_prose():
    text = "Result: [1, 2, 3] — final"
    assert parse_llm_json(text) == [1, 2, 3]


def test_parse_llm_json_rejects_empty():
    with pytest.raises(ValueError):
        parse_llm_json("")


def test_parse_llm_json_rejects_non_json_text():
    with pytest.raises(ValueError):
        parse_llm_json("the answer is 42")


# --------------------------------------------------------------------------
# LLMClient.call_json retry behavior
# --------------------------------------------------------------------------

def test_llm_client_call_json_retries_once_on_bad_json():
    calls = []

    def _call(system_prompt: str, user_prompt: str) -> str:
        calls.append((system_prompt, user_prompt))
        if len(calls) == 1:
            return "not json at all"
        return '{"ok": true}'

    client = LLMClient(model="m", _call=_call)
    assert client.call_json("sys", "user") == {"ok": True}
    assert len(calls) == 2
    assert "not valid JSON" in calls[1][1]


def test_llm_client_call_json_raises_after_double_failure():
    def _call(_system: str, _user: str) -> str:
        return "nope"

    client = LLMClient(model="m", _call=_call)
    with pytest.raises(LLMResponseError):
        client.call_json("sys", "user")


# --------------------------------------------------------------------------
# build_llm_client: provider-agnostic env contract
# --------------------------------------------------------------------------

def test_default_model_is_gpt_5_1():
    assert DEFAULT_MODEL == "gpt-5.1"


def _isolate_provider_env(monkeypatch) -> None:
    """Strip every known provider key so each test starts from a clean slate."""
    for name in (
        "LLM_MODEL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "XAI_API_KEY",
        "MINIMAX_API_KEY",
        "OPENAI_MODEL",  # any stale env from older versions must not leak in
    ):
        monkeypatch.delenv(name, raising=False)


def test_build_llm_client_defaults_to_default_model(monkeypatch):
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    client = build_llm_client()
    assert client.model == DEFAULT_MODEL


def test_build_llm_client_honors_explicit_model(monkeypatch):
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    client = build_llm_client()
    assert client.model == "gpt-4o-mini"


def test_build_llm_client_routes_to_anthropic_when_claude_model_set(monkeypatch):
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-4-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    client = build_llm_client()
    assert client.model == "claude-sonnet-4-5"


def test_build_llm_client_raises_when_openai_key_missing(monkeypatch):
    _isolate_provider_env(monkeypatch)  # no OPENAI_API_KEY
    monkeypatch.setenv("LLM_MODEL", "gpt-5.1")
    with pytest.raises(LLMResponseError) as exc_info:
        build_llm_client()
    message = str(exc_info.value)
    assert "OPENAI_API_KEY" in message
    assert "gpt-5.1" in message


def test_build_llm_client_raises_when_anthropic_key_missing(monkeypatch):
    _isolate_provider_env(monkeypatch)  # no ANTHROPIC_API_KEY
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-4-5")
    with pytest.raises(LLMResponseError) as exc_info:
        build_llm_client()
    message = str(exc_info.value)
    assert "ANTHROPIC_API_KEY" in message


def test_build_llm_client_call_invokes_litellm_completion(monkeypatch):
    """The returned callable forwards to litellm.completion with the model."""
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "gpt-5.1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    import litellm

    class _FakeChoice:
        def __init__(self, content: str) -> None:
            self.message = type("M", (), {"content": content})()

    class _FakeResponse:
        def __init__(self, content: str) -> None:
            self.choices = [_FakeChoice(content)]

    received: dict = {}

    def _fake_completion(*, model, messages, **_kwargs):
        received["model"] = model
        received["messages"] = messages
        return _FakeResponse("hello from the fake provider")

    monkeypatch.setattr(litellm, "completion", _fake_completion)

    client = build_llm_client()
    assert client.call("sys", "user") == "hello from the fake provider"
    assert received["model"] == "gpt-5.1"
    assert received["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user"},
    ]

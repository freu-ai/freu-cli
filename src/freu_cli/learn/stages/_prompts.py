"""Helpers for loading prompt files packaged alongside the stages."""

from __future__ import annotations

from importlib import resources


def load_prompt(name: str) -> str:
    """Return the contents of a prompts/<name> resource."""
    with resources.files("freu_cli.learn.prompts").joinpath(name).open("r", encoding="utf-8") as f:
        return f.read()

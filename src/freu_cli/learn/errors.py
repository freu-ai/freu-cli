from __future__ import annotations


class LearnError(Exception):
    """Base exception for learn-pipeline failures."""


class LLMResponseError(LearnError):
    """The LLM returned a malformed or unusable response."""


class ValidationError(LearnError):
    """The synthesized skill failed validation."""

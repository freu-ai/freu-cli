from __future__ import annotations


class DSLExecutionError(Exception):
    """Raised when a skill step cannot be parsed or executed."""

    def __init__(
        self,
        message: str,
        *,
        step_index: int | None = None,
        line_number: int | None = None,
        method: str | None = None,
        description: str | None = None,
    ) -> None:
        super().__init__(message)
        self.step_index = step_index
        self.line_number = line_number
        self.method = method
        self.description = description

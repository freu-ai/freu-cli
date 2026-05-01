from __future__ import annotations

import os
import re
from typing import Any

from freu_cli.run.errors import DSLExecutionError


class TemplateRenderer:
    VARIABLE_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
    FULL_VARIABLE_PATTERN = re.compile(r"^\s*\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}\s*$")
    ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

    def render_value(self, value: Any, context: dict[str, Any]) -> Any:
        if isinstance(value, str):
            return self._render_string(value, context)
        if isinstance(value, list):
            return [self.render_value(item, context) for item in value]
        if isinstance(value, dict):
            return {key: self.render_value(item, context) for key, item in value.items()}
        return value

    def _render_string(self, value: str, context: dict[str, Any]) -> Any:
        full_variable_match = self.FULL_VARIABLE_PATTERN.match(value)
        if full_variable_match:
            variable_name = full_variable_match.group(1)
            if variable_name not in context:
                raise DSLExecutionError(f"Missing template variable: {variable_name}")
            return context[variable_name]

        value = self._render_env_string(value)

        def replace(match: re.Match[str]) -> str:
            variable_name = match.group(1)
            if variable_name not in context:
                raise DSLExecutionError(f"Missing template variable: {variable_name}")
            return str(context[variable_name])

        value = self.VARIABLE_PATTERN.sub(replace, value)
        return self._render_env_string(value)

    def _render_env_string(self, value: str) -> str:
        def replace(match: re.Match[str]) -> str:
            variable_name = match.group(1)
            env_value = os.getenv(variable_name, "")
            if env_value == "":
                raise DSLExecutionError(f"Missing environment variable: {variable_name}")
            return env_value

        return self.ENV_PATTERN.sub(replace, value)

from __future__ import annotations

from typing import Any

from freu_cli.run.errors import DSLExecutionError
from freu_cli.run.models import Step
from freu_cli.run.registry import CommandRegistry

_WHOLE_RESULT_FIELD = "__whole_result__"


class DSLParser:
    def __init__(self, registry: CommandRegistry | None = None) -> None:
        self.registry = registry

    def parse_steps(self, raw_steps: list[Any]) -> list[Step]:
        steps: list[Step] = []
        for index, raw_step in enumerate(raw_steps, start=1):
            if not isinstance(raw_step, dict):
                raise DSLExecutionError(f"Step {index} must be a JSON object")

            if raw_step.get("type") == "for_each":
                steps.append(self._parse_for_each_step(raw_step, index))
                continue
            if raw_step.get("type") == "if":
                steps.append(self._parse_if_step(raw_step, index))
                continue
            steps.append(self._parse_action_step(raw_step, index))
        return steps

    def _parse_action_step(self, raw_step: dict[str, Any], index: int) -> Step:
        method = raw_step.get("method")
        if not isinstance(method, str) or not method.strip():
            raise DSLExecutionError(f"Step {index} is missing a valid 'method'")

        outputs = self._parse_step_outputs(raw_step, index)
        raw_arguments = raw_step.get("arguments", [])
        if not isinstance(raw_arguments, list):
            raise DSLExecutionError(f"Step {index} arguments must be an array")

        params = self._parse_arguments(raw_arguments, index)
        self._validate_registered_outputs(method, outputs, index)
        description = raw_step.get("description")
        if not isinstance(description, str) or not description.strip():
            raise DSLExecutionError(
                f"Step {index} is missing a non-empty 'description'"
            )
        return Step(
            line_number=index, method=method, description=description,
            params=params, outputs=outputs,
        )

    def _parse_for_each_step(self, raw_step: dict[str, Any], index: int) -> Step:
        item_name = raw_step.get("item_name")
        if not isinstance(item_name, str) or not item_name.strip():
            raise DSLExecutionError(f"Step {index} for_each must define a valid 'item_name'")
        if "source" not in raw_step:
            raise DSLExecutionError(f"Step {index} for_each must define 'source'")
        source = raw_step.get("source")

        nested_steps = raw_step.get("steps")
        if not isinstance(nested_steps, list):
            raise DSLExecutionError(f"Step {index} for_each 'steps' must be an array")

        result_name = raw_step.get("result")
        output_name = raw_step.get("output")
        if output_name is not None and result_name is None:
            raise DSLExecutionError(
                f"Step {index} for_each must define 'result' when 'output' is present"
            )
        if result_name is not None and output_name is None:
            raise DSLExecutionError(
                f"Step {index} for_each must define 'output' when 'result' is present"
            )

        return Step(
            line_number=index,
            step_type="for_each",
            source=source,
            item_name=item_name.strip(),
            steps=self.parse_steps(nested_steps),
            result=result_name.strip() if isinstance(result_name, str) else None,
            output=output_name.strip() if isinstance(output_name, str) else None,
        )

    def _parse_if_step(self, raw_step: dict[str, Any], index: int) -> Step:
        raw_condition = raw_step.get("condition")
        if not isinstance(raw_condition, dict):
            raise DSLExecutionError(f"Step {index} if must define a valid 'condition' object")
        if raw_condition.get("type") is not None:
            raise DSLExecutionError(f"Step {index} if condition must be a method step")

        condition_step = self._parse_action_step(raw_condition, index)
        if len(condition_step.outputs) != 1:
            raise DSLExecutionError(
                f"Step {index} if condition must define exactly one output variable"
            )

        nested_steps = raw_step.get("steps")
        if not isinstance(nested_steps, list):
            raise DSLExecutionError(f"Step {index} if 'steps' must be an array")

        return Step(
            line_number=index,
            step_type="if",
            condition=condition_step,
            steps=self.parse_steps(nested_steps),
        )

    def _parse_arguments(self, raw_arguments: list[Any], index: int) -> dict[str, Any]:
        params: dict[str, Any] = {}
        for argument in raw_arguments:
            if not isinstance(argument, dict):
                raise DSLExecutionError(f"Step {index} argument entries must be objects")
            name = argument.get("name")
            if not isinstance(name, str) or not name.strip():
                raise DSLExecutionError(f"Step {index} has an argument with invalid 'name'")
            if name in params:
                raise DSLExecutionError(f"Step {index} has duplicate argument: {name}")
            if "value" in argument and argument.get("value") is not None:
                params[name] = argument["value"]
                continue
            if "source" in argument and argument.get("source") is not None:
                params[name] = argument["source"]
                continue
            raise DSLExecutionError(
                f"Step {index} argument '{name}' is missing 'value' or 'source'"
            )
        return params

    def _parse_step_outputs(self, raw_step: dict[str, Any], index: int) -> dict[str, str]:
        if "outputs" not in raw_step:
            return {}
        raw_outputs = raw_step.get("outputs", [])
        if not isinstance(raw_outputs, list):
            raise DSLExecutionError(f"Step {index} outputs must be an array")
        outputs: dict[str, str] = {}
        for output in raw_outputs:
            if not isinstance(output, dict):
                raise DSLExecutionError(f"Step {index} output entries must be objects")
            context_name = output.get("name")
            if not isinstance(context_name, str) or not context_name.strip():
                raise DSLExecutionError(f"Step {index} has an invalid output variable name")
            if context_name in outputs:
                raise DSLExecutionError(f"Step {index} has duplicate output: {context_name}")
            result_name = output.get("value")
            if not isinstance(result_name, str) or not result_name.strip():
                raise DSLExecutionError(
                    f"Step {index} output '{context_name}' must map to a valid result field"
                )
            outputs[context_name] = result_name
        return outputs

    def _validate_registered_outputs(
        self, method: str, outputs: dict[str, str], index: int,
    ) -> None:
        if self.registry is None or not outputs:
            return
        spec = self.registry.get(method)
        if spec.allow_extra_params:
            return
        if not spec.output_fields:
            raise DSLExecutionError(
                f"Step {index} action '{method}' does not declare any output fields"
            )
        invalid_fields = [
            result_name
            for result_name in outputs.values()
            if result_name != _WHOLE_RESULT_FIELD and result_name not in spec.output_fields
        ]
        if invalid_fields:
            raise DSLExecutionError(
                f"Step {index} action '{method}' has invalid output fields: "
                f"{', '.join(invalid_fields)}"
            )


def load_workflow_data(data: dict[str, Any], registry: CommandRegistry | None = None) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise DSLExecutionError("Workflow data must be an object")
    dsl_steps = data.get("dsl")
    context = data.get("context", {})
    if not isinstance(dsl_steps, list):
        raise DSLExecutionError("Workflow data must contain a 'dsl' array")
    if not isinstance(context, dict):
        raise DSLExecutionError("Workflow data 'context' must be an object")
    parser = DSLParser(registry=registry)
    return {"steps": parser.parse_steps(dsl_steps), "context": context}

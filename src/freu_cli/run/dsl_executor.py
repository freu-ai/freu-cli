from __future__ import annotations

from collections.abc import Callable
from typing import Any

from freu_cli.run.actions.browser_actions import describe_action
from freu_cli.run.errors import DSLExecutionError
from freu_cli.run.models import Step
from freu_cli.run.registry import CommandRegistry
from freu_cli.run.renderer import TemplateRenderer
from freu_cli.run.workflow_loader import _WHOLE_RESULT_FIELD

StepStartCallback = Callable[[Step, int, int], None]


class DSLExecutor:
    """Walk a parsed list of Steps, invoking registered browser actions."""

    def __init__(self, registry: CommandRegistry, renderer: TemplateRenderer) -> None:
        self.registry = registry
        self.renderer = renderer
        # Descriptions of action steps that have completed successfully, in
        # execution order. Surfaced to the caller on failure so a calling
        # agent sees what worked before the break, not just the exception.
        self.completed_descriptions: list[str] = []
        # Monotonically increasing counter used to prefix every printed
        # step with `Step N:` — gives the operator a flat, sequential
        # view across nested for_each / if bodies.
        self._step_counter: int = 0

    def execute(
        self,
        steps: list[Step],
        context: dict[str, Any],
        on_step_start: StepStartCallback | None = None,
        step_offset: int = 0,
        **runtime_kwargs: Any,
    ) -> None:
        self._execute_steps(
            steps,
            context,
            on_step_start=on_step_start,
            step_offset=step_offset,
            **runtime_kwargs,
        )

    def _execute_steps(
        self,
        steps: list[Step],
        context: dict[str, Any],
        on_step_start: StepStartCallback | None = None,
        step_offset: int = 0,
        root_step_index: int | None = None,
        **runtime_kwargs: Any,
    ) -> None:
        total_steps = len(steps)
        for index, step in enumerate(steps, start=1):
            current_root_step_index = root_step_index or (step_offset + index)
            if step.step_type == "for_each":
                self._execute_for_each_step(
                    step, context, on_step_start=on_step_start,
                    root_step_index=current_root_step_index, **runtime_kwargs,
                )
                continue
            if step.step_type == "if":
                self._execute_if_step(
                    step, context, on_step_start=on_step_start,
                    root_step_index=current_root_step_index, **runtime_kwargs,
                )
                continue
            self._execute_action_step(
                step, context, index=index, total_steps=total_steps,
                on_step_start=on_step_start, root_step_index=current_root_step_index,
                **runtime_kwargs,
            )

    def _execute_action_step(
        self,
        step: Step,
        context: dict[str, Any],
        *,
        index: int,
        total_steps: int,
        on_step_start: StepStartCallback | None = None,
        root_step_index: int | None = None,
        **runtime_kwargs: Any,
    ) -> None:
        current_root_step_index = root_step_index or index
        try:
            spec = self.registry.get(step.method)
            rendered_params = self.renderer.render_value(step.params, context)
            validated_params = self._validate_params(
                step.method, rendered_params, spec.params,
                allow_extra=spec.allow_extra_params,
            )
        except DSLExecutionError as exc:
            raise self._wrap_step_error(str(exc), step=step,
                root_step_index=current_root_step_index, cause=exc) from exc

        self._print_step(step, validated_params, spec.params)
        if on_step_start is not None:
            on_step_start(step, index, total_steps)

        try:
            action_runtime_kwargs = {
                name: value for name, value in runtime_kwargs.items()
                if name in spec.runtime_params
            }
            result = spec.func(**action_runtime_kwargs, **validated_params)
        except DSLExecutionError as exc:
            raise self._wrap_step_error(str(exc), step=step,
                root_step_index=current_root_step_index, cause=exc) from exc
        except Exception as exc:
            raise self._wrap_step_error(
                f"Execution failed on step {step.line_number}: {exc}",
                step=step, root_step_index=current_root_step_index, cause=exc,
            ) from exc

        if step.outputs:
            self._store_outputs(step, result, context)

        self.completed_descriptions.append(step.description)

    def _execute_for_each_step(
        self, step: Step, context: dict[str, Any],
        on_step_start: StepStartCallback | None = None,
        root_step_index: int | None = None, **runtime_kwargs: Any,
    ) -> None:
        current_root_step_index = root_step_index or step.line_number
        try:
            rendered_source = self.renderer.render_value(step.source, context)
        except DSLExecutionError as exc:
            raise self._wrap_step_error(str(exc), step=step,
                root_step_index=current_root_step_index, cause=exc) from exc

        if not isinstance(rendered_source, list):
            raise self._wrap_step_error(
                f"for_each source must render to an array (step {step.line_number})",
                step=step, root_step_index=current_root_step_index,
            )

        aggregated_results: list[Any] = []
        self._step_counter += 1
        print(
            f"Step {self._step_counter}: for_each({step.item_name}) "
            f"over {len(rendered_source)} items"
        )

        for item in rendered_source:
            iteration_context = dict(context)
            iteration_context[step.item_name] = item
            self._execute_steps(
                step.steps, iteration_context, on_step_start=on_step_start,
                root_step_index=current_root_step_index, **runtime_kwargs,
            )
            if step.output:
                if step.result not in iteration_context:
                    raise self._wrap_step_error(
                        f"for_each result '{step.result}' was not produced (step {step.line_number})",
                        step=step, root_step_index=current_root_step_index,
                    )
                aggregated_results.append(iteration_context[step.result])

        if step.output:
            context[step.output] = aggregated_results
            print(f"  → stored output '{step.output}' ({len(aggregated_results)} items)")

    def _execute_if_step(
        self, step: Step, context: dict[str, Any],
        on_step_start: StepStartCallback | None = None,
        root_step_index: int | None = None, **runtime_kwargs: Any,
    ) -> None:
        current_root_step_index = root_step_index or step.line_number
        if step.condition is None:
            raise self._wrap_step_error(
                f"if step is missing condition (step {step.line_number})",
                step=step, root_step_index=current_root_step_index,
            )

        condition_context = dict(context)
        self._execute_action_step(
            step.condition, condition_context, index=1, total_steps=1,
            on_step_start=on_step_start, root_step_index=current_root_step_index,
            **runtime_kwargs,
        )

        condition_name = next(iter(step.condition.outputs.keys()))
        if condition_name not in condition_context:
            raise self._wrap_step_error(
                f"if condition output '{condition_name}' was not produced",
                step=step, root_step_index=current_root_step_index,
            )

        condition_value = condition_context[condition_name]
        if not isinstance(condition_value, bool):
            raise self._wrap_step_error(
                f"if condition output '{condition_name}' must be a boolean",
                step=step, root_step_index=current_root_step_index,
            )

        self._step_counter += 1
        print(f"Step {self._step_counter}: if({condition_name}={condition_value})")
        if not condition_value:
            return

        execution_context = dict(context)
        execution_context[condition_name] = condition_value
        self._execute_steps(
            step.steps, execution_context, on_step_start=on_step_start,
            root_step_index=current_root_step_index, **runtime_kwargs,
        )

    def _store_outputs(self, step: Step, result: Any, context: dict[str, Any]) -> None:
        if not isinstance(result, dict):
            raise DSLExecutionError(
                f"{step.method} must return an object when outputs are configured (step {step.line_number})"
            )
        for context_name, result_name in step.outputs.items():
            if result_name == _WHOLE_RESULT_FIELD:
                value = result
            else:
                if result_name not in result:
                    raise DSLExecutionError(
                        f"{step.method} did not return field '{result_name}' (step {step.line_number})"
                    )
                value = result[result_name]
            context[context_name] = value
            display = str(value)
            if len(display) > 200:
                display = display[:200] + "...(truncated)"
            print(f"  → stored output '{context_name}' from '{result_name}' = {display!r}")

    def _validate_params(
        self, action: str, params: dict[str, Any], expected_params: list[str],
        *, allow_extra: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise DSLExecutionError(f"Parameters for {action} must be an object")
        unexpected = [name for name in params if name not in expected_params]
        if unexpected and not allow_extra:
            raise DSLExecutionError(
                f"{action} has unexpected parameters: {', '.join(unexpected)}"
            )
        if allow_extra:
            return dict(params)
        return {name: params[name] for name in expected_params if name in params}

    def _print_step(self, step: Step, params: dict[str, Any], ordered_keys: list[str]) -> None:
        self._step_counter += 1
        prefix = f"Step {self._step_counter}:"
        detail = describe_action(step.method, params)
        if detail:
            print(f"{prefix} {step.description} ({detail})")
        else:
            print(f"{prefix} {step.description}")

    def _wrap_step_error(
        self, message: str, *, step: Step, root_step_index: int,
        cause: Exception | None = None,
    ) -> DSLExecutionError:
        cause_step_index = getattr(cause, "step_index", None)
        cause_line_number = getattr(cause, "line_number", None)
        cause_method = getattr(cause, "method", None)
        cause_description = getattr(cause, "description", None)
        return DSLExecutionError(
            message,
            step_index=(cause_step_index if cause_step_index is not None else root_step_index),
            line_number=(cause_line_number if cause_line_number is not None else step.line_number),
            method=(cause_method if cause_method else step.method or None),
            description=(cause_description if cause_description else step.description or None),
        )

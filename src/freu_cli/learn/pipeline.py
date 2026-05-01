"""Top-level learn pipeline: events.json + objective -> skill folder on disk.

When `log_dir` is provided, the stage outputs are persisted as
`normalized.json`, `resolved.json`, and `synthesized.json` for inspection
and debugging. The final SKILL.md + Command JSONs are always written to
`out_dir`, regardless of `log_dir`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from freu_cli.learn.emit import write_skill
from freu_cli.learn.errors import LearnError, LLMResponseError
from freu_cli.learn.llm_client import LLMClient
from freu_cli.learn.models import RawEvent, Skill
from freu_cli.learn.stages.identify import identify_outputs
from freu_cli.learn.stages.normalize import normalize_events
from freu_cli.learn.stages.resolve import resolve_constellations
from freu_cli.learn.stages.synthesize import synthesize_skill
from freu_cli.learn.validate import validate_skill

ProgressCallback = Callable[[str], None]


def run_learn(
    events_path: Path,
    objective: str,
    out_dir: Path,
    llm: LLMClient,
    *,
    log_dir: Path | None = None,
    on_progress: ProgressCallback | None = None,
) -> Skill:
    """Run normalize -> resolve -> synthesize -> validate -> emit.

    Stage outputs stay in memory by default. When `log_dir` is set, each
    stage's output is also written as JSON into `log_dir` so the caller
    can inspect intermediates after the fact.

    `on_progress`, when set, is called with one human-readable line per
    pipeline milestone — used by the CLI to narrate what's happening
    during the (often minutes-long) learn run. Library callers can leave
    it unset for silent execution.
    """
    log: ProgressCallback = on_progress or (lambda _msg: None)

    events_path = Path(events_path).expanduser().resolve()
    if not events_path.exists():
        raise LearnError(f"events.json not found: {events_path}")

    if log_dir is not None:
        log_dir = Path(log_dir).expanduser().resolve()
        log_dir.mkdir(parents=True, exist_ok=True)

    raw = _load_raw_events(events_path)
    log(f"Loaded {len(raw)} raw event(s) from {events_path.name}.")

    log("")
    log("Stage 1/4 — Normalize: distill raw DOM events into semantic actions.")
    normalized = normalize_events(raw, objective, llm)
    if not normalized:
        raise LLMResponseError(
            "normalize stage produced zero events; is the capture empty or the objective unclear?"
        )
    _dump(log_dir, "normalized.json", normalized)
    log(f"  → {len(normalized)} action(s):")
    for index, event in enumerate(normalized, start=1):
        target = event.target or ""
        target_part = f" '{target[:40]}'" if target else ""
        description = (event.description or "").strip() or "(no description)"
        log(f"    {index:>2}. {event.action}{target_part} — {description}")

    log("")
    log("Stage 2/4 — Resolve: prune captured DOM graphs into stable constellations.")
    resolved = resolve_constellations(normalized, llm, on_progress=on_progress)
    _dump(log_dir, "resolved.json", resolved)
    bound = sum(1 for r in resolved if r.constellation is not None)
    target_total = sum(1 for r in resolved if r.action in {"click_element", "type_text", "press_key"})
    log(f"  → resolved {bound}/{target_total} target-bearing event(s) into constellations.")

    log("")
    log("Stage 3/4 — Identify: detect retrieval objectives and locate value-bearing elements.")
    last_snapshot = _load_last_snapshot(events_path, raw)
    retrieval_plan = identify_outputs(
        resolved, objective, last_snapshot, llm, on_progress=on_progress,
    )
    _dump(log_dir, "identified.json", retrieval_plan)

    log("")
    log("Stage 4/4 — Synthesize: turn resolved actions into a reusable skill.")
    skill = synthesize_skill(resolved, retrieval_plan, objective, llm)
    _dump(log_dir, "synthesized.json", skill)
    total_steps = sum(len(command.dsl) for command in skill.commands)
    log(f"  → '{skill.skill}': {len(skill.commands)} command(s), {total_steps} step(s) total")
    for command in skill.commands:
        log(f"    • {command.command} ({len(command.dsl)} step(s)) — {command.description.strip() or '(no description)'}")

    validate_skill(skill)
    log("")
    log("Validated. Writing skill files...")
    write_skill(skill, Path(out_dir))
    return skill


def _dump(log_dir: Path | None, name: str, payload: Any) -> None:
    if log_dir is None:
        return
    (log_dir / name).write_text(
        json.dumps(_to_jsonable(payload), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _to_jsonable(payload: Any) -> Any:
    if isinstance(payload, BaseModel):
        return payload.model_dump(exclude_none=True)
    if isinstance(payload, list):
        return [_to_jsonable(item) for item in payload]
    if isinstance(payload, dict):
        return {key: _to_jsonable(value) for key, value in payload.items()}
    return payload


def _load_raw_events(events_path: Path) -> list[RawEvent]:
    try:
        data: Any = json.loads(events_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LearnError(f"events.json is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise LearnError("events.json must be a JSON array of event records")
    raw: list[RawEvent] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise LearnError(f"events.json entry {index} must be an object")
        raw.append(RawEvent.model_validate(item))
    return raw


def _load_last_snapshot(
    events_path: Path, raw_events: list[RawEvent],
) -> str | None:
    """Return the HTML of the last DOM snapshot referenced in `raw_events`.

    Each click record carries a relative `snapshot` path written by the
    capture sink (e.g. `snapshots/1745553600.html`). We walk the events
    in reverse and return the contents of the first one we can read.
    Returns None when no event carried a snapshot or no file exists —
    identify treats that as "skip retrieval analysis."
    """
    base_dir = events_path.parent
    for event in reversed(raw_events):
        extras = event.model_extra or {}
        snapshot = extras.get("snapshot")
        if not isinstance(snapshot, str) or not snapshot:
            continue
        candidate = (base_dir / snapshot).resolve()
        try:
            candidate.relative_to(base_dir.resolve())
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        try:
            return candidate.read_text(encoding="utf-8")
        except OSError:
            continue
    return None

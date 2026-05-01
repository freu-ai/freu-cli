"""freu-cli entry point.

Two subcommands:

  freu-cli learn <path> [--objective "..."]
      Capture a browsing session, then synthesize a skill.
      - Creates <path>/log/events.json from the Chrome extension.
      - Press Ctrl-C to stop capture; the pipeline then runs automatically
        and writes normalized.json, resolved.json, and synthesized.json
        into <path>/log/.
      - The final SKILL.md + one <Command>.json per command are written
        at the top level of <path>.

  freu-cli run <target> [command] [--<arg-name> value ...]
      Execute a skill command. <target> can be:
        - a path to a <Command>.json file (no command positional)
        - a path to a SKILL.md file (command is the next positional)
        - a path to a directory containing SKILL.md (command is next)
        - a bare skill name resolved against $FREU_SKILLS_DIR or cwd
      Any unknown `--flag value` pair becomes an input argument
      (`--email-subject "hi"` → `email_subject="hi"`).
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path
from typing import Any

from freu_cli import __version__


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    if argv is None:
        argv = sys.argv[1:]
    # `run` is parsed specially because it accepts free-form --<arg-name>
    # passthrough flags that argparse would otherwise reject.
    if argv and argv[0] == "run":
        args, extra = _parse_run_argv(argv[1:])
        return _cmd_run(args, extra)

    args = parser.parse_args(argv)
    dispatch = {"learn": _cmd_learn}
    return dispatch[args.command](args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="freu-cli",
        description="Capture, learn, and run browser skills via DOM.",
    )
    parser.add_argument("--version", action="version", version=f"freu-cli {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    learn = sub.add_parser(
        "learn",
        help="Capture a browsing session and synthesize a skill",
        description=(
            "Capture DOM events from the freu Chrome extension, then "
            "synthesize a Skill folder from the recording. Press Ctrl-C "
            "to stop capture; the learn pipeline runs automatically. "
            "Set LLM_MODEL (default: gpt-5.1) and the matching provider "
            "API key env var (OPENAI_API_KEY, ANTHROPIC_API_KEY, "
            "GEMINI_API_KEY, XAI_API_KEY, …). See README for the full list."
        ),
    )
    learn.add_argument(
        "path",
        help=(
            "Directory to write the skill into. Created if missing; re-runs "
            "on an existing skill folder ADD new commands to it. Capture "
            "events + pipeline intermediates are written to "
            "<path>/log_<unix_timestamp>/; the final SKILL.md and per-command "
            "JSON files are written at the top level of <path>."
        ),
    )
    learn.add_argument(
        "--objective",
        default="",
        help=(
            "Optional short description of what the recorded session is "
            "trying to accomplish. Injected into every LLM prompt."
        ),
    )
    learn.add_argument(
        "--from-events",
        metavar="EVENTS_JSON",
        default=None,
        help=(
            "Skip the capture step and re-run the synthesize pipeline against "
            "an existing events.json (e.g. <path>/log_<ts>/events.json). The "
            "skill folder at `path` is regenerated from those events; useful "
            "for iterating on prompts/heuristics without re-recording."
        ),
    )

    # `run` is handled by _parse_run_argv for free-form --arg-name flags.
    sub.add_parser(
        "run",
        help=(
            "Run a skill command. Usage: "
            "freu-cli run <skill> <command> --<arg-name> <value> ..."
        ),
        add_help=False,
    )
    return parser


# --------------------------------------------------------------------------
# learn (capture -> synthesize)
# --------------------------------------------------------------------------

_LEARNING_HINT = "Learning automation. This may take a minute..."


def _cmd_learn(args: argparse.Namespace) -> int:
    from freu_cli.capture.bridge import DEFAULT_HOST, DEFAULT_PORT
    from freu_cli.capture.recorder import record_capture
    from freu_cli.learn.errors import LearnError
    from freu_cli.learn.llm_client import build_llm_client
    from freu_cli.learn.pipeline import run_learn

    path = Path(args.path).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)

    # Validate env up front so the user doesn't record a capture only to
    # discover they haven't set the matching provider API key.
    try:
        llm = build_llm_client()
    except LearnError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(f"freu-cli learn → {path}")
    print(f"  model: {llm.model}")
    if args.objective:
        print(f"  objective: {args.objective}")

    if args.from_events:
        # Skip capture: reuse an existing events.json. The pipeline's
        # log artifacts go alongside the source events file so the
        # original log_<ts>/ folder isn't disturbed.
        events_path = Path(args.from_events).expanduser().resolve()
        if not events_path.exists():
            print(f"Error: events file not found: {events_path}", file=sys.stderr)
            return 2
        log_dir = events_path.parent
        print(f"\nReusing {events_path}")
    else:
        # Each run gets its own timestamped log folder so re-running `learn`
        # on an existing skill directory adds new commands without overwriting
        # earlier capture + pipeline traces.
        log_dir = path / f"log_{int(time.time())}"

        def _on_ready(handle) -> None:
            print(f"\nBridge listening on http://{handle.host}:{handle.port}/bridge")
            print("Load the freu Chrome extension, then interact with the browser.")
            print("Press Ctrl-C to stop recording.\n")

        try:
            events_path = record_capture(
                log_dir,
                host=DEFAULT_HOST,
                port=DEFAULT_PORT,
                on_ready=_on_ready,
            )
        except FileExistsError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        except OSError as exc:
            print(f"Error: failed to start bridge: {exc}", file=sys.stderr)
            return 1

        print(f"\nCaptured {events_path}")

    print(_LEARNING_HINT)

    # During the learn phase, additional Ctrl-Cs should NOT kill the
    # process — the user has already committed by stopping the capture.
    # Just re-print the hint so they know it's still working.
    previous_sigint = signal.signal(signal.SIGINT, _on_learn_interrupt)
    try:
        skill = run_learn(
            events_path=events_path,
            objective=args.objective,
            out_dir=path,
            llm=llm,
            log_dir=log_dir,
            on_progress=print,
        )
    except LearnError as exc:
        print(f"\nlearn failed: {exc}", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGINT, previous_sigint)

    print(f"\nWrote skill: {skill.skill} ({len(skill.commands)} commands)")
    for command in skill.commands:
        print(f"  - {command.command} ({len(command.dsl)} steps)")
    print(f"\nFiles at: {path}")
    print("  SKILL.md")
    for command in skill.commands:
        print(f"  {command.command}.json")
    return 0


def _on_learn_interrupt(_signum: int, _frame: object) -> None:
    print(f"\n{_LEARNING_HINT}", file=sys.stderr)


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------

def _parse_run_argv(argv: list[str]) -> tuple[argparse.Namespace, dict[str, Any]]:
    """Split `freu-cli run`'s argv into positionals + free-form --arg flags.

    Accepted positional forms:
      freu-cli run <Command.json> [--arg value ...]
      freu-cli run <SkillPath>  <Command> [--arg value ...]

    `SkillPath` is either a .md file, a directory, or a bare name that
    will be resolved against `FREU_SKILLS_DIR` or the current directory.
    """
    parser = argparse.ArgumentParser(
        prog="freu-cli run",
        add_help=True,
        description=(
            "Run a skill command. "
            "Usage: freu-cli run <skill> <command> --<arg-name> <value>, "
            "or freu-cli run <Command.json> --<arg-name> <value>."
        ),
    )
    parser.add_argument(
        "positionals",
        nargs="+",
        metavar="TARGET [COMMAND]",
        help="Either a <Command>.json path, or a <skill> and a <command>.",
    )
    args, extras = parser.parse_known_args(argv)

    positionals: list[str] = list(args.positionals)
    inputs = _parse_passthrough_flags(extras)
    return argparse.Namespace(positionals=positionals), inputs


def _parse_passthrough_flags(extras: list[str]) -> dict[str, Any]:
    """Turn a list of `--flag value` pairs (or `--flag=value`) into a dict.

    Flag names are translated to snake_case (so `--email-subject` is
    stored under the key `email_subject`). Values that parse as JSON are
    decoded (so `--times 3` passes an int), falling back to the raw
    string otherwise.
    """
    inputs: dict[str, Any] = {}
    index = 0
    while index < len(extras):
        token = extras[index]
        if not token.startswith("--"):
            raise SystemExit(
                f"Unexpected argument: {token} "
                "(free-form flags must start with '--')."
            )
        if "=" in token:
            flag, raw = token[2:].split("=", 1)
            index += 1
        else:
            flag = token[2:]
            if index + 1 >= len(extras):
                raise SystemExit(f"Flag {token} is missing a value.")
            raw = extras[index + 1]
            index += 2
        key = flag.replace("-", "_")
        try:
            inputs[key] = json.loads(raw)
        except json.JSONDecodeError:
            inputs[key] = raw
    return inputs


def _cmd_run(ns: argparse.Namespace, inputs: dict[str, Any]) -> int:
    from freu_cli.run.executor import run_file, run_skill

    positionals: list[str] = list(ns.positionals)
    if not positionals:
        print("Error: run requires at least one positional argument.", file=sys.stderr)
        return 2

    first = positionals[0]
    first_path = Path(first).expanduser()

    if first_path.suffix.lower() == ".json" and first_path.exists():
        if len(positionals) > 1:
            print(
                "Error: run with a Command.json does not accept a command positional.",
                file=sys.stderr,
            )
            return 2
        result = run_file(first_path.resolve(), inputs=inputs)
    else:
        if len(positionals) < 2:
            print(
                "Error: run with a skill path/name requires a command name.\n"
                "Usage: freu-cli run <skill> <command> --<arg-name> <value>",
                file=sys.stderr,
            )
            return 2
        command = positionals[1]
        skill_md = _resolve_skill_md_path(first)
        if skill_md is None:
            print(
                f"Error: could not resolve skill: {first}. "
                "Pass a SKILL.md path, a directory containing SKILL.md, "
                "or a bare skill name under $FREU_SKILLS_DIR.",
                file=sys.stderr,
            )
            return 2
        result = run_skill(command=command, skill_path=skill_md, inputs=inputs)

    if result.get("ok"):
        print("\nOK")
        return 0

    print(_format_failure_block(result), file=sys.stderr)
    return 1


def _format_failure_block(result: dict[str, Any]) -> str:
    """Render the run failure as a block listing what worked + what broke.

    Designed for a calling agent: it sees the descriptions of completed
    steps and the description of the step that failed, instead of a raw
    exception trace.
    """
    completed: list[str] = list(result.get("completed_steps") or [])
    failed_step = result.get("failed_step")
    if not failed_step:
        method = result.get("method")
        step_idx = result.get("step")
        if method and step_idx is not None:
            failed_step = f"step {step_idx} ({method})"
        elif step_idx is not None:
            failed_step = f"step {step_idx}"
        else:
            failed_step = "(unknown step)"
    reason = result.get("error") or "(unknown error)"

    lines: list[str] = ["", "FAILED.", ""]
    if completed:
        lines.append("Completed steps:")
        for index, description in enumerate(completed, start=1):
            lines.append(f"  {index}. {description}")
        lines.append("")
    lines.append("Pending step:")
    lines.append(f"  {failed_step}")
    lines.append("")
    lines.append(f"Reason: {reason}")
    return "\n".join(lines)


def _resolve_skill_md_path(target: str) -> Path | None:
    """Resolve <target> to a SKILL.md path.

    Resolution order:
      1. target points at an existing SKILL.md file.
      2. target is a directory containing SKILL.md.
      3. target is a bare name resolved against:
         a. `$FREU_SKILLS_DIR/<name>/SKILL.md`
         b. `./<name>/SKILL.md`
    """
    import os
    candidate = Path(target).expanduser()
    if candidate.is_file() and candidate.suffix.lower() == ".md":
        return candidate.resolve()
    if candidate.is_dir() and (candidate / "SKILL.md").is_file():
        return (candidate / "SKILL.md").resolve()
    skills_dir = os.getenv("FREU_SKILLS_DIR")
    if skills_dir:
        nested = Path(skills_dir).expanduser() / target / "SKILL.md"
        if nested.is_file():
            return nested.resolve()
    cwd_candidate = Path.cwd() / target / "SKILL.md"
    if cwd_candidate.is_file():
        return cwd_candidate.resolve()
    return None


if __name__ == "__main__":
    raise SystemExit(main())

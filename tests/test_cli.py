from __future__ import annotations

import pytest

from freu_cli import cli


def test_cli_prints_help(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "learn" in out
    assert "run" in out
    # The standalone `capture` subcommand has been folded into `learn`.
    assert "\n    capture" not in out


def test_cli_learn_help_mentions_capture_flow(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["learn", "--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "learn" in out
    assert "--objective" in out
    assert "Ctrl-C" in out or "capture" in out.lower()


def test_cli_run_help(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["run", "--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "freu-cli run" in out


def test_cli_requires_subcommand():
    with pytest.raises(SystemExit):
        cli.main([])


def test_cli_capture_subcommand_is_gone(capsys):
    """The separate `capture` subcommand must no longer exist."""
    with pytest.raises(SystemExit):
        cli.main(["capture", "/tmp/x"])


def test_parse_passthrough_flags_space_separated():
    assert cli._parse_passthrough_flags(["--repo-url", "https://x"]) == {
        "repo_url": "https://x"
    }


def test_parse_passthrough_flags_equals_form():
    assert cli._parse_passthrough_flags(["--repo-url=https://x"]) == {
        "repo_url": "https://x"
    }


def test_parse_passthrough_flags_translates_kebab_to_snake():
    result = cli._parse_passthrough_flags(["--email-subject", "Hello"])
    assert result == {"email_subject": "Hello"}


def test_parse_passthrough_flags_decodes_json_values():
    result = cli._parse_passthrough_flags([
        "--times", "3",
        "--flags", '["a","b"]',
        "--on", "true",
    ])
    assert result == {"times": 3, "flags": ["a", "b"], "on": True}


def test_parse_passthrough_flags_rejects_bare_tokens():
    with pytest.raises(SystemExit):
        cli._parse_passthrough_flags(["just-a-flag"])


def test_parse_passthrough_flags_rejects_missing_value():
    with pytest.raises(SystemExit):
        cli._parse_passthrough_flags(["--orphan"])


# ---------------------------------------------------------------------------
# Failure-block rendering: agent-facing "what worked / what broke" output.
# ---------------------------------------------------------------------------


def test_format_failure_block_includes_completed_and_pending_descriptions():
    block = cli._format_failure_block({
        "ok": False,
        "error": "element not found: button[data-action=star]",
        "completed_steps": [
            "Open the GitHub repository page.",
            "Focus the global search input.",
        ],
        "failed_step": "Click the Star button on the repository page.",
    })
    assert "FAILED." in block
    assert "Completed steps:" in block
    assert "1. Open the GitHub repository page." in block
    assert "2. Focus the global search input." in block
    assert "Pending step:" in block
    assert "Click the Star button on the repository page." in block
    assert "Reason: element not found: button[data-action=star]" in block


def test_format_failure_block_omits_completed_section_when_empty():
    block = cli._format_failure_block({
        "ok": False,
        "error": "boom",
        "completed_steps": [],
        "failed_step": "Open the demo page.",
    })
    assert "Completed steps:" not in block
    assert "Pending step:" in block
    assert "Open the demo page." in block


def test_format_failure_block_falls_back_when_no_failed_step_description():
    """When the runtime can't attach a description to the failed step
    (pre-step errors, unrecognized methods), the failure block still
    renders the method+index it has."""
    block = cli._format_failure_block({
        "ok": False,
        "error": "boom",
        "completed_steps": [],
        "failed_step": None,
        "step": 3,
        "method": "browser_click_element",
    })
    assert "step 3 (browser_click_element)" in block

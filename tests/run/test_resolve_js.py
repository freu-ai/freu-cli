"""Node-subprocess unit tests for the wildcard branch of `textSim` in
`resolve_js.py`. Skipped when `node` isn't on PATH.

We expose the closure-local `textSim` for the test by appending one
assignment line to the IIFE body before the closing `})()`. This
keeps `resolve_js.py` itself as the single source of truth — no
parallel re-implementation, no in-page-context-only execution.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from freu_cli.run.browser.resolve_js import RESOLVE_JS


def _node_eval_text_sim(cases: list[tuple[str, str]]) -> list[float]:
    if not shutil.which("node"):
        pytest.skip("node not available")
    expose = ";globalThis.__freuTextSim = textSim;\n})();"
    js_with_export = RESOLVE_JS.replace("})();", expose, 1)
    script = (
        "globalThis.window = globalThis;\n"
        + js_with_export
        + "const cases = " + json.dumps(cases) + ";\n"
        "const out = cases.map(([a, b]) => globalThis.__freuTextSim(a, b));\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_text_sim_wildcard_matches_when_segments_appear_in_order():
    out = _node_eval_text_sim([
        ("Total: $* due", "Total: $5,578.82 due"),
        ("Address: * Parcel Number: *", "Address: 35404 ARDO CT Parcel Number: 543-397-6"),
    ])
    assert out == [1.5, 1.5]


def test_text_sim_wildcard_misses_when_scaffold_is_missing():
    out = _node_eval_text_sim([
        ("Total: $* due", "Sum: $5,578.82 owed"),
        ("Parcel #*", "Tracer #41990500"),
    ])
    assert out == [0, 0]


def test_text_sim_wildcard_requires_segment_order():
    out = _node_eval_text_sim([
        ("foo * bar", "bar 1 foo"),  # segments in reversed order → no match
    ])
    assert out == [0]


def test_text_sim_bare_wildcard_contributes_nothing():
    out = _node_eval_text_sim([("*", "anything goes")])
    assert out == [0]


def test_text_sim_no_wildcard_uses_substring_branch():
    out = _node_eval_text_sim([
        ("star", "star"),                 # exact substring both ways → 1.5 + 1.0 + 0.5*1
        ("nope", "completely different"),
    ])
    assert out[0] == pytest.approx(3.0)
    assert out[1] < 0.5


def _node_eval_glob_match(cases: list[tuple[str, str]]) -> list[bool]:
    if not shutil.which("node"):
        pytest.skip("node not available")
    expose = ";globalThis.__freuGlob = globMatch;\n})();"
    js_with_export = RESOLVE_JS.replace("})();", expose, 1)
    script = (
        "globalThis.window = globalThis;\n"
        + js_with_export
        + "const cases = " + json.dumps(cases) + ";\n"
        "const out = cases.map(([p, c]) => globalThis.__freuGlob(p, c));\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_glob_match_handles_redacted_css_module_classes():
    """CSS Modules emit `<Component>-module__<part>__<hash>`. The learn
    pipeline redacts the trailing hash to `*`; the runtime scorer must
    glob-match so the same component still matches across rebuilds."""
    out = _node_eval_glob_match([
        # Same component, different bundler hash → match.
        ("Title-module__anchor__*", "Title-module__anchor__dBbYy"),
        ("Title-module__anchor__*", "Title-module__anchor__XYZ12"),
        # Different component → no match.
        ("Title-module__anchor__*", "Title-module__inline__dBbYy"),
        # Identical (no wildcard) — exact equality.
        ("btn-primary", "btn-primary"),
        ("btn-primary", "btn-secondary"),
    ])
    assert out == [True, True, False, True, False]


def test_glob_match_handles_react_useid_segments():
    """`_r_<hex>_` segments inside ids become `_r_*_` after redaction.
    The structural tokens around them carry the actual identity."""
    out = _node_eval_glob_match([
        ("_r_*_-list-view-node-_r_*_", "_r_1b_-list-view-node-_r_20_"),
        ("_r_*_-list-view-node-_r_*_", "_r_3a_-list-view-node-_r_77_"),
        # Different structure — no match.
        ("_r_*_-list-view-node-_r_*_", "_r_1b_-other-thing-_r_20_"),
    ])
    assert out == [True, True, False]


def test_glob_match_anchors_first_and_last_segments():
    out = _node_eval_glob_match([
        # First segment doesn't match at the START of candidate → fail.
        ("foo-*", "xfoo-bar"),
        # Last segment matches at the END → ok.
        ("foo-*-bar", "foo-anything-bar"),
        # Last segment present but not at the end → fail.
        ("foo-*-bar", "foo-bar-baz"),
    ])
    assert out == [False, True, False]


def test_glob_match_bare_wildcard_matches_anything():
    out = _node_eval_glob_match([
        ("*", "anything"),
        ("*", ""),
    ])
    assert out == [True, True]

# Changelog

## 0.1.0 — Initial release

First public release of `freu-cli`.

- **`freu-cli learn`** — captures DOM events from the freu Chrome
  extension, then runs a four-stage pipeline (normalize → resolve →
  identify → synthesize) that turns the recording into a reusable
  skill: a `SKILL.md` plus one `<Command>.json` per command, with
  per-step descriptions for human-readable execution traces.
- **`freu-cli run`** — executes a skill command against a live browser
  through the same bridge, narrating each step in domain terms and
  rendering an agent-friendly recovery block on failure
  (`completed_steps`, `failed_step`, `error`).
- **Constellation-based targeting** — every target-bearing action is
  stored as a structured element description (tag, ancestors,
  neighbors, children, semantic anchor), and a page-context scorer
  picks the best live match at run time. Skills survive class
  renames, runtime hashes, and minor DOM reshuffles.
- **Snapshot-based retrieval** — when the objective is retrieval-style
  ("find …", "get …", "look up …"), the identify stage locates the
  value-bearing element on the final DOM snapshot and synthesize
  appends a `browser_get_element_text` /
  `browser_get_element_attribute` step with a declared command output.
- **Provider-agnostic LLM** — `LLM_MODEL` selects the model; LiteLLM
  routes the call. OpenAI, Anthropic, Google, xAI, and MiniMax are
  documented out of the box.

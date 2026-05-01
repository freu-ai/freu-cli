# Skill format

A skill is a folder containing exactly two kinds of files, plus one
`log_<unix_timestamp>/` subfolder per `freu-cli learn` run:

```
MySkill/
├── SKILL.md                  # one file, human-readable
├── <Command>.json            # one file per command in the skill
└── log_<unix_timestamp>/     # capture + learn-pipeline intermediates
    ├── events.json
    ├── normalized.json
    ├── resolved.json
    ├── identified.json       # retrieval plan (which value, if any, the skill returns)
    └── synthesized.json      # final skill, constellations bound into DSL
```

Running `freu-cli learn` again on the same folder is *additive*:
new commands are appended to `SKILL.md`; existing commands with the
same name are replaced in place; and each run gets its own fresh
`log_<ts>/` folder so older traces are never overwritten.

## `SKILL.md` structure

```markdown
---
name: <skill_name>
description: >
  <one or two sentences describing what the skill does and when to use it>
version: 1.0.0
---

# <Skill Title>

<The skill description paragraph, usually the same sentence as in the frontmatter.>

## <CommandName>

<One or two sentences describing what this command does and when to use it.>

### CLI
freu-cli run <SkillName> <CommandName> --<arg-name> <value>

### Arguments
- **<arg_name>** → <description>

### Outputs
- **<output_name>** → <description>
```

### Rules

- **Frontmatter is required.** The `---` block at the top declares `name`
  (used as the skill identifier), `description` (a short summary for
  humans and agents), and `version`.
- **`# <Skill Title>` is a one-off block** at the top. The parser skips it
  when looking up commands.
- **Each `## <CommandName>` section starts a new command.** Command names
  should be PascalCase so they look natural as CLI arguments.
- Inside a command section, three H3 subsections are recognized:
  - **`### CLI`** — a one-line `freu-cli run ...` invocation that lists
    every argument as a `--kebab-case-arg <value>` flag. This is
    documentation, not the source of truth — the command's DSL steps live
    in the sibling `<CommandName>.json`.
  - **`### Arguments`** — bullet list. Each line reads `- **name** → description`.
    Use a single `-` to mark a command that takes no arguments.
  - **`### Outputs`** — bullet list in the same format. Use `-` when the
    command has no outputs.

## `<Command>.json` structure

The DSL steps for each command live in a file next to `SKILL.md`, named
`<CommandName>.json`. The parser resolves it by command name.

The top-level `arguments` and `outputs` arrays mirror the `### Arguments`
and `### Outputs` sections of `SKILL.md` so the JSON file is
self-describing — programmatic consumers can read what a command needs
without parsing markdown. `SKILL.md` remains the source of truth that the
runtime parser reads at execution time.

```json
{
  "arguments": [
    {"name": "repo_url", "description": "Full URL of the repository"}
  ],
  "outputs": [],
  "steps": [
    {
      "method": "browser_open_url",
      "description": "Open the repository page for {{repo_url}}.",
      "arguments": [
        {"name": "url", "source": "repo_url"}
      ],
      "event_ids": ["e1"]
    },
    {
      "method": "browser_click_element",
      "description": "Click the Star button on the repository page.",
      "arguments": [
        {
          "name": "target",
          "value": {
            "tag": "button",
            "attrs": {"data-action": "star", "aria-label": "Star"},
            "text": "Star",
            "ancestors": [{"tag": "main"}, {"tag": "ul", "classes": ["pagehead-actions"]}]
          }
        }
      ],
      "event_ids": ["e6"]
    },
    {
      "method": "browser_get_element_text",
      "description": "Read the repo title to capture the repository name.",
      "arguments": [
        {"name": "target", "value": {"tag": "h1", "attrs": {"id": "repo-title"}}}
      ],
      "outputs": [
        {"name": "repo_title", "value": "text"}
      ],
      "event_ids": ["e7"]
    }
  ]
}
```

Each argument carries exactly one of:

- `"value": <literal>` — hardcoded string, number, boolean, or
  constellation dict (see below).
- `"source": "<var>"` — references a command argument (declared in the
  `### Arguments` block) OR an earlier step's output. The parser
  normalizes this into `"value": "{{var}}"` so the runtime template
  renderer can resolve it.

`description` is a one-line, agent-facing explanation of what the step
does or expects. The runtime prints it instead of the raw method+args
during execution, and surfaces it on failure (alongside the descriptions
of completed steps) so a calling agent can recover with context. It is
required: every action step must carry a non-empty description.

`event_ids` records the capture events that motivated a step. It is
mandatory for target-bearing steps (synthesize uses it to attach the
learned constellation) and optional otherwise.

Outputs declare the mapping from a method's result fields (see the DSL
reference below) into the execution context, where subsequent steps can
reference them as `{{name}}`.

## Constellation: the `target` argument shape

Every target-bearing method takes a single `target` argument whose
value is a **constellation** — a structured description of the
clicked element and its surroundings. The runtime scores every live
candidate on the current page against the constellation and picks the
best match.

```json
{
  "tag": "button",
  "id": null,
  "classes": ["btn-primary"],
  "attrs": {"data-action": "star", "aria-label": "Star"},
  "text": "Star",
  "x": 850, "y": 95, "w": 60, "h": 28,
  "x_rel": 0.664, "w_rel": 0.047,
  "ancestors": [
    {"tag": "html"},
    {"tag": "body"},
    {"tag": "main"},
    {"tag": "ul", "classes": ["pagehead-actions"]},
    {"tag": "li"}
  ],
  "neighbors": [
    {"tag": "span", "text": "1,234"},
    {"tag": "button", "text": "Watch"}
  ],
  "children": [
    {"tag": "svg"},
    {"tag": "span", "text": "Star"}
  ],
  "special": {
    "role": "list",
    "tag": "ul",
    "classes": ["pagehead-actions"]
  }
}
```

| Field          | What it describes                                                                     |
|----------------|---------------------------------------------------------------------------------------|
| `tag`          | Element tag name, lowercase. Hard requirement: scoring only considers candidates of the same tag. |
| `id`           | Element id, or `null` if none (or the id was auto-generated and pruned).              |
| `classes`      | Stable class names. Auto-generated hashes (`css-1abc23`, `sc-…`, etc.) are pruned by the resolve stage. |
| `attrs`        | Whitelisted semantic attrs (`role`, `name`, `type`, `aria-label`, `data-testid`, `data-action`, `href`, `placeholder`, `for`). |
| `text`         | Short normalized text content (`textContent`, whitespace-collapsed, ≤ 120 chars).     |
| `x, y, w, h`   | Viewport-relative bounding rect at capture time, rounded to integer pixels.           |
| `x_rel, w_rel` | Horizontal position / width as a fraction of viewport width (0..1).                   |
| `ancestors`    | `[root, …, parent]` chain, each an object with the same fields as the target.         |
| `neighbors`    | Visible elements within 50 px of the target (edge-to-edge gap). Up to 16 entries.     |
| `children`     | Direct children (same shape). `null` if the target has more than 20 children.         |
| `special`      | Tag-specific semantic anchor: a `<label>` for inputs, a `<select>` around options, a `<ul>`/`<ol>` around list items, or a `<table>` around rows/cells. `null` otherwise. |

The runtime scorer weights every signal: exact id match and semantic
attrs score highest, followed by text similarity, class Jaccard, rect
similarity, and ancestor/neighbor/children/special context.
Auto-generated noise at capture time is tolerated: the resolve stage
strips the obvious stuff deterministically and uses the LLM for the
rest.

## DSL methods

| Method                                | Required args                          | Optional args                    | Outputs        |
|---------------------------------------|----------------------------------------|----------------------------------|----------------|
| `browser_open_url`                    | `url`                                  |                                  |                |
| `browser_click_element`               | `target`                               |                                  |                |
| `browser_fill_element`                | `target`, `text`                       |                                  |                |
| `browser_press_key`                   | `target`, `key`                        |                                  |                |
| `browser_wait_for_element`            | `target`, `timeout`                    |                                  |                |
| `browser_wait_for_element_count_stable` | `target`, `timeout`, `settle_time`   |                                  |                |
| `browser_verify_element`              | `target`                               |                                  |                |
| `browser_verify_element_negated`      | `target`                               |                                  |                |
| `browser_wait_for_url_contains`       | `text`, `timeout`                      |                                  |                |
| `browser_scroll`                      | `x`, `y`                               | `times`                          |                |
| `browser_get_element_text`            | `target`                               |                                  | `text`         |
| `browser_get_page_info`               |                                        |                                  | `title`, `url` |
| `browser_get_element_attribute`       | `target`, `attribute`                  |                                  | `value`        |
| `browser_collect_attribute`           | `target`, `attribute`                  | `value_contains`, `resolve_urls` | `values`       |

`timeout` values are in milliseconds.

## Control-flow steps

In addition to action steps, a skill may contain:

- **`for_each`** — iterate over a list. Children run once per item.

  ```json
  {
    "type": "for_each",
    "item_name": "row",
    "source": "{{rows}}",
    "result": "row_title",
    "output": "titles",
    "steps": [
      {"method": "browser_open_url", "arguments": [{"name": "url", "source": "row"}]},
      {"method": "browser_get_page_info", "arguments": [], "outputs": [{"name": "row_title", "value": "title"}]}
    ]
  }
  ```

- **`if`** — branch on a boolean output of a condition step.

  ```json
  {
    "type": "if",
    "condition": {
      "method": "value_is_true",
      "arguments": [{"name": "value", "source": "should_continue"}],
      "outputs": [{"name": "ok", "value": "ok"}]
    },
    "steps": [
      {
        "method": "browser_click_element",
        "arguments": [
          {"name": "target", "value": {"tag": "button", "attrs": {"aria-label": "Next"}}}
        ],
        "event_ids": ["e8"]
      }
    ]
  }
  ```

## Variable resolution

`source` must be a bare identifier — the name of a command argument or
an earlier step's output — OR an already-wrapped `{{name}}`:

- `"source": "repo_url"` → `{{repo_url}}`
- `"source": "{{repo_url}}"` → kept as-is

Anything else (dotted paths, prefixed forms) is passed through
unchanged, so template rendering will raise a clear
`Missing template variable: <name>` at runtime.

At render time, `{{name}}` is looked up in the execution context
(inputs + prior outputs). An unresolvable reference fails fast.
Constellation dicts stored as `target` values are NOT templated —
they travel through the parser and executor untouched.

## Runtime output

`freu-cli run` prints one line per executed step, prefixed with a flat
`Step N:` counter that increments across nested control flow:

```
Step 1: Open the repository page. (Opening https://github.com/freu-ai/freu-cli)
Step 2: Click the Star button on the repository page. (Clicking <button> 'Star')
Step 3: Read the repository title. (Reading text from <h1>)
  → stored output 'repo_title' from 'text' = 'freu-ai/freu-cli'

OK
```

The text before `(...)` is the step's `description`. The parenthetical
is a parameter-aware summary of the underlying browser call, so the
operator can correlate "what we wanted to do" with "what we actually
did".

`for_each` and `if` headers consume their own `Step N:` slot; nested
action steps continue the same counter. Output capture lines (post-step
side effects like `stored output …`) are indented with `→` and don't
consume a step number.

### Failure output

When a step fails mid-run, `freu-cli run` swaps the raw exception for an
agent-facing recovery block:

```
FAILED.

Completed steps:
  1. Open the repository page.
  2. Focus the global search input.

Pending step:
  Click the Star button on the repository page.

Reason: element not found: button[data-action=star]
```

The numbering in `Completed steps:` aligns with the live `Step N:` log
— a calling agent can match the failure summary to the live transcript
directly. The same data is available programmatically on the result
dict returned by `freu_cli.run.executor.run_skill` / `run_file`:
`completed_steps: list[str]`, `failed_step: str | None`, plus the
existing `error` / `error_type` / `step` / `method` fields.

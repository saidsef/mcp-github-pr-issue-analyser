---
description: Ask the user to pick an option, or render GitHub data as an interactive UI panel
---

# Interactive UI

Two ways to put something in front of the user: a set of buttons to choose from, or a rendered panel built from Prefab components.

## Prerequisites

- A client that renders MCP UI. Text-only clients will not show these
- No GitHub token, since neither tool calls GitHub

## Choosing Between Them

`choose` when the answer is one of a few known options and you need the user to
pick. `github_pr_issue_analyser_ui` when you have data worth showing as
something other than a wall of text.

Neither replaces a plain answer. A single fact belongs in the reply.

## `choose`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `prompt` | str | - | The question put to the user |
| `options` | list[str] | - | The options to show as buttons |
| `title` | str \| None | `None` | Heading for the card, falls back to the provider default |

The user clicks one option and the selection comes back into the conversation
as a message. Nothing is returned to you directly, so treat the click as the
user's next turn and carry on from it.

This is the right way to get confirmation before anything irreversible. The
merge step in the pr-management skill requires an explicit yes from the user,
and `merge_pr` does not prompt on its own, so ask here first:

```
choose(
  prompt="Merge PR #123 into main with a squash commit?",
  options=["Merge it", "Not yet"],
  title="Confirm merge",
)
```

It also suits picking a target when a search returned several, such as which of
four open PRs to review, or which release version to tag.

Keep the options short enough to read on a button and make them distinct. Do
not offer an option you are not prepared to act on.

## `github_pr_issue_analyser_ui`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `code` | str | - | Prefab Python source to execute |
| `data` | str \| dict \| None | `None` | Initial data, as a dict or a JSON string |

The code runs in a Pyodide WASM sandbox and renders as a streaming UI. Import
everything you use, and make `PrefabApp` the outermost context manager so the
panel renders progressively as the code is written:

```python
from prefab_ui.app import PrefabApp
from prefab_ui.components import Badge, Column, Heading, Row, Text

with PrefabApp() as app:
    with Column(gap=4):
        Heading("PR #123")
        with Row(gap=2):
            Text("12 files changed")
            Badge("CI passing", variant="success")
```

For an interactive panel, pass initial state and bind to it with `.rx` on
stateful components.

Good uses here are the shapes that read badly as prose: a PR review summary
with per-file findings, a status-check breakdown, a user's contribution
activity over a quarter, or a table of repos ranked by new stars.

## `search_prefab_components`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | str | `""` | Component name or description, space-separated terms are OR-matched |
| `detail` | bool \| None | `None` | Force full docstrings and args, otherwise automatic |
| `limit` | int \| None | `None` | Cap on components returned in detail mode, default 8 |

Look up exact argument names and accepted values before writing component code.
A query matching five or fewer components returns full detail, and a broader
one returns a compact listing.

This skill covers when to build a panel and what to put in it. The component
API lives in this tool, so call it rather than guessing at argument names.

## Best Practices

- Ask with `choose` before calling `merge_pr`, and before anything else that cannot be undone
- Treat a `choose` click as the user's answer, since the tool returns nothing to you
- Call `search_prefab_components` before writing Prefab code rather than guessing component arguments
- Keep `PrefabApp` as the outermost context manager so the panel streams
- Fall back to plain text when the client does not render UI
- Do not render a panel for a single fact

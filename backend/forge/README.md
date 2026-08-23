# forge

A small agent core: tools the model can call, a context window that stays
inside its budget, a plan a human approves, and skills loaded only when the
task needs them. The builder and the QA agent are written on top of it.

Every file here is under 180 lines and does one thing.

## The map

| File | What it is |
| --- | --- |
| `tools/paths.py` | the sandbox — nothing outside the project, nothing from `node_modules` |
| `tools/spec.py` | what a tool is, its JSON function schema, and the cap on its output |
| `tools/read.py` | `list_files` (the `ls`), `read_file`, `grep`, `tree` |
| `tools/write.py` | `write_file`, `edit_file`, `delete_file` |
| `tools/shell.py` | `run_command`, behind an allow-list |
| `tools/registry.py` | dispatch, and the refusal when a mode does not allow a tool |
| `tokens.py` | estimating a window without a tokenizer |
| `context.py` | the window: system prompt and goal pinned, the rest is working history |
| `compaction.py` | trim tool output → drop repeats → summarise → clip |
| `modes.py` | plan mode (read-only) and build mode (writes) |
| `plan.py` | the plan, parsed out of markdown, with the approval gate |
| `loop.py` | ask, run what it asked for, hand back what happened |
| `skills/` | Next.js, Vitest and Playwright guides, loaded on a trigger match |
| `builder/` | scaffold, plan, build |
| `qa/` | Vitest and Playwright, run and repaired |
| `events.py` | agent events → the websocket messages the UI already knows |
| `service.py` | the pipeline that puts those in order |

## How a run goes

```
scaffold ─► PLAN MODE ─► approval ─► BUILD MODE ─► unit ─► e2e
           read-only     human        writes       vitest  playwright
           tools only    or gate      + shell      + repair rounds
```

**Plan mode** hands the model only `list_files`, `read_file`, `grep` and
`tree`. Asking for `write_file` there comes back as a refusal, not a write, so
a plan cannot quietly become a build. `plan.py` reads the four sections out of
the answer, and `Plan.approve()` is the only thing that unlocks `build()` —
`BuilderAgent.build` raises on an unapproved plan.

**Context.** `Conversation` pins the system prompt and the original goal;
everything else is working history. When a turn would go over budget,
`compact()` runs the cheap steps first — cut older tool output down to its
opening lines, collapse repeated tool answers — and only then summarises the
middle into a rolling note. A single message larger than the whole budget is
halved in place rather than dropped, because dropping is how an agent forgets
what it already tried.

**Skills** are markdown with front-matter triggers. `skills.for_task(text)`
scores them against the task and renders at most three, so a build prompt
carries the Next.js guide and a QA prompt carries the Vitest or Playwright one
— never all of them at once.

**QA** authors the suite, runs it, and repairs what it catches. It stops when
the same cases fail two rounds running and says so, rather than looping. It
never quarantines a test to reach green.

## Using it

```python
from forge import Pipeline
from forge.llm import Model

result = Pipeline("/path/to/project", Model("qwen3-coder:480b-cloud"),
                  emit=send_to_websocket).run("an item tracker with a form")
print(result.summary())     # built 6 file(s) · unit: 14 passed, 0 failed · …
```

The approval gate is a callable taking the plan and returning `True` to build,
a string to send it back with that note, or `False` to stop.
`server_modules/forge/bridge.py` provides one driven from the websocket.

## Tests

```
tests/test_v64_forge_tools_and_modes.py     the sandbox, the allow-list, mode gating
tests/test_v65_forge_context_and_skills.py  budgets, compaction, skill selection
tests/test_v66_forge_builder_and_qa.py      the loop, plan approval, QA repair, pipeline
```

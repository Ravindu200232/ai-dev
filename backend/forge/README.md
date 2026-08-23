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
| `edit/` | changing a project that already exists — the loop feature, select and pencil run on |
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

## Editing an existing project

`forge/edit/` is the same loop pointed at a project that is already there, and
it is what the feature, element-select and pencil agents run on. Three things
make it different from the builder:

- **Writes go through the host.** The architect's `write_file` merges
  `package.json`, canonicalises a path, removes a shadowed `.js`/`.jsx` twin
  and emits the file event the studio draws. Writing straight to disk would
  lose all of that, so `host_write_tools` delegates. A caller that guards its
  own writes passes `writer=`; one that must inspect a rewrite before allowing
  it passes `capture=` and gets the content without the change happening.
- **No shell.** An edit changes files; it does not build them.
- **Sessions.** An analysis that converges over rounds calls `session(...)`
  once and then `loop.run(note)` per round, so the request stays pinned and
  the history compacts instead of growing until it hits a character ceiling.

## The server

`forge/server/` is the whole backend. `server.py` starts it.

| File | What it is |
| --- | --- |
| `state.py` | where projects live, and what the studio asks about them |
| `api.py` | the JSON routes under `/__agentforge/api` |
| `http.py` | the HTTP handler, proxying anything else to the studio |
| `ws.py` | the socket protocol: build, decide, cancel, edit |
| `runs.py` | starting, answering and stopping one run |
| `gate.py` | the approval a build blocks on |
| `events.py` | the one place a message to the studio goes through |
| `app.py` | starting both servers |

Socket messages:

| message | what it does |
| --- | --- |
| `{type: "forge_build", prompt, model, qa_model, kinds}` | starts a build |
| `{type: "plan_decision", project, verdict, note}` | answers the plan — `approve`, `revise` (with a note) or `reject` |
| `{type: "chat", project, prompt, files}` | changes a project that already exists |
| `{type: "cancel"}` | stops the run at its next checkpoint |

A build emits `plan_review` when its plan is ready and blocks until the studio
answers; the studio draws it as the *Plan ready* panel. Forge's phases are
grouped onto the two stages the overlay draws.

## Tests

```
tests/test_v64_forge_tools_and_modes.py     the sandbox, the allow-list, mode gating
tests/test_v65_forge_context_and_skills.py  budgets, compaction, skill selection
tests/test_v66_forge_builder_and_qa.py      the loop, plan approval, QA repair, pipeline
```

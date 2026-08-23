# AgentForge maintenance map

The backend is the forge pipeline and the server that drives it. Everything
else was removed in the cutover.

## Layout

- `server.py` — the entrypoint. Starts `forge.server`.
- `forge/` — the agent core and everything built on it. See `forge/README.md`.
- `forge/server/` — HTTP for the studio's API, a websocket for a build.
- `tests/` — the suites for all of it.
- `production-ready/` — where built projects are written.
- `scripts/` — desktop packaging helpers, unrelated to the backend.

## Sidecars

- `srs-agent/` and `deployment-agent/` are separate services with their own
  dependencies. `forge/server/sidecars.py` starts each in a thread and keeps
  going when one cannot be imported — a missing sidecar is not a reason to be
  unable to build anything. `/__agentforge/api/srs-status` reports both.

## Rules

- Keep source files below 200 lines. Put new code in the narrowest module.
- A change that cannot be verified is not finished: run the suite, and run
  the pipeline against a real project when the change touches it.
- Never report a run green when a stage did not run.
- Prefer evidence over inference — read the file before changing it.

# Server modules

`server.py` is the public entrypoint. `server_runtime.py` loads the runtime modules in an explicit order.

- `core/` — process and server lifecycle.
- `srs/` — SRS handoff and API.
- `qa/` — verification and E2E flow.
- `agent/` — build, repair and editing workflows.
- `deploy/` — deployment logic.
- `ui/` — HTTP handling.
- `forge/` — the forge pipeline: model wiring, event relay and the plan gate
  the UI answers. Unlike the parts above it is a normal importable module, not
  a runtime fragment, so it can be tested on its own.

Keep files focused, below 1000 lines, and preserve the public server contract.

# Server modules

`server.py` is the public entrypoint. `server_runtime.py` loads the runtime modules in an explicit order.

- `core/` — process and server lifecycle.
- `srs/` — SRS handoff and API.
- `qa/` — verification and E2E flow.
- `agent/` — build, repair and editing workflows.
- `deploy/` — deployment logic.
- `ui/` — HTTP handling.
- `forge/` — the forge pipeline. `bridge.py` is a normal importable module
  (models, event relay, plan gate) so it can be tested on its own; `stage.py`
  is the runtime fragment that `server_runtime.py` loads, and it is what puts
  `run_forge_pipeline` and `forge_decide` in the shared namespace.

Keep files focused, below 1000 lines, and preserve the public server contract.

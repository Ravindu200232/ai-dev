# Server modules

`server.py` is the public entrypoint. `server_runtime.py` loads the runtime modules in an explicit order.

- `core/` — process and server lifecycle.
- `srs/` — SRS handoff and API.
- `qa/` — verification and E2E flow.
- `agent/` — build, repair and editing workflows.
- `deploy/` — deployment logic.
- `ui/` — HTTP handling.

Keep files focused, below 1000 lines, and preserve the public server contract.

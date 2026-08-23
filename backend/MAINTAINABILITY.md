# AgentForge maintenance map

The backend is a Go binary. The SRS and deployment agents stay in Python and run
as subprocesses of it, on the ports they always used.

## Backend — `agent/` (Go module `agentforge/agent`)

- `cmd/agentforge/` — process lifecycle: MongoDB, the two Python sidecars, the
  HTTP and WebSocket listeners, shutdown.
- `core/` — the shared kernel, and the only package with no internal imports.
  - `run.go` — run state, project paths, the WebSocket event vocabulary, the
    streaming file writer.
  - `shell.go` — the agent's whole tool surface: `Ls`, `Tree`, `Read`, `Write`,
    `Grep`, `Exec`, `Start`, `Survey`. Every path is resolved inside the project.
  - `llm.go` — settings, per-role model routing, retries, the JSON repair loop.
- `builder/` — the build graph: `intake → plan → build ⟲ → coverage ⟲` and then
  the QA stages.
- `qa/` — runtime boot and repair, the unit rounds, the API probes, the browser
  journeys, performance and security.
- `app/` — the app summary, and the selection, pencil, feature and repair flow
  that reads it.
- `server/` — the Studio's two surfaces: `ws.go` (7825), `http.go` (7824), plus
  `sidecar.go` for the Python services and MongoDB.

## Python services

- `srs-agent/` — SRS generation. FastAPI + LangGraph on 7826.
- `deployment-agent/` — deployment orchestration on 7834.
- Each reaches the rest of the system only through its own `bridge.py`, which
  reads `~/.agentforge/settings.json` and the environment the parent sets.

## Ports

`5173` the generated app · `7824` API · `7825` WebSocket · `7826` SRS ·
`7834` deploy. `studio/next.config.js` and `studio/lib/ws.js` depend on these.

## Rules

- Keep source files under 850 lines, and most of them nearer 400. When one grows
  past that the split is usually a missing concept, not a file boundary. The two
  largest are `server/http.go`, which is one endpoint table and has to be, and
  `core/run.go`, which is the state plus the event vocabulary every package
  emits — splitting either would cost more in indirection than it saves.
- Seventeen source files is the whole backend. Reach for a new one only when a
  new concept arrives, not when an existing file gets long.
- `core` never imports another package in this module. Everything else may
  import `core`.
- Every phase re-runs `core.Refresh` before it decides anything. Do not carry a
  file listing forward between phases.
- The WebSocket event names and their fields are a contract with `studio/`.
  `server/server_test.go` pins them; add a field freely, never rename one.
- QA rounds are sequential. A repair round has to see the failure the previous
  round left behind.
- Prefer evidence-backed repairs over broad rewrites: read the file before
  changing it, and change only what the failure names.
- Run `go build ./... && go vet ./... && gofmt -l . && go test ./...` before
  pushing.

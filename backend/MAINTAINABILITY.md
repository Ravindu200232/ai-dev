# AgentForge maintenance map

The backend is a Go binary. The SRS agent is part of it. The deployment agent is
still Python and runs as a subprocess, on the port it always used.

## Backend — `agent/` (Go module `agentforge/agent`)

- `cmd/agentforge/` — process lifecycle: MongoDB, the SRS service, the Python
  deployment sidecar, the HTTP and WebSocket listeners, shutdown.
- `core/` — the shared kernel, and the only package with no internal imports.
  - `run.go` — run state, project paths, the WebSocket event vocabulary, the
    streaming file writer.
  - `shell.go` — the agent's whole tool surface: `Ls`, `Tree`, `Read`, `Write`,
    `Grep`, `Exec`, `Start`, `Survey`. Every path is resolved inside the project.
  - `llm.go` — settings, per-role model routing, retries, the JSON repair loop.
- `builder/` — the build graph: `intake → plan → build ⟲ → coverage ⟲` and then
  the QA stages.
- `qa/` — runtime boot and repair, the unit rounds, the API probes, the browser
  journeys, performance and security, and `report.go` for the PDF.
- `app/` — the app summary and the selection, pencil, feature and repair flow
  that reads it, plus `picture.go` (Fooocus, uploads, attachments) and
  `design.go` (whole-app visual directions and logo prompts).
- `srs/` — idea → interview → approved plan → specification → diagrams → PDF →
  builder handoff, and the edits after it. `service.go` is its HTTP surface,
  served in-process under `/srs/*`; `orchestrate.go` is what each endpoint
  does; `graph.go` holds the three langgraphgo graphs. `data/*.json` is the
  domain knowledge, embedded.
- `server/` — the Studio's two surfaces: `ws.go` (7825), `http.go` (7824), plus
  `sidecar.go` for the deployment service and `mongo.go`, which fetches and runs
  mongod when the machine has none.

## Python services

- `deployment-agent/` — deployment orchestration on 7834. It reaches the rest of
  the system only through its own `bridge.py`, which reads
  `~/.agentforge/settings.json` and the environment the parent sets.

## Ports

`5173` the generated app · `7824` API · `7825` WebSocket · `7834` deploy.
`studio/next.config.js` and `studio/lib/ws.js` depend on these. The SRS has no
port of its own any more: it is served under `/srs/*` on 7824.

## Rules

- Keep source files under 850 lines, and most of them nearer 400. When one grows
  past that the split is usually a missing concept, not a file boundary. The two
  largest are `server/http.go`, which is one endpoint table and has to be, and
  `core/run.go`, which is the state plus the event vocabulary every package
  emits — splitting either would cost more in indirection than it saves.
- Reach for a new file only when a new concept arrives, not when an existing
  one gets long. `agent/` outside `srs/` is twenty-one files and should stay
  that way; `srs/` is growing as the Python SRS service is ported into it.
- `core` never imports another package in this module. Everything else may
  import `core`.
- Nothing the SRS produces may exceed the approved plan. The plan's records
  become the tables, its users the roles, its screens the pages; a model's
  answer is merged onto that, never substituted for it.
- A diagram the specification cannot support says so. Never draw a plausible
  one — a reader cannot tell an invented lifecycle from a real one.
- Every phase re-runs `core.Refresh` before it decides anything. Do not carry a
  file listing forward between phases.
- Listing a directory runs the platform's own `ls` or `dir` and parses it. The
  fallback to reading the directory is for a machine with no shell, not the
  normal path.
- Nothing slow may run before the listeners are up. Fetching mongod is ~90 MB;
  it happens in the background and reports through `/mongo`.
- The WebSocket event names and their fields are a contract with `studio/`.
  `server/server_test.go` pins them; add a field freely, never rename one.
- QA rounds are sequential. A repair round has to see the failure the previous
  round left behind.
- Prefer evidence-backed repairs over broad rewrites: read the file before
  changing it, and change only what the failure names.
- Run `go build ./... && go vet ./... && gofmt -l . && go test ./...` before
  pushing.

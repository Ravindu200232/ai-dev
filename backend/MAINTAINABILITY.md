# AgentForge maintenance map

The backend is one Go binary. Everything is in it: the builder, QA, the SRS
agent and the deployment agent. There is no Python, and no subprocess but the
database.

## Backend — `agent/` (Go module `agentforge/agent`)

- `cmd/agentforge/` — process lifecycle: MongoDB, the SRS and deployment
  agents, the HTTP and WebSocket listeners, shutdown.
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
- `deploy/` — project → plan → generated files → validation → AWS or Vercel →
  monitoring. `service.go` is its HTTP surface, served in-process under
  `/deploy/*`; `graph.go` is the analysis graph; `assets/` are the files it
  generates, kept as files. See below.
- `server/` — the Studio's two surfaces: `ws.go` (7825), `http.go` (7824),
  `deploy.go` for the deployment agent's seam into it, `jobs.go` for slow work
  the Studio polls, and `mongo.go`, which fetches and runs mongod when the
  machine has none.

## Deployment — `agent/deploy/`

Read it in the order a run happens: `intake` (what the project is) → `plan`
(how it will be deployed) → `patch` and `generate` (the files that carry it
out) → `validate` (whether they are safe) → `graph` (all of that, plus the
local build) → `deployer` (the gates and the sequence) → `aws`, `github`,
`vercel` (the world) → `monitor` (what happened) → `lifecycle` (undoing it) →
`export` (what it leaves behind).

- Every AWS call goes through the customer's own `aws` CLI, in `aws.go`. There
  is no second credential path and no SDK: a profile is named, or an SSO
  session's keys are handed to one process through its environment.
- `assets/` holds the generated files as files — two CloudFormation templates,
  three workflows, a Dockerfile, a release script, a report and the source
  patches. They are rendered with `text/template` using `<% %>` delimiters,
  because the output is GitHub Actions YAML (`${{ }}`) and shell (`{}`).
- `testdata/` is what the Python agent produced for the same project, captured
  before it was removed. `generate_test.go` regenerates and compares byte for
  byte; `testdata/README.md` records the two files that deliberately differ.

## Ports

`5173` the generated app · `7824` API · `7825` WebSocket.
`studio/next.config.js` and `studio/lib/ws.js` depend on these. The SRS and the
deployment agent have no port of their own any more: they are served under
`/srs/*` and `/deploy/*` on 7824.

## Rules

- Keep source files under 850 lines, and most of them nearer 400. When one grows
  past that the split is usually a missing concept, not a file boundary. The two
  largest are `server/http.go`, which is one endpoint table and has to be, and
  `core/run.go`, which is the state plus the event vocabulary every package
  emits — splitting either would cost more in indirection than it saves.
  Nothing else is over 850.
- Reach for a new file only when a new concept arrives, not when an existing
  one gets long.
- `core` never imports another package in this module. Everything else may
  import `core`. `srs` and `deploy` do not import each other.
- Nothing the SRS produces may exceed the approved plan. The plan's records
  become the tables, its users the roles, its screens the pages; a model's
  answer is merged onto that, never substituted for it.
- A diagram the specification cannot support says so. Never draw a plausible
  one — a reader cannot tell an invented lifecycle from a real one.
- A deployment decides nothing a model told it. The commands, the port, the
  variables and the service root are read off disk before the model is asked
  anything; what comes back is filtered against a list of what is allowed.
- Nothing generated may carry a value. `.env.example` names variables and never
  holds one, the git index is checked for `.env` files before any commit, and
  the only place a value is ever written is Secrets Manager.
- Never decide anything on redacted text. Redaction rewrites what it is given,
  so a pattern that matches one line can swallow the next: `Command.Plain` is
  for the reads that make a decision, and everything else is redacted. A secret
  is passed to a program on its standard input, never as an argument — an
  argument is readable by every process on the machine. Where a platform has no
  standard-input path — Windows has no `/dev/stdin` — it goes into a file that
  is deleted the moment the command returns.
- Redaction never changes how many lines a string has, and masking is applied
  wherever redaction is. Both live in `deploy/secret.go`: redaction removes
  secrets, masking removes the account number and the image digest, which are
  not secrets but are the customer's. Everything that leaves the process gets
  both — the Studio, the evidence bundle, and the record left in the project.
- Editing a customer's JavaScript is done against the file with its comments
  blanked out (`codeOnly`), never against the raw text. A bracket in a trailing
  comment or a `/*` inside a glob string is how an edit lands in the middle of
  an import or inside a comment, and the file that comes back does not parse.
  When the walk cannot tell where it is, insert somewhere always valid rather
  than somewhere guessed.
- Every phase re-runs `core.Refresh` before it decides anything. Do not carry a
  file listing forward between phases.
- Listing a directory runs the platform's own `ls` or `dir` and parses it. The
  fallback to reading the directory is for a machine with no shell, not the
  normal path.
- Nothing slow may run before the listeners are up. Fetching mongod is ~90 MB;
  it happens in the background and reports through `/mongo`.
- The WebSocket event names and their fields are a contract with `studio/`.
  `server/server_test.go` pins them; add a field freely, never rename one.
- The Deploy tab's HTTP shape is a contract too, and a silent one: a missing
  field does not error, it just leaves the tab blank or the button grey.
  `TestDeployTabContract` pins `agent.listening`, the `settings` block on
  `/deploy-results`, and `/settings.deploy`. The panel reads a finished run's
  provider block as `repo_state` and draws its pipeline from the live run's
  `events`.
- The database a deployment uses is `deploy_mongodb_uri`, never the
  `mongodb_uri` AgentForge runs for itself — that one is usually on this
  machine, and an app in a cloud cannot reach it. `CheckMongoURI` refuses a
  loopback address whichever of the two it came from.
- A run only blocks another one while it is doing something. `Active` is that
  question; `!Terminal` is not — a run abandoned in `DRAFT` or `REVIEW_READY`
  is swept up by nothing, and gating on it leaves a project's Deploy button
  refusing for good.
- The agent commits the project and nothing else. A pathspec's `**/` needs a
  directory to match against, so every exclusion in `github.go` takes a second
  pattern for the repository root — that is how a root `.env` was once staged.
  `.agentforge/` is the agent's own record and is never committed.
- QA rounds are sequential. A repair round has to see the failure the previous
  round left behind.
- Prefer evidence-backed repairs over broad rewrites: read the file before
  changing it, and change only what the failure names.
- Run `go build ./... && go vet ./... && gofmt -l . && go test ./...` before
  pushing.

# AgentForge maintenance map

Public entrypoints stay small. Implementation lives in focused modules and existing imports remain stable.

## Backend

- `server.py` — public backend entrypoint.
- `server_runtime.py` — ordered runtime assembler.
- `server_modules/core/` — startup, shutdown and dev-server lifecycle.
- `server_modules/srs/` — SRS handoff and sidecar API.
- `server_modules/qa/` — unit, runtime, API, security and E2E orchestration.
- `server_modules/agent/` — build, repair, feature, image and editor workflows.
- `server_modules/deploy/` — deployment orchestration and jobs.
- `server_modules/ui/` — HTTP handling.
- `server_modules/forge/` — the forge pipeline, its websocket plan gate and
  the `forge_build` / `plan_decision` entry points.

## Pipeline

- `pipeline.py` — compatibility facade.
- `server_modules/agent/pipeline/` — watcher, runner and dev-server logic.
- `pipeline_core/` — compatibility imports for older callers.

## Supporting packages

- `forge/` — the small agent core: tools, context budget, plan mode, skills,
  and the builder and QA agents written on top of them. See `forge/README.md`.
- `agents/` — architect, analyzer, builder, repair and project helpers.
- `qa_agent/` — test authoring, browser execution, evidence and repair support.
- `srs-agent/` — SRS service.
- `deployment-agent/` — deployment service.

## Rules

- Keep source files below 1000 lines. New code in `forge/` stays below 180.
- Put new code in the narrowest matching module.
- Preserve public imports when moving implementation.
- Keep comments short and useful.
- Prefer evidence-backed repairs over broad rewrites.
- Validate build, runtime and E2E behavior after structural changes.

#!/usr/bin/env python3
"""Load the server modules into the legacy shared runtime namespace."""
from __future__ import annotations

from pathlib import Path

# Order matters because these files share one runtime namespace.
_RUNTIME_PARTS = (
    'server_modules/core/bootstrap.py',
    'server_modules/core/dev_runtime.py',
    'server_modules/core/build_entry.py',
    'server_modules/agent/build_repair.py',
    'server_modules/qa/unit_support.py',
    'server_modules/agent/design_stage.py',
    'server_modules/srs/srs_runtime.py',
    'server_modules/deploy/deploy_runtime.py',
    'server_modules/qa/unit_stage.py',
    'server_modules/qa/e2e_prepare.py',
    'server_modules/qa/e2e_stage.py',
    'server_modules/qa/e2e_final.py',
    'server_modules/qa/runtime_repair.py',
    'server_modules/agent/images.py',
    'server_modules/qa/verification.py',
    'server_modules/agent/chat_bugfix.py',
    'server_modules/agent/agent_pipeline.py',
    'server_modules/agent/feature_actions.py',
    'server_modules/agent/scope_map.py',
    'server_modules/agent/pencil_page.py',
    'server_modules/agent/project_ops.py',
    'server_modules/ui/http_base.py',
    'server_modules/ui/http_handler.py',
    'server_modules/srs/srs_api.py',
    'server_modules/deploy/deploy_api.py',
    'server_modules/core/jobs.py',
    'server_modules/deploy/jobs.py',
    'server_modules/core/main.py',
)


def _load_runtime_parts() -> None:
    root = Path(__file__).resolve().parent
    namespace = globals()
    for relative_path in _RUNTIME_PARTS:
        path = root / relative_path
        source = path.read_text(encoding="utf-8")
        exec(compile(source, str(path), "exec"), namespace, namespace)


_load_runtime_parts()

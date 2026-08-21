from __future__ import annotations

import json
import re
import secrets
import shutil
import textwrap
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from dfagents.monitor import select_workflow_run
from deployment_agent.environment import DEPLOYER_INJECTED
from deployment_agent.models import DeploymentTarget, RunState
from deployment_agent.config import ARTIFACT_SCHEMA_VERSION, GIT_TIMEOUT_SECONDS
from deployment_agent.providers import ProviderPrep, TargetProfile, profile_for, target_of
from deployment_agent.security import redact_text, sha256_file
from deployment_agent.state import StateStore
from deployment_agent.tools import command_exists, resolve_command, run_command


_ACTIVE_PROJECTS: set[str] = set()
_ACTIVE_LOCK = threading.RLock()
_PERSISTED_ACTIVE_STATES = {
    RunState.BOOTSTRAPPING.value,
    RunState.CI_RUNNING.value,
    RunState.DEPLOYING.value,
    RunState.VALIDATING.value,
}


_CANCELLABLE_STATES = _PERSISTED_ACTIVE_STATES | {
    RunState.ANALYZING.value,
    RunState.REVIEW_READY.value,
}


def github_credential_args() -> list[str]:
    """Use gh credentials for non-interactive git commands."""
    executable = resolve_command("gh")
    command = Path(executable).as_posix() if executable else "gh"
    return [
        "-c", "credential.https://github.com.helper=",
        "-c", f"credential.https://github.com.helper=!'{command}' auth git-credential",
    ]


def active_project_keys() -> set[str]:
    """Return project keys owned by live deployment workers."""
    with _ACTIVE_LOCK:
        return set(_ACTIVE_PROJECTS)


class DeploymentConflictError(RuntimeError):
    pass

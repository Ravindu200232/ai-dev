from __future__ import annotations

import difflib
import json
import re
import textwrap
from dataclasses import replace
from pathlib import Path
from typing import Callable

from deployment_agent.environment import (DEPLOYER_INJECTED,
                                          EnvironmentContractResolver)
from deployment_agent.providers import profile_for
from deployment_agent.models import (
    ArtifactRecord,
    DeploymentPlan,
    DeploymentTarget,
    EnvironmentContract,
    EnvironmentEntry,
    ProjectSpec,
    ServiceSpec,
)
from deployment_agent.config import ARTIFACT_SCHEMA_VERSION
from deployment_agent.security import sha256_file


_EAGER_CONNECT = re.compile(
    r"let\s+clientPromise\b.*?export\s+default\s+clientPromise",
    re.DOTALL,
)

_LAZY_CONNECT = """let clientPromise

// Reuse across HMR reloads so dev doesn't leak a connection per edit.
if (process.env.NODE_ENV === 'development' && global._mongoClientPromise) {
  clientPromise = global._mongoClientPromise
}

// Connect on first use, never during import.
// Next.js imports routes while building, when no database may exist.
function connection() {
  if (!clientPromise) {
    if (!uri) throw new Error('MONGODB_URI is not set')
    clientPromise = new MongoClient(uri).connect()
    if (process.env.NODE_ENV === 'development') {
      global._mongoClientPromise = clientPromise
    }
  }
  return clientPromise
}

/** Awaitable exactly like the promise it replaced. */
export default { then: (ok, no) => connection().then(ok, no) }"""

BUILD_PLACEHOLDER_MONGODB_URI = "mongodb://127.0.0.1:27017/deployment_agent_build"
BUILD_PLACEHOLDER_AUTH_SECRET = "build-only-placeholder-real-value-injected-at-runtime"
BUILD_PLACEHOLDER_AUTH_URL = "http://localhost:3000"


def generator_class():
    # Resolve the wrapper lazily to avoid import cycles.
    from .generator import ArtifactGeneratorAgent
    return ArtifactGeneratorAgent

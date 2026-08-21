from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class RunState(str, Enum):
    DRAFT = "DRAFT"
    ANALYZING = "ANALYZING"
    REVIEW_READY = "REVIEW_READY"
    BOOTSTRAPPING = "BOOTSTRAPPING"
    CI_RUNNING = "CI_RUNNING"
    DEPLOYING = "DEPLOYING"
    VALIDATING = "VALIDATING"
    LIVE = "LIVE"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"
    DESTROYED = "DESTROYED"

    CANCELLED = "CANCELLED"


ACTIVE_STATES = frozenset({
    RunState.BOOTSTRAPPING.value,
    RunState.CI_RUNNING.value,
    RunState.DEPLOYING.value,
    RunState.VALIDATING.value,
})


class DeploymentTarget(str, Enum):
    AWS_EC2 = "aws_ec2"
    VERCEL = "vercel"

    AWS_ECS = "aws_ecs"


class GateStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"


@dataclass
class EnvironmentVariable:
    name: str
    required: bool = True
    secret: bool = False
    scope: str = "runtime"
    sources: list[str] = field(default_factory=list)


@dataclass
class EnvironmentEntry:
    name: str
    required: bool
    secret: bool
    scope: str
    sources: list[str] = field(default_factory=list)
    resolution: str = "user_required"
    value_present: bool = False
    public: bool = False
    development_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EnvironmentContract:
    entries: list[EnvironmentEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"entries": [entry.to_dict() for entry in self.entries]}

    def by_name(self) -> dict[str, EnvironmentEntry]:
        return {entry.name: entry for entry in self.entries}

    def unresolved(self) -> list[EnvironmentEntry]:
        return [entry for entry in self.entries if entry.required and not entry.value_present]


@dataclass
class ValidationGate:
    id: str
    required: bool
    status: GateStatus
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class ServiceSpec:
    name: str
    root: str
    framework: str
    version: str
    package_manager: str
    install_command: str
    build_command: str
    start_command: str
    port: int = 3000
    health_path: str = "/api/health"
    routes: list[dict[str, str]] = field(default_factory=list)
    environment: list[EnvironmentVariable] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    scripts: dict[str, str] = field(default_factory=dict)
    lockfile: str = ""
    has_mongodb: bool = False
    has_better_auth: bool = False


@dataclass
class RepositorySpec:
    is_git: bool
    branch: str = "main"
    remote: str = ""
    dirty_files: list[str] = field(default_factory=list)


@dataclass
class ProjectSpec:
    name: str
    source_path: str
    staged_path: str
    services: list[ServiceSpec]
    repository: RepositorySpec
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DeploymentPlan:
    project_slug: str
    primary_service: str
    region: str = "ap-south-1"
    infrastructure: str = "cloudformation"
    topology: str = "ec2-single-instance"
    database: str = "mongodb-atlas"
    source_patches: list[dict[str, str]] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    service_root: str = ""
    package_manager: str = "npm"
    install_command: str = "npm ci"
    build_command: str = "npm run build"
    start_command: str = "npm run start"
    port: int = 3000
    health_path: str = "/api/health"
    environment_contract: list[dict[str, Any]] = field(default_factory=list)
    runtime_strategy: str = "nextjs-standalone"
    github_jobs: list[str] = field(default_factory=list)
    aws_sizing: dict[str, Any] = field(default_factory=dict)
    required_patches: list[str] = field(default_factory=list)
    repair_actions: list[str] = field(default_factory=list)
    model: str = "gemma4:31b-cloud"
    model_used: bool = False
    target: str = DeploymentTarget.AWS_EC2.value
    environment: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ArtifactRecord:
    path: str
    kind: str
    sha256: str
    size: int
    original_exists: bool
    original_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

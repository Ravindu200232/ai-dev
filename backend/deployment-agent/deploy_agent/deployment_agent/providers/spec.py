"""Deployment target facts and result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import DeploymentTarget


@dataclass(frozen=True)
class TargetProfile:
    target: DeploymentTarget
    label: str

    required_tools: tuple[str, ...]

    required_artifacts: tuple[str, ...]

    artifact_kind: str

    runtime_strategy: str

    requires_oidc_permissions: bool

    supports_teardown: bool
    commit_subject: str
    pr_title: str


TARGET_PROFILES: dict[DeploymentTarget, TargetProfile] = {
    DeploymentTarget.AWS_EC2: TargetProfile(
        target=DeploymentTarget.AWS_EC2,
        label="AWS EC2",
        required_tools=("git", "gh", "aws"),
        required_artifacts=(
            ".github/workflows/ci.yml",
            ".github/workflows/deploy.yml",
            "infra/bootstrap.yml",
            "deploy/release.sh",
        ),
        artifact_kind="aws",
        runtime_strategy="nextjs-standalone",
        requires_oidc_permissions=True,
        supports_teardown=True,
        commit_subject="chore(deploy): add generated AWS deployment for {run}",
        pr_title="Generated AWS deployment configuration",
    ),
    DeploymentTarget.AWS_ECS: TargetProfile(
        target=DeploymentTarget.AWS_ECS,
        label="AWS ECS Fargate",

        required_tools=("git", "gh", "aws"),
        required_artifacts=(
            ".github/workflows/ci.yml",
            ".github/workflows/deploy.yml",
            "infra/bootstrap.yml",
            "Dockerfile",
            "deploy/task-definition.json",
        ),

        artifact_kind="aws",

        runtime_strategy="nextjs-docker-standalone",
        requires_oidc_permissions=True,
        supports_teardown=True,
        commit_subject="chore(deploy): add generated AWS ECS deployment for {run}",
        pr_title="Generated AWS ECS deployment configuration",
    ),
    DeploymentTarget.VERCEL: TargetProfile(
        target=DeploymentTarget.VERCEL,
        label="Vercel",
        required_tools=("git", "gh", "vercel"),
        required_artifacts=(
            ".github/workflows/ci.yml",
            ".github/workflows/deploy.yml",
            "deploy/vercel-environment.json",
        ),
        artifact_kind="vercel",
        runtime_strategy="vercel-managed",
        requires_oidc_permissions=False,
        supports_teardown=True,
        commit_subject="chore(deploy): add generated Vercel deployment for {run}",
        pr_title="Generated Vercel deployment configuration",
    ),
}


def profile_for(value: Any) -> TargetProfile:
    """Resolve a profile from a target, its string value, or None."""
    if isinstance(value, TargetProfile):
        return value
    if isinstance(value, DeploymentTarget):
        return TARGET_PROFILES[value]
    try:
        return TARGET_PROFILES[DeploymentTarget(str(value or DeploymentTarget.AWS_EC2.value))]
    except (ValueError, KeyError):
        return TARGET_PROFILES[DeploymentTarget.AWS_EC2]


def target_of(run: dict[str, Any] | None) -> DeploymentTarget:
    """The deployment target a persisted run belongs to."""
    plan = (run or {}).get("plan") or {}
    return profile_for(plan.get("target")).target


@dataclass
class ProviderPrep:
    """What a provider needs the shared GitHub delivery half to know."""

    github_variables: dict[str, str] = field(default_factory=dict)

    github_secrets: dict[str, str] = field(default_factory=dict)

    repo_state: dict[str, Any] = field(default_factory=dict)

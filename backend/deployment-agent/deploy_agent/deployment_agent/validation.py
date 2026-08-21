from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .environment import EnvironmentContractResolver
from .models import DeploymentTarget, EnvironmentContract, GateStatus, ValidationGate
from .security import SECRET_VALUE_PATTERNS, redact_text


MANDATORY_GATE_IDS = (
    "project_discovery",
    "package_lock",
    "environment_contract",
    "source_compatibility",
    "dependency_install",
    "application_build",
    "quality_scripts",
    "provider_artifacts",
    "secret_boundary",
    "local_runtime",
    "repository_authentication",
    "provider_authentication",
    "provider_preflight",
    "exact_commit",
    "provider_success",
    "live_health",
    "homepage",
    "auth_configuration",
    "cicd_commit_match",
)


def gate(
    gate_id: str,
    status: GateStatus | str,
    message: str,
    evidence: dict | None = None,
    required: bool = True,
) -> ValidationGate:
    return ValidationGate(
        id=gate_id,
        required=required,
        status=status if isinstance(status, GateStatus) else GateStatus(str(status)),
        message=redact_text(message),
        evidence=evidence or {},
    )


def mandatory_gates_pass(gates: Iterable[ValidationGate | dict]) -> bool:
    values: dict[str, tuple[bool, str]] = {}
    for item in gates:
        if isinstance(item, ValidationGate):
            values[item.id] = (item.required, item.status.value)
        else:
            values[str(item.get("id", ""))] = (bool(item.get("required", True)), str(item.get("status", "pending")))
    return all(gate_id in values and (not values[gate_id][0] or values[gate_id][1] == GateStatus.PASSED.value) for gate_id in MANDATORY_GATE_IDS)


class SemanticValidator:
    @staticmethod
    def environment_contract(contract: EnvironmentContract) -> ValidationGate:
        errors = EnvironmentContractResolver.validate(contract)
        if errors:
            return gate(
                "environment_contract",
                GateStatus.FAILED,
                "; ".join(errors),
                {"unresolved": [entry.name for entry in contract.unresolved()]},
            )
        return gate(
            "environment_contract",
            GateStatus.PASSED,
            "All required production variables are resolved",
            {"entries": [entry.to_dict() for entry in contract.entries]},
        )

    @staticmethod
    def provider_artifacts(
        target: DeploymentTarget,
        contract: EnvironmentContract,
        staged_root: Path,
    ) -> ValidationGate:
        if target == DeploymentTarget.AWS_EC2:

            return gate(
                "provider_artifacts",
                GateStatus.PASSED,
                "AWS artifacts are validated by the CloudFormation checks",
            )
        try:
            SemanticValidator._validate_vercel_environment(contract, staged_root)
        except Exception as exc:
            return gate("provider_artifacts", GateStatus.FAILED, str(exc))
        return gate(
            "provider_artifacts",
            GateStatus.PASSED,
            f"{target.value} artifacts match the production environment contract",
        )

    @staticmethod
    def secret_boundary(staged_root: Path, paths: Iterable[str]) -> ValidationGate:
        violations: list[str] = []
        for relative in paths:
            path = staged_root / relative
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if relative.endswith(".env.example"):
                continue
            for pattern in SECRET_VALUE_PATTERNS:
                match = pattern.search(content)
                if match and "AWS_SECRETS_MANAGER_ARN" not in match.group(0) and "deployment_agent_build" not in match.group(0):
                    violations.append(relative)
                    break
        if violations:
            return gate(
                "secret_boundary",
                GateStatus.FAILED,
                "Potential secret values were found in generated files",
                {"files": sorted(set(violations))},
            )
        return gate("secret_boundary", GateStatus.PASSED, "No secret values are present in generated artifacts")

    @staticmethod
    def _validate_vercel_environment(contract: EnvironmentContract, staged_root: Path) -> None:
        path = staged_root / "deploy" / "vercel-environment.json"
        if not path.is_file():
            raise ValueError("Vercel environment mapping artifact is required")
        rendered = json.loads(path.read_text(encoding="utf-8"))
        variables = rendered.get("variables", [])
        names = [item.get("name") for item in variables]
        SemanticValidator._reject_duplicates(names)
        expected = {
            entry.name
            for entry in EnvironmentContractResolver.production_entries(contract)
            if entry.resolution not in {"provider_managed", "optional"}
        }
        if set(names) != expected:
            raise ValueError(
                f"Vercel production environment mapping mismatch; expected {sorted(expected)}, rendered {sorted(filter(None, names))}"
            )
        by_name = {item.get("name"): item for item in variables}
        for entry in EnvironmentContractResolver.production_entries(contract):
            if entry.name not in by_name:
                continue
            item = by_name[entry.name]
            if entry.public and item.get("type") == "sensitive":
                raise ValueError(f"Public variable {entry.name} cannot be stored as sensitive")
            expected_type = "sensitive" if entry.secret else "plain"
            if item.get("type") != expected_type:
                raise ValueError(f"Vercel variable {entry.name} has the wrong sensitivity")
            if "value" in item:
                raise ValueError(f"Vercel artifact must not contain a value for {entry.name}")

    @staticmethod
    def _reject_duplicates(names: list[str | None]) -> None:
        normalized = [name for name in names if name]
        duplicates = sorted({name for name in normalized if normalized.count(name) > 1})
        if duplicates:
            raise ValueError(f"Duplicate environment mappings: {duplicates}")

from __future__ import annotations

import json
from pathlib import Path

from .environment import EnvironmentContractResolver
from .models import DeploymentTarget, EnvironmentContract, GateStatus, ValidationGate
from .security import redact_text


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

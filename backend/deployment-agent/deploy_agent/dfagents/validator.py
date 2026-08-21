from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import yaml

from deployment_agent.config import ARTIFACT_SCHEMA_VERSION
from deployment_agent.environment import contract_from_dict
from deployment_agent.models import DeploymentTarget, GateStatus
from deployment_agent.providers import profile_for
from deployment_agent.validation import SemanticValidator
from deployment_agent.security import SECRET_VALUE_PATTERNS, redact_text


class SecurityValidatorAgent:
    def __init__(self, emit: Callable[..., object] | None = None):
        self.emit = emit or (lambda *args, **kwargs: None)

    def validate(
        self,
        run_id: str,
        staged_root: Path,
        records: list[dict],
        target: DeploymentTarget = DeploymentTarget.AWS_EC2,
    ) -> dict:
        profile = profile_for(target)
        manifest: dict | None = None
        self.emit(run_id, "step", "security", "running", 78, "Validating generated artifacts and secret boundaries")
        errors: list[str] = []
        warnings: list[str] = []
        checks: list[dict] = []
        for record in records:
            path = staged_root / record["path"]
            if not path.exists():
                errors.append(f"Missing generated artifact: {record['path']}")
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if record["path"] != ".env.example":
                for pattern in SECRET_VALUE_PATTERNS:
                    match = pattern.search(text)
                    if (
                        match
                        and "AWS_SECRETS_MANAGER_ARN" not in match.group(0)
                        and "mongodb://127.0.0.1:27017/deployment_agent_build" not in match.group(0)
                    ):
                        errors.append(f"Potential secret value detected in {record['path']}")
                        break
        for record in records:
            if record["path"].endswith(".json"):
                try:
                    json.loads((staged_root / record["path"]).read_text(encoding="utf-8"))
                    checks.append({"name": f"JSON: {record['path']}", "status": "passed"})
                except Exception as exc:
                    errors.append(f"Invalid JSON in {record['path']}: {exc}")
        required = list(profile.required_artifacts)
        available = {record["path"] for record in records}
        for suffix in required:
            ok = any(path == suffix or path.endswith("/" + suffix) for path in available)
            checks.append({"name": suffix, "status": "passed" if ok else "failed"})
            if not ok:
                errors.append(f"Required artifact not generated: {suffix}")
        for workflow_name, required_job in (("ci.yml", "validate"), ("deploy.yml", "deploy")):
            workflow_path = staged_root / ".github" / "workflows" / workflow_name
            try:
                workflow = self._load_yaml(workflow_path.read_text(encoding="utf-8"))
                if not isinstance(workflow, dict) or not isinstance(workflow.get("on"), dict):
                    raise ValueError("workflow triggers are missing")
                jobs = workflow.get("jobs") or {}
                if required_job not in jobs:
                    raise ValueError(f"required job {required_job} is missing")
                if workflow_name == "deploy.yml":
                    permissions = workflow.get("permissions") or {}
                    if permissions.get("contents") != "read":
                        raise ValueError("least-privilege contents: read permission is missing")

                    if profile.requires_oidc_permissions and permissions.get("id-token") != "write":
                        raise ValueError("least-privilege GitHub OIDC permissions are missing")
                    if not profile.requires_oidc_permissions and permissions.get("id-token"):
                        raise ValueError("this target must not request an OIDC id-token")
                    if "workflow_dispatch" not in workflow["on"]:
                        raise ValueError("retry-safe workflow_dispatch trigger is missing")
                checks.append({"name": f"Workflow YAML: {workflow_name}", "status": "passed"})
            except Exception as exc:
                errors.append(f"Invalid GitHub workflow {workflow_name}: {exc}")

        if target in (DeploymentTarget.AWS_EC2, DeploymentTarget.AWS_ECS):
            for template_name in ("bootstrap.yml",):
                template_path = staged_root / "infra" / template_name
                try:
                    template = self._load_yaml(template_path.read_text(encoding="utf-8"))
                    if not isinstance(template, dict) or not isinstance(template.get("Resources"), dict):
                        raise ValueError("CloudFormation Resources are missing")
                    checks.append({"name": f"CloudFormation YAML: {template_name}", "status": "passed"})
                except Exception as exc:
                    errors.append(f"Invalid CloudFormation template {template_name}: {exc}")
        manifest_path = staged_root / "deployment-manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("version") != ARTIFACT_SCHEMA_VERSION:
                raise ValueError("artifact schema version is stale")
            if manifest.get("model_used") is not True:
                raise ValueError("a validated Ollama plan is required")
            generation = manifest.get("generation") or {}
            if generation.get("runtime_strategy") != profile.runtime_strategy:
                raise ValueError(
                    f"validated {profile.label} generation strategy is missing "
                    f"(expected {profile.runtime_strategy})"
                )
            owned = set(manifest.get("agent_owned_files") or [])
            if not available.issubset(owned):
                raise ValueError("agent-owned file manifest is incomplete")
            checks.append({"name": "AI generation manifest", "status": "passed"})
        except Exception as exc:
            errors.append(f"Invalid deployment manifest: {exc}")
        if target == DeploymentTarget.AWS_EC2:

            bootstrap = (staged_root / "infra" / "bootstrap.yml").read_text(encoding="utf-8", errors="ignore")
            if "token.actions.githubusercontent.com:sub" not in bootstrap:
                errors.append("GitHub OIDC subject restriction is missing")
            if "CidrIp: 0.0.0.0/0" in bootstrap:
                warnings.append("The instance security group allows HTTP from the internet; the application itself binds to loopback behind nginx.")
        elif target == DeploymentTarget.AWS_ECS:

            bootstrap = (staged_root / "infra" / "bootstrap.yml").read_text(encoding="utf-8", errors="ignore")
            if "token.actions.githubusercontent.com:sub" not in bootstrap:
                errors.append("GitHub OIDC subject restriction is missing")

            if "SourceSecurityGroupId" not in bootstrap:
                warnings.append(
                    "The task security group does not restrict ingress to the load balancer; "
                    "the container may be reachable directly from the internet."
                )
            if "iam:PassedToService" not in bootstrap:
                errors.append(
                    "iam:PassRole in the deploy role is not conditioned on ecs-tasks.amazonaws.com"
                )
            dockerfile = ""
            for candidate in staged_root.rglob("Dockerfile"):
                dockerfile = candidate.read_text(encoding="utf-8", errors="ignore")
                break
            if dockerfile:
                if "USER " not in dockerfile:
                    warnings.append("The container image runs as root; add a non-root USER.")
                if ".next/static" not in dockerfile:
                    errors.append(
                        "The image does not copy .next/static into the standalone tree; "
                        "the deployed application would serve no CSS or images."
                    )
                checks.append({"name": "Container image", "status": "passed"})
            else:
                errors.append("Dockerfile is missing")
        else:
            gate = SemanticValidator.provider_artifacts(
                target, contract_from_dict((manifest or {}).get("environment_contract") or {}), staged_root
            )
            checks.append({"name": "Provider environment mapping", "status": gate.status.value})
            if gate.status == GateStatus.FAILED:
                errors.append(gate.message)
            warnings.append(
                "A Vercel deploy token will be stored in this repository's GitHub Actions secrets; "
                "anyone who can push a workflow there can use it."
            )
        result = {
            "passed": not errors,
            "errors": [redact_text(item) for item in errors],
            "warnings": [redact_text(item) for item in warnings],
            "checks": checks,
        }
        status = "complete" if not errors else "failed"
        self.emit(
            run_id,
            "step",
            "security",
            status,
            84,
            "Security validation passed" if not errors else f"Security validation failed with {len(errors)} issue(s)",
            result,
        )
        return result

    @staticmethod
    def _load_yaml(value: str):
        class Loader(yaml.SafeLoader):
            pass

        Loader.yaml_implicit_resolvers = {
            key: [(tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:bool"]
            for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
        }

        def construct_tag(loader, _tag_suffix, node):
            if isinstance(node, yaml.ScalarNode):
                return loader.construct_scalar(node)
            if isinstance(node, yaml.SequenceNode):
                return loader.construct_sequence(node)
            return loader.construct_mapping(node)

        Loader.add_multi_constructor("!", construct_tag)
        return yaml.load(value, Loader=Loader)

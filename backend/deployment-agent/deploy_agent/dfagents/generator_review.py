from __future__ import annotations

from .generator_shared import *
from .generator_shared import _EAGER_CONNECT, _LAZY_CONNECT, generator_class


class GeneratorReviewMixin:
    @staticmethod
    def _runtime_secret_entries(contract: EnvironmentContract) -> list[EnvironmentEntry]:
        """Every key the runtime secret carries — contract-declared or injected."""
        entries = [
            entry for entry in EnvironmentContractResolver.production_entries(contract)
            if entry.secret and entry.scope in {"runtime", "both"}
        ]
        declared = {entry.name for entry in entries}
        entries.extend(
            EnvironmentEntry(
                name=name, required=False, secret=True, scope="runtime",
                resolution="provider_managed", value_present=True,
                sources=["deployer:stack-outputs"],
            )
            for name in DEPLOYER_INJECTED if name not in declared
        )
        return entries
    @staticmethod
    def _parameters_example(spec: ProjectSpec, plan: DeploymentPlan, service: ServiceSpec) -> dict:
        return {
            "ProjectSlug": plan.project_slug,
            "GitHubSubjects": [
                f"repo:owner/repository:ref:refs/heads/{spec.repository.branch or 'main'}",
                f"repo:owner@OWNER_ID/repository@REPOSITORY_ID:ref:refs/heads/{spec.repository.branch or 'main'}",
            ],
            "AppPort": service.port,
            "InstanceType": str((plan.aws_sizing or {}).get("instance_type", "t3.micro")),
            "ExistingOidcProviderArn": "",
        }
    @staticmethod
    def _initial_readiness(
        records: list[ArtifactRecord], target: DeploymentTarget = DeploymentTarget.AWS_EC2
    ) -> dict:
        kinds = {record.kind for record in records}
        profile = profile_for(target)
        categories = {
            "build": 0,
            "cicd": 20 if "cicd" in kinds else 0,
            "provider": 25 if profile.artifact_kind in kinds else 0,
            "security": 0,
            "monitoring": 0,
            "api": 0,
        }
        return {"score": sum(categories.values()), "categories": categories, "phase": "review"}
    def finalize_review(
        self,
        spec: ProjectSpec,
        plan: DeploymentPlan,
        staged_root: Path,
        readiness: dict,
        records: list[ArtifactRecord],
    ) -> list[ArtifactRecord]:
        """Synchronize user-visible review files after validation has finished."""
        replacements = {
            "deployment-report.md": self._report(spec, plan, readiness, DeploymentTarget(plan.target)),
            "readiness-score.json": json.dumps(readiness, indent=2) + "\n",
        }
        by_path = {record.path: index for index, record in enumerate(records)}
        for relative, content in replacements.items():
            updated = self._write(spec, staged_root, relative, content, "report")
            if relative in by_path:
                records[by_path[relative]] = updated
            else:
                records.append(updated)
        return records
    @staticmethod
    def _report(
        spec: ProjectSpec,
        plan: DeploymentPlan,
        readiness: dict,
        target: DeploymentTarget = DeploymentTarget.AWS_EC2,
    ) -> str:
        service = spec.services[0]
        env_names = ", ".join(item.name for item in service.environment) or "None detected"
        risks = "\n".join(f"- {item}" for item in plan.risks) or "- No model risks reported."
        recommendations = "\n".join(f"- {item}" for item in plan.recommendations) or "- Use the generated review checks."
        if target == DeploymentTarget.AWS_EC2:
            database_note = "MongoDB Atlas URI written directly to AWS Secrets Manager"
            security_notes = (
                "- No cloud access keys or provider tokens are written to GitHub; "
                "Actions authenticates through OIDC.\n"
                "- Provider credentials are kept only in the in-memory credential vault.\n"
                "- The instance security group exposes port 80 only; the Next.js process listens on loopback."
            )
        else:
            database_note = "MongoDB Atlas URI set as a Vercel production environment variable"
            security_notes = (
                "- The MongoDB URI is written to Vercel, not to GitHub.\n"
                "- **A Vercel deploy token is stored in this repository's GitHub Actions secrets.** "
                "Vercel has no OIDC equivalent for deploying, so unlike the AWS target this is a "
                "long-lived credential: anyone who can push a workflow to this repository can use it. "
                "Scope it to a team and rotate it if the repository changes hands.\n"
                "- The token is passed to GitHub over stdin and is never written to disk by the agent."
            )
        return textwrap.dedent(
            f"""
            # Deployment Readiness Report

            - Project: `{spec.name}`
            - Primary service: `{service.name}`
            - Detected root: `{service.root or '.'}`
            - Framework: Next.js `{service.version}`
            - Target: {f"AWS EC2 ({(plan.aws_sizing or {}).get('instance_type', 't3.micro')}) behind nginx, released from S3 via SSM" if target == DeploymentTarget.AWS_EC2 else "Vercel production deployment"}
            - Database: {database_note}
            - Readiness: **{readiness['score']}/100** (review phase)
            - Required environment variables: {env_names}

            ## Risks

            {risks}

            ## Recommendations

            {recommendations}

            ## Security invariants

            - Runtime secret values are never included in this report or generated artifacts.
            {security_notes}
            """
        ).lstrip()

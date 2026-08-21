from __future__ import annotations

from .deployer_shared import *
from .deployer_shared import _ACTIVE_PROJECTS, _ACTIVE_LOCK, _PERSISTED_ACTIVE_STATES, _CANCELLABLE_STATES


class DeploymentPrepareMixin:
    @staticmethod
    def _require_tools(profile: TargetProfile | None = None) -> None:
        required = (profile or profile_for(None)).required_tools
        missing = [name for name in required if not command_exists(name)]
        if missing:
            raise RuntimeError("Missing required deployment tools: " + ", ".join(missing))
    @staticmethod
    def _runtime_secret_values(mongodb_uri: str, outputs: dict) -> dict:
        """Everything the running application needs, not just the database."""
        url = str(outputs.get("ApplicationUrl") or "").rstrip("/")
        values = {
            "MONGODB_URI": mongodb_uri,
            "BETTER_AUTH_SECRET": secrets.token_urlsafe(48),
        }
        if url:

            values.update({name: url for name in DEPLOYER_INJECTED})
        return values
    def _prepare_aws(
        self,
        run_id: str,
        run: dict[str, Any],
        spec: dict[str, Any],
        plan: dict[str, Any],
        staged: Path,
        profile: str,
        region: str,
        mongodb_uri: str,
        credential_reference: str,
        oidc_subjects: list[str],
    ) -> ProviderPrep:
        """Bring the AWS account to the point where the workflow can deploy."""
        slug = plan["project_slug"]
        session = self._aws_session(profile, region, credential_reference)
        identity = session.client("sts").get_caller_identity()

        oidc_arn = self._find_github_oidc(session, f"{slug}-bootstrap")
        network = self._select_bootstrap_network(run_id, session)
        outputs = self._apply_bootstrap_stack(
            run_id,
            session,
            staged / "infra" / "bootstrap.yml",
            slug,
            oidc_subjects,
            spec["services"][0]["port"],
            oidc_arn,
            network,
        )
        runtime_secret = outputs.get("RuntimeSecretArn")
        if not runtime_secret:
            raise RuntimeError("Bootstrap stack did not return RuntimeSecretArn")
        session.client("secretsmanager").put_secret_value(
            SecretId=runtime_secret,
            SecretString=json.dumps(
                self._runtime_secret_values(mongodb_uri, outputs)
            ),
        )
        self.emit(run_id, "step", "secrets", "complete", 45, "Runtime secrets stored in AWS Secrets Manager")
        return ProviderPrep(
            github_variables={
                "AWS_DEPLOY_ROLE_ARN": outputs.get("DeployRoleArn", ""),
                "AWS_REGION": region,
                "ARTIFACT_BUCKET": outputs.get("ArtifactBucket", ""),
                "INSTANCE_ID": outputs.get("InstanceId", ""),

                "RUNTIME_SECRET_ID": runtime_secret,
                "PROJECT_SLUG": slug,
            },
            repo_state={
                "aws_profile": profile,
                "region": region,
                "account_id": identity.get("Account", ""),
                "bootstrap_stack": f"{slug}-bootstrap",
                "application_url": outputs.get("ApplicationUrl", ""),
                "artifact_bucket": outputs.get("ArtifactBucket", ""),
                "instance_id": outputs.get("InstanceId", ""),
                "public_ip": outputs.get("PublicIp", ""),
                "log_group": outputs.get("LogGroupName", ""),
                "runtime_secret_arn": runtime_secret,
            },
        )
    def _prepare_ecs(
        self,
        run_id: str,
        run: dict[str, Any],
        spec: dict[str, Any],
        plan: dict[str, Any],
        staged: Path,
        profile: str,
        region: str,
        mongodb_uri: str,
        credential_reference: str,
        oidc_subjects: list[str],
    ) -> ProviderPrep:
        """Bring the account to the point where the workflow can push an image."""
        slug = plan["project_slug"]
        session = self._aws_session(profile, region, credential_reference)
        identity = session.client("sts").get_caller_identity()
        oidc_arn = self._find_github_oidc(session, f"{slug}-bootstrap")
        network = self._select_bootstrap_network(run_id, session)
        outputs = self._apply_bootstrap_stack(
            run_id,
            session,
            staged / "infra" / "bootstrap.yml",
            slug,
            oidc_subjects,
            spec["services"][0]["port"],
            oidc_arn,
            network,
        )
        runtime_secret = outputs.get("RuntimeSecretArn")
        if not runtime_secret:
            raise RuntimeError("Bootstrap stack did not return RuntimeSecretArn")
        session.client("secretsmanager").put_secret_value(
            SecretId=runtime_secret,
            SecretString=json.dumps(
                self._runtime_secret_values(mongodb_uri, outputs)
            ),
        )
        self.emit(run_id, "step", "secrets", "complete", 45,
                  "Runtime secrets stored in AWS Secrets Manager")
        return ProviderPrep(
            github_variables={
                "AWS_DEPLOY_ROLE_ARN": outputs.get("DeployRoleArn", ""),
                "AWS_REGION": region,
                "ECR_REPOSITORY_URI": outputs.get("EcrRepositoryUri", ""),
                "ECS_CLUSTER": outputs.get("ClusterName", ""),
                "ECS_SERVICE": outputs.get("ServiceName", ""),
                "ECS_TASK_FAMILY": outputs.get("TaskFamily", ""),
                "ECS_EXECUTION_ROLE_ARN": outputs.get("ExecutionRoleArn", ""),
                "ECS_TASK_ROLE_ARN": outputs.get("TaskRoleArn", ""),
                "CONTAINER_NAME": outputs.get("ContainerName", ""),

                "RUNTIME_SECRET_ID": runtime_secret,
                "PROJECT_SLUG": slug,
            },
            repo_state={
                "aws_profile": profile,
                "region": region,
                "account_id": identity.get("Account", ""),
                "bootstrap_stack": f"{slug}-bootstrap",

                "application_url": outputs.get("ApplicationUrl", ""),
                "ecr_repository": outputs.get("EcrRepositoryUri", ""),
                "ecs_cluster": outputs.get("ClusterName", ""),
                "ecs_service": outputs.get("ServiceName", ""),
                "task_family": outputs.get("TaskFamily", ""),
                "container_name": outputs.get("ContainerName", ""),
                "load_balancer_dns": outputs.get("LoadBalancerDns", ""),
                "log_group": outputs.get("LogGroupName", ""),
                "runtime_secret_arn": runtime_secret,
            },
        )
    def _prepare_vercel(
        self,
        run_id: str,
        run: dict[str, Any],
        spec: dict[str, Any],
        plan: dict[str, Any],
        staged: Path,
        mongodb_uri: str,
        vercel_token: str,
    ) -> ProviderPrep:
        """Create and configure the Vercel project, then hand GitHub what it needs."""
        from deployment_agent import vercel_api
        from deployment_agent.vercel_auth import require_vercel_token

        token = require_vercel_token(vercel_token)
        slug = plan["project_slug"]
        service_root = (spec.get("services") or [{}])[0].get("root") or ""
        cwd = staged / service_root if service_root else staged
        team_id = str((run.get("repo") or {}).get("vercel_team_id", ""))

        self.emit(run_id, "step", "link", "running", 20, f"Linking the Vercel project {slug}")
        link = run_command(
            ["vercel", "link", "--yes", "--project", slug, "--cwd", str(cwd)],
            cwd=cwd,
            timeout=180,

            env={"VERCEL_TOKEN": token} if token else None,
        )
        if link.returncode != 0:

            detail = (link.stderr or link.stdout or "").strip()[:300]
            why = ("The Vercel token was rejected."
                   if "token" in detail.lower()
                   else f"'{slug}' may already exist under another account or scope.")
            raise RuntimeError(f"Could not link the Vercel project. {why} {detail}")
        try:
            project_link = json.loads((cwd / ".vercel" / "project.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Vercel link did not produce .vercel/project.json") from exc
        project_id = str(project_link.get("projectId", ""))
        org_id = str(project_link.get("orgId", ""))
        if not project_id or not org_id:
            raise RuntimeError("Vercel link returned no project or organisation id")

        if not team_id and org_id.startswith("team_"):
            team_id = org_id
        self.emit(run_id, "step", "link", "complete", 30,
                  f"Linked Vercel project {slug}"
                  + (f" (team {team_id})" if team_id else ""))

        try:
            project = vercel_api.get_project(token, project_id, team_id)
            application_url = vercel_api.production_url(project)
            project_name = str(project.get("name") or slug)
        except Exception:
            application_url = f"https://{slug}.vercel.app"
            project_name = slug

        self._sync_vercel_environment(run_id, cwd, token, project_id, team_id,
                                      plan, mongodb_uri, application_url, slug)

        return ProviderPrep(
            github_variables={
                "VERCEL_ORG_ID": org_id,
                "VERCEL_PROJECT_ID": project_id,
                "PROJECT_SLUG": slug,
            },

            github_secrets={"VERCEL_TOKEN": token},
            repo_state={
                "vercel_org_id": org_id,
                "vercel_project_id": project_id,
                "vercel_project_name": project_name,
                "vercel_team_id": team_id,
                "application_url": application_url,
            },
        )
    def _sync_vercel_environment(
        self,
        run_id: str,
        cwd: Path,
        token: str,
        project_id: str,
        team_id: str,
        plan: dict[str, Any],
        mongodb_uri: str,
        application_url: str = "",
        slug: str = "",
    ) -> None:
        """Write production environment variables straight to Vercel."""
        from deployment_agent import vercel_api

        entries = ((plan.get("environment") or {}).get("entries")) or []
        existing: set[str] = set()
        existing_ids: dict[str, str] = {}
        try:
            for item in vercel_api.list_env(token, project_id, team_id):
                if "production" not in (item.get("target") or []):
                    continue
                key = str(item.get("key"))
                existing.add(key)
                existing_ids[key] = str(item.get("id", ""))
        except Exception as exc:
            self.emit(run_id, "warning", "env", "warning", 35, redact_text(str(exc)))

        resolved = self._vercel_user_values(mongodb_uri, application_url, slug)

        values: dict[str, str] = {}
        unresolved: list[str] = []
        for entry in entries:
            name = str(entry.get("name", ""))
            if entry.get("resolution") == "user_required":
                if name in resolved and resolved[name]:
                    values[name] = resolved[name]
                elif name not in existing:
                    unresolved.append(name)
            elif entry.get("resolution") == "auto_generate":

                if name in existing:
                    continue
                values[name] = secrets.token_urlsafe(48)
        if unresolved:

            self.emit(run_id, "warning", "env", "warning", 36,
                      "No value for " + ", ".join(sorted(unresolved))
                      + " — set it on the Vercel project if the app needs it.")
        if not values:
            self.emit(run_id, "step", "env", "complete", 40, "No production environment variables to sync")
            return

        self.emit(run_id, "step", "env", "running", 35, f"Syncing {len(values)} Vercel environment variable(s)")
        for name, value in values.items():

            try:

                if name in existing_ids and existing_ids[name]:
                    try:
                        vercel_api.delete_env(token, project_id, existing_ids[name], team_id)
                    except Exception:
                        pass
                vercel_api.upsert_env(token, project_id, name, value, team_id)
            except Exception as exc:
                raise RuntimeError(
                    f"Could not set the Vercel environment variable {name}: {redact_text(str(exc))[:200]}"
                ) from exc
        self.emit(run_id, "step", "env", "complete", 40, "Vercel production environment variables are set")
    @staticmethod
    def _vercel_user_values(mongodb_uri: str, application_url: str,
                           slug: str) -> dict[str, str]:
        """Values for the variables the contract leaves to a person."""
        from urllib.parse import urlsplit

        try:
            database = (urlsplit(mongodb_uri).path or "").lstrip("/").split("?")[0]
        except ValueError:
            database = ""
        url = (application_url or "").rstrip("/")
        values = {
            "MONGODB_URI": mongodb_uri,
            "MONGODB_DB": database or slug.replace("-", "_")[:63],
        }
        if url:

            values["BETTER_AUTH_URL"] = url
            values["BASE_URL"] = url
            values["NEXT_PUBLIC_BASE_URL"] = url
        return values

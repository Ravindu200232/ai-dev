from __future__ import annotations

from .deployer_shared import *
from .deployer_shared import _ACTIVE_PROJECTS, _ACTIVE_LOCK, _PERSISTED_ACTIVE_STATES, _CANCELLABLE_STATES


class DeploymentLifecycleMixin:
    def cancel(self, run_id: str) -> dict[str, Any]:
        """Stop a run that is still in flight."""
        run = self.store.get_run(run_id)
        if not run:
            raise ValueError("Run not found")
        state = run["state"]
        if state not in _CANCELLABLE_STATES:
            raise ValueError(
                f"This deployment is not running (it is {state}), so there is "
                f"nothing to cancel"
            )
        workflow = self._cancel_workflow_run(run)
        self.store.transition_run(run_id, RunState.CANCELLED, error="")
        self.emit(
            run_id, "step", "cancel", "complete", 100,
            "Deployment cancelled"
            + (f"; GitHub Actions run {workflow} was stopped too" if workflow else "")
            + ". Cloud resources were not deleted — use Delete for those.",
        )
        return {"cancelled": True, "state": RunState.CANCELLED.value,
                "workflow_cancelled": workflow}
    def _cancel_workflow_run(self, run: dict[str, Any]) -> str:
        """Stop the GitHub Actions run this deployment started, if one is live."""
        repository = str((run.get("repo") or {}).get("repository") or "")
        if not repository or not command_exists("gh"):
            return ""
        try:
            listed = run_command(
                ["gh", "run", "list", "--repo", repository, "--limit", "10",
                 "--json", "databaseId,status"],
                timeout=30,
            )
            if listed.returncode != 0:
                return ""
            live = [
                item for item in json.loads(listed.stdout or "[]")
                if str(item.get("status", "")) in
                {"queued", "in_progress", "waiting", "requested", "pending"}
            ]
            if not live:
                return ""
            target = str(live[0].get("databaseId", ""))
            cancelled = run_command(
                ["gh", "run", "cancel", target, "--repo", repository], timeout=30
            )
            return target if cancelled.returncode == 0 else ""
        except Exception:                                   # noqa: BLE001

            return ""
    def start_teardown(self, run_id: str, credential_reference: str = "",
                       force: bool = False) -> dict[str, Any]:
        """Delete every provider resource this run created."""
        run = self.store.get_run(run_id)
        if not run:
            raise ValueError("Run not found")
        if run["state"] in {RunState.DESTROYED.value}:
            raise ValueError("This deployment has already been torn down")
        if run["state"] in _PERSISTED_ACTIVE_STATES:
            if not force:
                raise ValueError(
                    "This deployment is still running. Stop it first with "
                    "Cancel, or delete it anyway — that stops it and removes "
                    "its resources in one go."
                )

            self.cancel(run_id)
            run = self.store.get_run(run_id) or run
            self.emit(run_id, "step", "teardown", "running", 5,
                      "Deployment stopped; deleting what it created")
        plan = run.get("plan") or {}
        slug = plan.get("project_slug") or ""
        repo = run.get("repo") or {}
        if not slug or not repo:

            self.store.transition_run(run_id, RunState.DESTROYED, error="")
            self.emit(run_id, "step", "teardown", "complete", 100, "No cloud resources were created by this run")
            return {"deleted": [], "slug": slug}
        if target_of(run) is DeploymentTarget.VERCEL:
            threading.Thread(
                target=self._teardown_vercel, args=(run_id, slug, repo), daemon=True
            ).start()
            return {"accepted": True, "slug": slug}
        threading.Thread(
            target=self._teardown_worker, args=(run_id, slug, repo, credential_reference), daemon=True
        ).start()
        return {"accepted": True, "slug": slug}
    def _teardown_vercel(self, run_id: str, slug: str, repo: dict[str, Any]) -> None:
        from deployment_agent import vercel_api
        from deployment_agent.vercel_auth import require_vercel_token

        try:
            project_id = str(repo.get("vercel_project_id", ""))
            if not project_id:
                raise RuntimeError("No Vercel project is recorded for this deployment")
            self.emit(run_id, "step", "teardown", "running", 40, f"Deleting the Vercel project {slug}")
            vercel_api.delete_project(
                require_vercel_token(), project_id, str(repo.get("vercel_team_id", ""))
            )
            self.store.transition_run(run_id, RunState.DESTROYED, error="")
            self.emit(
                run_id, "step", "teardown", "complete", 100,
                "Teardown complete; the Vercel project and its deployments were deleted",
                {"deleted": [slug]},
            )
        except Exception as exc:

            self.emit(
                run_id, "error", "teardown", "failed", 100,
                f"Teardown failed; the Vercel project was NOT deleted: {redact_text(str(exc))}",
            )
    def _teardown_worker(
        self, run_id: str, slug: str, repo: dict[str, Any], credential_reference: str = ""
    ) -> None:
        try:

            session = self._aws_session(
                repo.get("aws_profile", ""), repo.get("region", "ap-south-1"), credential_reference
            )
            cfn = session.client("cloudformation")
            deleted: list[str] = []

            for index, stack_name in enumerate((f"{slug}-service", f"{slug}-bootstrap")):
                try:
                    cfn.describe_stacks(StackName=stack_name)
                except Exception as exc:

                    if "does not exist" in str(exc):
                        continue
                    raise
                self.emit(
                    run_id, "step", "teardown", "running", 20 + index * 40, f"Deleting stack {stack_name}"
                )

                self._drain_stack_resources(run_id, session, cfn, stack_name)
                cfn.delete_stack(StackName=stack_name)
                cfn.get_waiter("stack_delete_complete").wait(
                    StackName=stack_name, WaiterConfig={"Delay": 10, "MaxAttempts": 120}
                )
                deleted.append(stack_name)
                self.emit(run_id, "step", "teardown", "running", 40 + index * 40, f"Deleted {stack_name}")
            self.store.transition_run(run_id, RunState.DESTROYED, error="")
            self.emit(
                run_id,
                "step",
                "teardown",
                "complete",
                100,
                f"Teardown complete; deleted {len(deleted)} stack(s)" if deleted else "No stacks remained to delete",
                {"deleted": deleted},
            )
        except Exception as exc:

            self.emit(
                run_id,
                "error",
                "teardown",
                "failed",
                100,
                f"Teardown failed; resources were NOT deleted: {redact_text(str(exc))}",
            )
    def _drain_stack_resources(self, run_id: str, session, cfn, stack_name: str) -> None:
        """Empty the resources CloudFormation cannot delete while non-empty."""
        try:
            resources = cfn.describe_stack_resources(StackName=stack_name)["StackResources"]
        except Exception:
            return
        for resource in resources:
            kind = resource.get("ResourceType")
            physical = resource.get("PhysicalResourceId")
            if not physical:
                continue
            try:
                if kind == "AWS::ECR::Repository":
                    self._empty_ecr_repository(session, physical)
                    self.emit(run_id, "step", "teardown", "running", 30, f"Emptied ECR repository {physical}")
                elif kind == "AWS::S3::Bucket":
                    self._empty_s3_bucket(session, physical)
                    self.emit(run_id, "step", "teardown", "running", 30, f"Emptied S3 bucket {physical}")
            except Exception as exc:
                self.emit(
                    run_id, "warning", "teardown", "warning", 30,
                    f"Could not empty {kind} {physical}: {redact_text(str(exc))}",
                )
    @staticmethod
    def _empty_ecr_repository(session, repository: str) -> None:
        ecr = session.client("ecr")
        paginator = ecr.get_paginator("list_images")
        for page in paginator.paginate(repositoryName=repository):
            ids = page.get("imageIds", [])
            if ids:
                ecr.batch_delete_image(repositoryName=repository, imageIds=ids)
    @staticmethod
    def _empty_s3_bucket(session, bucket: str) -> None:
        s3 = session.client("s3")

        for key in ("Versions", "DeleteMarkers"):
            paginator = s3.get_paginator("list_object_versions")
            for page in paginator.paginate(Bucket=bucket):
                items = [{"Key": item["Key"], "VersionId": item["VersionId"]} for item in page.get(key, [])]
                for index in range(0, len(items), 1000):
                    s3.delete_objects(Bucket=bucket, Delete={"Objects": items[index : index + 1000]})
    def _rollback_vercel(self, run_id: str, repo: dict[str, Any]) -> dict[str, Any]:
        """Promote the most recent healthy deployment that is not the current one."""
        from deployment_agent import vercel_api
        from deployment_agent.vercel_auth import require_vercel_token

        project_id = str(repo.get("vercel_project_id", ""))
        if not project_id:
            raise RuntimeError("No Vercel project is recorded for this deployment")
        token = require_vercel_token()
        team_id = str(repo.get("vercel_team_id", ""))
        ready = [
            item
            for item in vercel_api.list_deployments(token, project_id, team_id, limit=20)
            if item.get("readyState") == "READY" or item.get("state") == "READY"
        ]
        if len(ready) < 2:
            raise RuntimeError("No previous healthy Vercel deployment is available to roll back to")

        previous = ready[1]
        deployment_id = str(previous.get("uid") or previous.get("id") or "")
        vercel_api.promote_deployment(token, project_id, deployment_id, team_id)
        self.store.transition_run(run_id, RunState.ROLLED_BACK)
        self.emit(run_id, "step", "rollback", "complete", 100, "Promoted the previous Vercel deployment")
        return {"deployment_id": deployment_id, "url": previous.get("url", "")}
    def vercel_domains(self, run_id: str) -> list[dict[str, Any]]:
        from deployment_agent import vercel_api
        from deployment_agent.vercel_auth import require_vercel_token

        project_id, team_id = self._vercel_project(run_id)
        return vercel_api.list_domains(require_vercel_token(), project_id, team_id)
    def add_vercel_domain(self, run_id: str, domain: str) -> dict[str, Any]:
        from deployment_agent import vercel_api
        from deployment_agent.vercel_auth import require_vercel_token

        domain = str(domain or "").strip().lower()
        if not domain or "/" in domain or " " in domain:
            raise ValueError("Enter a bare hostname, for example app.example.com")
        project_id, team_id = self._vercel_project(run_id)
        result = vercel_api.add_domain(require_vercel_token(), project_id, domain, team_id)
        self.emit(run_id, "step", "domains", "complete", 100, f"Added the domain {domain}")

        return result
    def remove_vercel_domain(self, run_id: str, domain: str) -> dict[str, Any]:
        from deployment_agent import vercel_api
        from deployment_agent.vercel_auth import require_vercel_token

        project_id, team_id = self._vercel_project(run_id)
        result = vercel_api.remove_domain(require_vercel_token(), project_id, str(domain), team_id)
        self.emit(run_id, "step", "domains", "complete", 100, f"Removed the domain {domain}")
        return result
    def _vercel_project(self, run_id: str) -> tuple[str, str]:
        run = self.store.get_run(run_id)
        if not run:
            raise ValueError("Run not found")
        if target_of(run) is not DeploymentTarget.VERCEL:
            raise ValueError("This deployment does not target Vercel")
        repo = run.get("repo") or {}
        project_id = str(repo.get("vercel_project_id", ""))
        if not project_id:
            raise ValueError("This run has no linked Vercel project yet; deploy it first")
        return project_id, str(repo.get("vercel_team_id", ""))
    def rollback(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run or not run.get("repo"):
            raise ValueError("No deployed environment is available for rollback")
        repo = run["repo"]
        if target_of(run) is DeploymentTarget.VERCEL:
            return self._rollback_vercel(run_id, repo)
        session = self._aws_session(repo.get("aws_profile", ""), repo.get("region", "ap-south-1"))
        instance_id = repo.get("instance_id")
        if not instance_id:
            raise RuntimeError("No EC2 instance is recorded for this deployment")
        ssm = session.client("ssm")

        script = textwrap.dedent(
            """
            set -euo pipefail
            cd /opt/app/releases
            current=$(readlink -f /opt/app/current || true)
            previous=$(ls -1dt /opt/app/releases/*/ | grep -v "^${current}/$" | head -n 1)
            test -n "$previous"
            ln -sfn "${previous%/}" /opt/app/current.new
            mv -Tf /opt/app/current.new /opt/app/current
            systemctl restart nextjs
            basename "${previous%/}" > /opt/app/shared/current-sha
            echo "rolled back to ${previous%/}"
            """
        ).strip()
        command = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Comment="Deployment Agent rollback",
            TimeoutSeconds=600,
            Parameters={"commands": script.splitlines()},
        )
        command_id = command["Command"]["CommandId"]
        self.store.transition_run(run_id, RunState.ROLLED_BACK)
        self.emit(run_id, "step", "rollback", "complete", 100, "Rollback to the previous release dispatched")
        return {"command_id": command_id, "instance_id": instance_id}

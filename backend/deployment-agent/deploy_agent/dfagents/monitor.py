from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

from deployment_agent.models import ACTIVE_STATES as _ACTIVE_STATES
from deployment_agent.models import DeploymentTarget, RunState
from deployment_agent.providers import target_of
from deployment_agent.security import redact_data, redact_text
from deployment_agent.state import StateStore
from deployment_agent.tools import command_exists, run_command


_TERMINAL_FAILURE_CONCLUSIONS = {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}


def select_workflow_run(
    runs: list[dict[str, Any]],
    head_sha: str = "",
    previous_url: str = "",
) -> dict[str, Any] | None:
    """Pick the workflow run that belongs to this deployment."""
    if head_sha:
        for item in runs:
            if str(item.get("headSha", "")) == head_sha:
                return item
        return None
    for item in runs:
        if previous_url and item.get("url") == previous_url:
            continue
        return item
    return None


class MonitorAgent:
    def __init__(self, store: StateStore, emit: Callable[..., object] | None = None):
        self.store = store
        self.emit = emit or (lambda *args, **kwargs: None)

    def snapshot(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run:
            raise ValueError("Run not found")
        repo_state = run.get("repo") or {}
        snapshot: dict[str, Any] = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "github": {},
            "aws": {},
            "vercel": {},
            "logs": [],
            "api": [],
            "errors": [],
        }
        if repo_state.get("repository") and command_exists("gh"):
            snapshot["github"] = self._github(repo_state["repository"], repo_state.get("push") or {})
        if target_of(run) is DeploymentTarget.VERCEL:
            if repo_state.get("vercel_project_id"):
                try:
                    self._vercel(run, snapshot)
                except Exception as exc:
                    snapshot["errors"].append(redact_text(str(exc)))
        elif target_of(run) is DeploymentTarget.AWS_ECS:

            try:
                self._ecs(run, snapshot)
            except Exception as exc:
                snapshot["errors"].append(redact_text(str(exc)))
        elif repo_state.get("region"):
            try:
                self._aws(run, snapshot)
            except Exception as exc:
                snapshot["errors"].append(redact_text(str(exc)))
        snapshot["api"] = self._validate_api(repo_state.get("application_url", ""))
        deploy_runs = [
            item
            for item in snapshot.get("github", {}).get("runs", [])
            if "deploy" in str(item.get("name", "")).lower()
        ]

        head_sha = str((repo_state.get("push") or {}).get("head_sha", ""))
        current_workflow = select_workflow_run(deploy_runs, head_sha)
        snapshot["workflow"] = current_workflow or {}
        if current_workflow:
            conclusion = str(current_workflow.get("conclusion") or "")
            in_flight = current_workflow.get("status") in {
                "queued", "in_progress", "waiting", "pending", "requested"}

            if run["state"] in {RunState.CI_RUNNING.value, RunState.DEPLOYING.value}:
                if in_flight:
                    self.store.transition_run(run_id, RunState.DEPLOYING)
                elif conclusion == "success":
                    self.store.transition_run(run_id, RunState.VALIDATING)

            if not in_flight and conclusion in _TERMINAL_FAILURE_CONCLUSIONS \
                    and run["state"] in _ACTIVE_STATES:
                self.store.transition_run(
                    run_id,
                    RunState.FAILED,
                    error=f"GitHub deployment workflow concluded with {conclusion}: {current_workflow.get('url', '')}",
                )
                self.emit(
                    run_id,
                    "error",
                    "deploy",
                    "failed",
                    100,
                    f"GitHub deployment workflow concluded with {conclusion}",
                    current_workflow,
                )
            run = self.store.get_run(run_id) or run
        readiness = self._readiness(run, snapshot)
        snapshot["readiness"] = readiness
        sanitized = self._mask_identifiers(redact_data(snapshot))
        self.store.update_run(run_id, monitor_json=sanitized, readiness_json=readiness)
        latest = self.store.get_run(run_id)
        if (
            latest
            and latest["state"] in {RunState.VALIDATING.value, RunState.LIVE.value}
            and readiness["score"] >= 90
            and snapshot["api"]
            and all(item.get("passed") for item in snapshot["api"])
        ):
            if latest["state"] != RunState.LIVE.value:
                self.emit(run_id, "step", "validation", "complete", 98, "Homepage and health API validation passed")
                self._capture_evidence(run_id)
            self.store.transition_run(run_id, RunState.LIVE)
        return sanitized

    def _capture_evidence(self, run_id: str) -> None:
        """Capture the dashboard now that the run is live."""
        try:
            from deployment_agent.exporter import EvidenceExporter

            exporter = EvidenceExporter(self.store)
            captured = exporter.capture_dashboard(run_id)
        except Exception as exc:                                # noqa: BLE001
            self.emit(run_id, "warning", "evidence", "complete", 99,
                      f"Evidence capture could not run: {redact_text(str(exc))[:160]}")
            return

        if captured:
            self.emit(run_id, "step", "evidence", "complete", 99,
                      f"Captured {len(captured)} masked evidence image(s) for export")
            return

        why = getattr(exporter, "last_capture_error", "")
        self.emit(
            run_id, "warning", "evidence", "complete", 99,
            f"No evidence image was captured — {redact_text(why)[:160]}"
            if why else
            "No evidence image was captured; the .zip and .pdf still export "
            "everything else this run recorded.")

    @staticmethod
    def _github(repo: str, push: dict[str, Any]) -> dict[str, Any]:
        result = run_command(
            [
                "gh",
                "run",
                "list",
                "--repo",
                repo,
                "--limit",
                "10",
                "--json",
                "databaseId,name,displayTitle,status,conclusion,url,headSha,createdAt,updatedAt",
            ],
            timeout=30,
        )
        if result.returncode != 0:
            return {"repository": repo, "runs": [], "error": result.stderr}
        runs = json.loads(result.stdout or "[]")
        value = {
            "repository": repo,
            "runs": runs,
            "successful": sum(1 for item in runs if item.get("conclusion") == "success"),
            "failed": sum(1 for item in runs if item.get("conclusion") == "failure"),
        }
        if push.get("mode") == "pull_request" and push.get("url"):
            pr = run_command(
                [
                    "gh", "pr", "view", push["url"], "--repo", repo,
                    "--json", "state,mergeStateStatus,statusCheckRollup,url,headRefName,baseRefName,mergedAt",
                ],
                timeout=30,
            )
            if pr.returncode == 0:
                value["pull_request"] = json.loads(pr.stdout or "{}")
            else:
                value["pull_request"] = {"url": push["url"], "error": pr.stderr}
        return value

    def _vercel(self, run: dict, snapshot: dict[str, Any]) -> None:
        """Collect the Vercel equivalents of the AWS instance/release/log views."""
        from deployment_agent import vercel_api
        from deployment_agent.vercel_auth import require_vercel_token

        repo = run["repo"]
        project_id = str(repo.get("vercel_project_id", ""))
        team_id = str(repo.get("vercel_team_id", ""))
        token = require_vercel_token()

        vercel: dict[str, Any] = {
            "project_id": project_id,
            "project_name": repo.get("vercel_project_name", ""),
            "org_id": repo.get("vercel_org_id", ""),
            "application_url": repo.get("application_url", ""),
        }
        deployments = vercel_api.list_deployments(token, project_id, team_id, limit=20)
        vercel["deployments"] = [
            {
                "id": item.get("uid") or item.get("id", ""),
                "ready_state": item.get("readyState") or item.get("state", ""),
                "url": f"https://{item['url']}" if item.get("url") else "",
                "commit_sha": (item.get("meta") or {}).get("githubCommitSha", ""),
                "commit_message": (item.get("meta") or {}).get("githubCommitMessage", ""),
                "created_at": (
                    datetime.fromtimestamp(item["created"] / 1000, tz=timezone.utc).isoformat()
                    if isinstance(item.get("created"), (int, float))
                    else ""
                ),
            }
            for item in deployments
        ]
        try:
            vercel["domains"] = [
                {"name": item.get("name", ""), "verified": bool(item.get("verified"))}
                for item in vercel_api.list_domains(token, project_id, team_id)
            ]
        except Exception as exc:
            snapshot["errors"].append(redact_text(str(exc)))
            vercel["domains"] = []
        snapshot["vercel"] = vercel

        newest = vercel["deployments"][0] if vercel["deployments"] else None
        if newest and newest["id"]:
            try:
                snapshot["logs"] = vercel_api.deployment_events(token, newest["id"], team_id)
            except Exception as exc:
                snapshot["errors"].append(redact_text(str(exc)))

    def _stacks(self, session, slug: str) -> list:
        """The bootstrap stack, its outputs and its recent events."""
        cfn = session.client("cloudformation")
        stacks: list[dict[str, Any]] = []
        for stack_name in (f"{slug}-bootstrap",):
            try:
                stack = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]
            except Exception as exc:
                stacks.append({"name": stack_name, "status": "NOT_FOUND", "error": str(exc)})
                continue
            stack_events: list[dict[str, Any]] = []
            try:
                event_kwargs: dict[str, Any] = {"StackName": stack_name}
                for _ in range(3):
                    event_page = cfn.describe_stack_events(**event_kwargs)
                    for event in event_page.get("StackEvents", []):
                        stack_events.append(
                            {
                                "timestamp": event.get("Timestamp").isoformat() if event.get("Timestamp") else "",
                                "logical_resource_id": event.get("LogicalResourceId", ""),
                                "resource_type": event.get("ResourceType", ""),
                                "status": event.get("ResourceStatus", ""),
                                "reason": redact_text(event.get("ResourceStatusReason", "")),
                            }
                        )
                    token = event_page.get("NextToken")
                    if not token or len(stack_events) >= 80:
                        break
                    event_kwargs["NextToken"] = token
            except Exception as exc:
                stack_events.append({"status": "UNAVAILABLE", "reason": redact_text(str(exc))})
            stacks.append(
                {
                    "name": stack_name,
                    "status": stack.get("StackStatus"),
                    "outputs": {item["OutputKey"]: item.get("OutputValue") for item in stack.get("Outputs", [])},
                    "events": stack_events[:80],
                }
            )
        return stacks

    def _cloudwatch(self, session, run: dict, snapshot: dict[str, Any]) -> None:
        """The application's own log lines."""
        repo = run["repo"]
        slug = run["plan"]["project_slug"]
        logs = session.client("logs")
        try:
            events = []
            log_kwargs: dict[str, Any] = {
                "logGroupName": repo.get("log_group") or f"/deployment-agent/{slug}",
                "limit": 100,
                "interleaved": True,
            }
            seen_tokens: set[str] = set()
            for _ in range(4):
                page = logs.filter_log_events(**log_kwargs)
                events.extend(page.get("events", []))
                token = page.get("nextToken", "")
                if not token or token in seen_tokens or len(events) >= 200:
                    break
                seen_tokens.add(token)
                log_kwargs["nextToken"] = token
            snapshot["logs"] = [
                {"timestamp": item.get("timestamp"), "message": redact_text(item.get("message", ""))}
                for item in events[-100:]
            ]
        except Exception as exc:
            snapshot["errors"].append(redact_text(str(exc)))

    def _ecs(self, run: dict, snapshot: dict[str, Any]) -> None:
        """The container equivalent of `_aws`, under the same `aws` key."""
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is not installed") from exc
        repo = run["repo"]
        kwargs: dict[str, str] = {"region_name": repo["region"]}
        if repo.get("aws_profile"):
            kwargs["profile_name"] = repo["aws_profile"]
        session = boto3.Session(**kwargs)
        slug = run["plan"]["project_slug"]
        aws: dict[str, Any] = {
            "region": repo["region"],
            "ecr_repository": repo.get("ecr_repository", ""),
            "ecs_cluster": repo.get("ecs_cluster", ""),
            "ecs_service": repo.get("ecs_service", ""),
            "load_balancer_dns": repo.get("load_balancer_dns", ""),
        }
        aws["stacks"] = self._stacks(session, slug)

        cluster = repo.get("ecs_cluster", "")
        service = repo.get("ecs_service", "")
        aws["services"] = []
        aws["tasks"] = []
        aws["releases"] = []
        if cluster and service:
            ecs = session.client("ecs")
            try:
                described = ecs.describe_services(cluster=cluster, services=[service])
                for item in described.get("services", []):
                    deployments = item.get("deployments") or []
                    primary = deployments[0] if deployments else {}
                    task_def = str(primary.get("taskDefinition") or "")
                    image = ""
                    if task_def:
                        try:
                            definition = ecs.describe_task_definition(taskDefinition=task_def)
                            containers = definition["taskDefinition"].get("containerDefinitions") or []
                            image = str((containers[0] if containers else {}).get("image") or "")
                        except Exception:               # noqa: BLE001
                            image = ""
                    aws["services"].append({
                        "name": item.get("serviceName", ""),
                        "status": item.get("status", ""),
                        "desired_count": item.get("desiredCount", 0),
                        "running_count": item.get("runningCount", 0),
                        "pending_count": item.get("pendingCount", 0),
                        "launch_type": item.get("launchType", ""),
                        "task_definition": task_def,

                        "image": image,
                        "rollout_state": str(primary.get("rolloutState") or ""),
                        "rollout_reason": str(primary.get("rolloutStateReason") or ""),
                    })

                    for deployment in deployments[:8]:
                        aws["releases"].append({
                            "id": deployment.get("id", ""),
                            "status": deployment.get("status", ""),
                            "rollout_state": str(deployment.get("rolloutState") or ""),
                            "task_definition": str(deployment.get("taskDefinition") or ""),
                            "running_count": deployment.get("runningCount", 0),
                            "created_at": str(deployment.get("createdAt") or ""),
                        })
            except Exception as exc:                    # noqa: BLE001
                snapshot["errors"].append(redact_text(str(exc)))
            try:
                arns = (ecs.list_tasks(cluster=cluster, serviceName=service)
                        .get("taskArns") or [])
                if arns:
                    for task in ecs.describe_tasks(cluster=cluster,
                                                   tasks=arns[:10]).get("tasks", []):
                        aws["tasks"].append({
                            "task_arn": task.get("taskArn", ""),
                            "last_status": task.get("lastStatus", ""),
                            "desired_status": task.get("desiredStatus", ""),
                            "health_status": task.get("healthStatus", ""),
                            "cpu": task.get("cpu", ""),
                            "memory": task.get("memory", ""),
                            "started_at": str(task.get("startedAt") or ""),
                        })
            except Exception as exc:                    # noqa: BLE001
                snapshot["errors"].append(redact_text(str(exc)))

        snapshot["aws"] = aws
        self._cloudwatch(session, run, snapshot)

    def _aws(self, run: dict, snapshot: dict[str, Any]) -> None:
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is not installed") from exc
        repo = run["repo"]
        kwargs: dict[str, str] = {"region_name": repo["region"]}
        if repo.get("aws_profile"):
            kwargs["profile_name"] = repo["aws_profile"]
        session = boto3.Session(**kwargs)
        slug = run["plan"]["project_slug"]
        aws: dict[str, Any] = {
            "region": repo["region"],
            "instance_id": repo.get("instance_id", ""),
            "artifact_bucket": repo.get("artifact_bucket", ""),
        }
        aws["stacks"] = self._stacks(session, slug)
        instance_id = repo.get("instance_id", "")
        aws["instances"] = []
        if instance_id:
            try:
                reservations = session.client("ec2").describe_instances(InstanceIds=[instance_id]).get("Reservations", [])
            except Exception as exc:
                reservations = []
                snapshot["errors"].append(redact_text(str(exc)))
            for reservation in reservations:
                for item in reservation.get("Instances", []):
                    aws["instances"].append(
                        {
                            "instance_id": item.get("InstanceId", ""),
                            "state": (item.get("State") or {}).get("Name", ""),
                            "instance_type": item.get("InstanceType", ""),
                            "public_ip": item.get("PublicIpAddress", ""),
                            "private_ip": item.get("PrivateIpAddress", ""),
                            "availability_zone": (item.get("Placement") or {}).get("AvailabilityZone", ""),
                            "launched_at": item.get("LaunchTime").isoformat() if item.get("LaunchTime") else "",
                        }
                    )

        aws["releases"] = []
        if instance_id:
            try:
                invocations = session.client("ssm").list_command_invocations(
                    InstanceId=instance_id, MaxResults=20, Details=False
                ).get("CommandInvocations", [])
            except Exception:
                invocations = []
            aws["releases"] = [
                {
                    "command_id": item.get("CommandId", ""),
                    "status": item.get("Status", ""),
                    "comment": item.get("Comment", ""),
                    "requested_at": item.get("RequestedDateTime").isoformat() if item.get("RequestedDateTime") else "",
                }
                for item in sorted(
                    invocations,
                    key=lambda value: value.get("RequestedDateTime") or datetime.min.replace(tzinfo=timezone.utc),
                    reverse=True,
                )
            ]
        snapshot["aws"] = aws
        self._cloudwatch(session, run, snapshot)

    @staticmethod
    def _validate_api(base_url: str) -> list[dict[str, Any]]:
        if not base_url:
            return []
        try:
            import requests
        except ImportError:
            return [{"name": "HTTP client", "method": "GET", "path": "/", "status": 0, "passed": False, "error": "requests is not installed"}]
        results: list[dict[str, Any]] = []
        for path in ("/", "/api/health"):
            url = base_url.rstrip("/") + path
            try:
                response = requests.get(url, timeout=12, allow_redirects=True)
                results.append(
                    {
                        "name": "Homepage" if path == "/" else "Health API",
                        "method": "GET",
                        "path": path,
                        "status": response.status_code,
                        "elapsed_ms": round(response.elapsed.total_seconds() * 1000),
                        "passed": 200 <= response.status_code < 400,
                    }
                )
            except Exception as exc:
                results.append({"name": path, "method": "GET", "path": path, "status": 0, "passed": False, "error": str(exc)})
        return results

    @staticmethod
    def _readiness(run: dict, snapshot: dict[str, Any]) -> dict[str, Any]:

        ci_ok = str((snapshot.get("workflow") or {}).get("conclusion") or "") == "success"
        if target_of(run) is DeploymentTarget.VERCEL:
            deployments = snapshot.get("vercel", {}).get("deployments", [])
            head_sha = str(((run.get("repo") or {}).get("push") or {}).get("head_sha", ""))
            newest = deployments[0] if deployments else {}

            provider_ok = bool(newest) and newest.get("ready_state") == "READY" and (
                not head_sha or str(newest.get("commit_sha", "")) == head_sha
            )
        elif target_of(run) is DeploymentTarget.AWS_ECS:
            aws = snapshot.get("aws", {})
            services = aws.get("services", [])
            head_sha = str(((run.get("repo") or {}).get("push") or {}).get("head_sha", ""))
            newest = services[0] if services else {}

            running_ok = (
                bool(newest)
                and int(newest.get("running_count") or 0) >= int(newest.get("desired_count") or 1)
                and str(newest.get("rollout_state") or "") in ("COMPLETED", "")
            )
            image_ok = not head_sha or head_sha in str(newest.get("image") or "")
            provider_ok = running_ok and image_ok
        else:
            instances = snapshot.get("aws", {}).get("instances", [])
            releases = snapshot.get("aws", {}).get("releases", [])
            provider_ok = (
                bool(instances)
                and all(item.get("state") == "running" for item in instances)
                and bool(releases)
                and releases[0].get("status") == "Success"
            )
        logs_ok = bool(snapshot.get("logs"))
        api = snapshot.get("api", [])
        api_ok = bool(api) and all(item.get("passed") for item in api)
        security = (run.get("readiness") or {}).get("categories", {}).get("security", 20)
        review_categories = (run.get("readiness") or {}).get("categories", {})
        categories = {
            "build": review_categories.get("build", 0),
            "cicd": 20 if ci_ok else 0,
            "provider": 25 if provider_ok else 0,
            "security": security,
            "monitoring": 10 if logs_ok else 0,
            "api": 10 if api_ok else 0,
        }
        return {"score": sum(categories.values()), "categories": categories, "phase": "live" if api_ok else "deploying"}

    @staticmethod
    def _mask_identifiers(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: MonitorAgent._mask_identifiers(item) for key, item in value.items()}
        if isinstance(value, list):
            return [MonitorAgent._mask_identifiers(item) for item in value]
        if isinstance(value, str):
            value = re.sub(r"(?<!\d)\d{12}(?!\d)", "***ACCOUNT***", value)
            value = re.sub(r"(sha256:)[a-f0-9]{20,}", r"\1***masked***", value)
            return value
        return value

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from dfagents.generator import ArtifactGeneratorAgent
from dfagents.intake import IntakeAgent
from dfagents.planner import PlannerAgent
from dfagents.validator import SecurityValidatorAgent

from .config import RUNS_DIR
from .events import EventBus
from .models import DeploymentTarget, RunState
from .security import redact_text
from .state import StateStore
from .tools import command_exists, run_command


class Orchestrator:
    def __init__(self, store: StateStore, events: EventBus):
        self.store = store
        self.events = events

    def start_analysis(
        self,
        source_path: str,
        validate_container: bool = True,
        target: DeploymentTarget = DeploymentTarget.AWS_EC2,
    ) -> str:
        source = Path(source_path).resolve()
        run_id = uuid.uuid4().hex
        run_dir = RUNS_DIR / run_id
        staged = run_dir / "worktree"
        self.store.create_run(run_id, source.name, str(source), str(staged))
        threading.Thread(
            target=self._analyze,
            args=(run_id, source, staged, validate_container, target),
            daemon=True,
        ).start()
        return run_id

    def _analyze(
        self,
        run_id: str,
        source: Path,
        staged: Path,
        validate_container: bool,
        target: DeploymentTarget = DeploymentTarget.AWS_EC2,
    ) -> None:
        emit = self.events.emit
        self.store.transition_run(run_id, RunState.ANALYZING)
        emit(run_id, "state", "analysis", "running", 1, "Deployment analysis started")
        try:
            spec = IntakeAgent(emit).stage_and_analyze(run_id, source, staged)
            self.store.update_run(run_id, project_name=spec.name, spec_json=spec.to_dict())

            try:
                from deploy_agent.bridge import ollama_client
                planner = PlannerAgent(emit=emit, client=ollama_client())
            except Exception:                                   # noqa: BLE001
                planner = PlannerAgent(emit=emit)
            plan = planner.plan(run_id, spec)
            generator = ArtifactGeneratorAgent(emit)
            records = generator.generate(run_id, spec, plan, staged, target=target)
            record_dicts = [record.to_dict() for record in records]
            validation = SecurityValidatorAgent(emit).validate(run_id, staged, record_dicts, target=target)
            readiness_path = staged / "readiness-score.json"
            readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
            if validation["passed"]:
                readiness["categories"]["security"] = 20
                readiness["score"] = sum(readiness["categories"].values())
            plan.risks.extend(validation["warnings"])
            build_result: dict[str, Any] = {"attempted": False, "passed": False, "output": ""}
            if validation["passed"] and validate_container and os.environ.get("DEPLOYMENT_AGENT_SKIP_BUILD_VALIDATION") != "1":
                build_result = self._validate_build(run_id, spec, staged, target)
                if build_result["attempted"] and not build_result["passed"] and plan.model_used:
                    try:
                        actions = planner.repair_build(run_id, spec, plan, build_result.get("output", ""))
                        records, repaired = generator.repair_compatibility(
                            spec, plan, staged, records, actions, build_result.get("output", "")
                        )
                        if repaired:
                            record_dicts = [record.to_dict() for record in records]
                            validation = SecurityValidatorAgent(emit).validate(run_id, staged, record_dicts, target=target)
                            if validation["passed"]:
                                build_result = self._validate_build(run_id, spec, staged, target)
                    except Exception as exc:
                        plan.risks.append("The bounded Ollama build repair did not produce a valid safe action.")
                        emit(run_id, "warning", "repair", "failed", 93, redact_text(str(exc)))
                if build_result["attempted"] and not build_result["passed"]:
                    readiness["categories"]["build"] = 8
                    readiness["score"] = sum(readiness["categories"].values())
                    plan.risks.append("Local build validation failed; inspect the terminal output before deployment.")
                elif build_result["passed"]:
                    readiness["categories"]["build"] = 15
                    readiness["score"] = sum(readiness["categories"].values())
                    if not plan.repair_actions:
                        emit(run_id, "step", "repair", "complete", 93, "No compatibility repair was required")
                findings = build_result.get("dependency_findings", {})
                if build_result.get("passed") and findings.get("total", 0):
                    if findings.get("critical", 0):
                        readiness["categories"]["security"] = min(readiness["categories"]["security"], 8)
                    elif findings.get("high", 0):
                        readiness["categories"]["security"] = min(readiness["categories"]["security"], 12)
                    else:
                        readiness["categories"]["security"] = min(readiness["categories"]["security"], 16)
                    readiness["score"] = sum(readiness["categories"].values())
                    plan.risks.append(
                        "Local dependency installation reported "
                        f"{findings['total']} vulnerability finding(s), including development dependencies; "
                        "review the CI production dependency audit before deployment."
                    )
            readiness["gates"] = {
                "model_used": plan.model_used,
                "artifacts_valid": validation["passed"],
                "build_validation": bool(build_result.get("passed")),
                "security_validation": validation["passed"],
            }
            records = generator.finalize_review(spec, plan, staged, readiness, records)
            record_dicts = [record.to_dict() for record in records]
            self.store.set_artifacts(run_id, record_dicts)
            self.store.transition_run(
                run_id,
                RunState.REVIEW_READY if validation["passed"] else RunState.FAILED,
                plan_json=plan.to_dict(),
                readiness_json=readiness,
                error="" if validation["passed"] else "; ".join(validation["errors"]),
            )
            emit(run_id, "state", "analysis", "complete", 100,
                 "Deployment analysis complete")
            emit(
                run_id,
                "state",
                "review",
                "complete" if validation["passed"] else "failed",
                100,
                "Review is ready. No source or cloud resources have been changed."
                if validation["passed"]
                else "Artifact validation failed",
                {"readiness": readiness, "artifacts": len(records)},
            )
        except Exception as exc:
            self.store.transition_run(run_id, RunState.FAILED, error=str(exc))
            emit(run_id, "error", "analysis", "failed", 100, str(exc))

    def _validate_build(
        self,
        run_id: str,
        spec,
        staged: Path,
        target: DeploymentTarget = DeploymentTarget.AWS_EC2,
    ) -> dict[str, Any]:
        """Install and build the staged copy exactly as CI will, without Docker."""
        if not command_exists("node"):
            self.events.emit(run_id, "warning", "build", "skipped", 86, "Node.js not found; build validation skipped")
            return {"attempted": False, "passed": False}
        service = spec.services[0]
        root = staged / service.root if service.root else staged
        steps = [
            ("install", service.install_command, 900),
            ("build", service.build_command or "npm run build", 1200),
        ]
        combined: list[str] = []
        returncode = 0
        for label, command, timeout in steps:
            self.events.emit(run_id, "terminal", "build", "running", 87, f"Running local {label}: {command}")
            try:
                result = run_command(
                    command.split(),
                    cwd=str(root),
                    timeout=timeout,

                    env={
                        "NODE_ENV": "",
                        "NEXT_TELEMETRY_DISABLED": "1",
                        "MONGODB_URI": "mongodb://127.0.0.1:27017/deployment_agent_build",
                    },
                )
            except Exception as exc:
                self.events.emit(run_id, "terminal", "build", "failed", 90, str(exc))
                return {"attempted": True, "passed": False, "output": redact_text(str(exc))}
            combined.append(f"$ {command}\n{result.stdout}\n{result.stderr}")
            returncode = result.returncode
            if returncode != 0:
                break
        output = redact_text("\n".join(combined)[-12000:])
        dependency_findings = self._dependency_findings(output)

        if target in (DeploymentTarget.AWS_EC2, DeploymentTarget.AWS_ECS):
            standalone = (root / ".next" / "standalone" / "server.js").is_file()
            if returncode == 0 and not standalone:
                returncode = 1
                output += "\n[deployment-agent] .next/standalone/server.js was not produced; output: 'standalone' is required."
        self.events.emit(
            run_id,
            "terminal",
            "build",
            "complete" if returncode == 0 else "failed",
            90,
            "Local build passed" if returncode == 0 else "Local build failed",
            {"output": output, "returncode": returncode},
        )
        if dependency_findings.get("total", 0):
            self.events.emit(
                run_id,
                "warning",
                "security",
                "warning",
                92,
                (
                    f"Dependency installation reported {dependency_findings['total']} vulnerability finding(s) "
                    f"({dependency_findings.get('high', 0)} high, {dependency_findings.get('critical', 0)} critical)"
                ),
                dependency_findings,
            )
        return {
            "attempted": True,
            "passed": returncode == 0,
            "dependency_findings": dependency_findings,
            "output": output,
        }

    @staticmethod
    def _dependency_findings(output: str) -> dict[str, int]:
        def count(label: str) -> int:
            matches = re.findall(rf"(\d+)\s+{label}\b", output, flags=re.IGNORECASE)
            return max((int(value) for value in matches), default=0)

        totals = re.findall(r"(\d+)\s+vulnerabilit(?:y|ies)\b", output, flags=re.IGNORECASE)
        return {
            "total": max((int(value) for value in totals), default=0),
            "moderate": count("moderate"),
            "high": count("high"),
            "critical": count("critical"),
        }

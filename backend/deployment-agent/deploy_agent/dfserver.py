"""DeployForge's HTTP API, as AgentForge runs it."""
from __future__ import annotations

import json
import mimetypes
import os
import threading
import urllib.parse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from dfagents.deployer import DeploymentAgent, DeploymentConflictError
from dfagents.generator import ArtifactGeneratorAgent
from dfagents.monitor import MonitorAgent
from deployment_agent.config import ARTIFACT_SCHEMA_VERSION, HTTP_HOST, HTTP_PORT
from deployment_agent.events import EventBus
from deployment_agent.exporter import EvidenceExporter
from deployment_agent.models import DeploymentTarget
from deployment_agent.orchestrator import Orchestrator
from deployment_agent.providers import target_of
from deployment_agent.aws_onboarding import (
    SsoDeviceFlowManager,
    actions_for,
    bootstrap_role_template,
    check_permissions,
    check_quotas,
    persist_sso_profile,
    request_quota_increase,
    session_from_reference,
)
from deployment_agent.mongodb_check import check_mongodb_uri
from deployment_agent.security import redact_data
from deployment_agent.vercel_auth import connection_status as vercel_connection_status
from deployment_agent.state import StateStore
from deployment_agent.supervisor import reconcile_once, start_supervisor
from deployment_agent.tools import run_command, start_login, tool_status


BASE_DIR = Path(__file__).resolve().parent

STORE = StateStore()
EVENTS = EventBus(STORE)
ORCHESTRATOR = Orchestrator(STORE, EVENTS)
DEPLOYER = DeploymentAgent(STORE, EVENTS.emit)
MONITOR = MonitorAgent(STORE, EVENTS.emit)
EXPORTER = EvidenceExporter(STORE)
SSO_FLOWS = SsoDeviceFlowManager()
HTTP_SERVER: ThreadingHTTPServer | None = None


def _migrate_readiness(value: dict[str, Any]) -> None:
    """Runs deployed before Vercel support scored an "aws" category."""
    readiness = value.get("readiness")
    if not isinstance(readiness, dict):
        return
    categories = readiness.get("categories")
    if isinstance(categories, dict) and "aws" in categories and "provider" not in categories:
        categories["provider"] = categories.pop("aws")


def public_run(run: dict[str, Any]) -> dict[str, Any]:
    value = redact_data(run)
    _migrate_readiness(value)
    version = 0
    try:
        manifest = json.loads((Path(run["staged_path"]) / "deployment-manifest.json").read_text(encoding="utf-8"))
        version = int(manifest.get("version", 0))
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    value["artifact_schema_version"] = version
    value["artifacts_current"] = version == ARTIFACT_SCHEMA_VERSION

    masked = MonitorAgent._mask_identifiers(value)
    masked["id"] = run["id"]
    return masked


class APIHandler(SimpleHTTPRequestHandler):
    server_version = "DeploymentAgent/0.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def log_message(self, _format: str, *_args) -> None:
        return

    def end_headers(self) -> None:

        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/api/health":
                return self._json({"status": "ok", "http_port": HTTP_PORT})
            if path == "/api/onboarding/status":
                return self._json(self._onboarding())
            if path == "/api/runs":
                return self._json({"runs": [public_run(run) for run in STORE.list_runs()]})
            match = self._run_path(path)
            if match:
                run_id, action = match
                return self._get_run_action(run_id, action, query)

            return self._error(HTTPStatus.NOT_FOUND, "API endpoint not found")
        except DeploymentConflictError as exc:
            return self._error(HTTPStatus.CONFLICT, str(exc))
        except ValueError as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            body = self._body()

            if path == "/api/onboarding/login":
                tool = str(body.get("tool", ""))
                profile = str(body.get("profile", "")).strip()
                region = str(body.get("region", "")).strip()
                start_login(tool, profile, region)
                return self._json(
                    {"started": True, "tool": tool, "profile": profile or "deployment-agent"},
                    HTTPStatus.ACCEPTED,
                )
            if path == "/api/mongodb/check":

                return self._json(check_mongodb_uri(str(body.get("uri", ""))))
            if path.startswith("/api/aws/"):
                return self._aws_action(path[len("/api/aws/"):].strip("/"), body)
            if path == "/api/runs/analyze":
                if body.get("cloud_consent") is not True:
                    raise ValueError("Ollama cloud source-context consent is required")
                source = str(body.get("path", "")).strip()
                if not source:
                    raise ValueError("Project path is required")
                try:
                    target = DeploymentTarget(str(body.get("target", DeploymentTarget.AWS_EC2.value)))
                except ValueError as exc:
                    raise ValueError(f"Unsupported deployment target: {body.get('target')}") from exc
                run_id = ORCHESTRATOR.start_analysis(
                    source, bool(body.get("validate_container", True)), target
                )
                return self._json({"run_id": run_id, "state": "ANALYZING"}, HTTPStatus.ACCEPTED)
            match = self._run_path(path)
            if match:
                run_id, action = match
                return self._post_run_action(run_id, action, body)
            return self._error(HTTPStatus.NOT_FOUND, "API endpoint not found")
        except DeploymentConflictError as exc:
            return self._error(HTTPStatus.CONFLICT, str(exc))
        except ValueError as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    @staticmethod
    def _run_path(path: str) -> tuple[str, str] | None:
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "runs":
            return parts[2], "/".join(parts[3:])
        return None

    def _get_run_action(self, run_id: str, action: str, query: dict[str, list[str]]) -> None:
        run = STORE.get_run(run_id)
        if not run:
            return self._error(HTTPStatus.NOT_FOUND, "Run not found")
        if not action:
            return self._json(public_run(run))
        if action == "domains":
            return self._json({"domains": DEPLOYER.vercel_domains(run_id)})
        if action == "events":
            after = int((query.get("after") or ["0"])[0])
            return self._json({"events": STORE.get_events(run_id, after_id=after)})
        if action == "artifacts":
            return self._json({"artifacts": STORE.get_artifacts(run_id)})
        if action == "diff":
            spec = self._spec_object(run["spec"])
            diff = ArtifactGeneratorAgent.diff(spec, Path(run["staged_path"]), STORE.get_artifacts(run_id))
            return self._json({"diff": diff})
        if action == "artifact":
            relative = (query.get("path") or [""])[0]
            return self._artifact(run, relative)
        if action == "monitor":
            return self._json(MONITOR.snapshot(run_id))
        if action == "evidence":
            return self._json({"evidence": STORE.list_evidence(run_id)})
        if action == "evidence/file":
            evidence_id = int((query.get("id") or ["0"])[0])
            return self._evidence_file(run_id, evidence_id)
        if action == "export.zip":
            return self._download(EXPORTER.export_zip(run_id), f"deployment-evidence-{run_id[:8]}.zip", "application/zip")
        if action == "report.pdf":
            return self._download(EXPORTER.export_pdf(run_id), f"deployment-report-{run_id[:8]}.pdf", "application/pdf")
        return self._error(HTTPStatus.NOT_FOUND, "Run action not found")

    def _aws_action(self, action: str, body: dict[str, Any]) -> None:
        """In-app AWS onboarding so the operator never leaves the dashboard."""
        if action == "sso/start":
            return self._json(
                SSO_FLOWS.start(str(body.get("start_url", "")), str(body.get("region", "")).strip()),
                HTTPStatus.ACCEPTED,
            )
        if action == "sso/poll":
            return self._json(SSO_FLOWS.poll(str(body.get("flow_id", ""))))
        if action == "sso/roles":
            flow_id = str(body.get("flow_id", ""))
            account_id = str(body.get("account_id", ""))
            return self._json({"roles": SSO_FLOWS.list_roles(flow_id, account_id)})
        if action == "sso/select":
            flow_id = str(body.get("flow_id", ""))
            result = SSO_FLOWS.select(
                flow_id, str(body.get("account_id", "")), str(body.get("role_name", ""))
            )
            if body.get("persist_profile") is True:
                result["profile"] = persist_sso_profile(
                    str(body.get("profile", "deployment-agent")),
                    SSO_FLOWS.start_url(flow_id),
                    result["region"],
                    result["account_id"],
                    result["role_name"],
                    deployment_region=str(body.get("deployment_region", "")).strip(),
                )
            return self._json(result)
        if action == "preflight":
            session = self._aws_session_from(body)

            try:
                target = DeploymentTarget(str(body.get("target", "")).strip())
            except ValueError:
                target = DeploymentTarget.AWS_EC2
            return self._json(
                {
                    "target": target.value,
                    "quotas": check_quotas(session, target),
                    "permissions": check_permissions(session, actions_for(target)),
                }
            )
        if action == "quota/request":
            session = self._aws_session_from(body)
            return self._json(
                request_quota_increase(
                    session,
                    str(body.get("service_code", "")),
                    str(body.get("quota_code", "")),
                    float(body.get("desired", 0)),
                ),
                HTTPStatus.ACCEPTED,
            )
        if action == "vercel/status":
            return self._json(vercel_connection_status(str(body.get("token", ""))))
        if action == "bootstrap-role-template":
            return self._json(
                {"template": bootstrap_role_template(str(body.get("principal_arn", "")))}
            )
        return self._error(HTTPStatus.NOT_FOUND, "AWS action not found")

    @staticmethod
    def _aws_session_from(body: dict[str, Any]):
        reference = str(body.get("credential_reference", "")).strip()
        region = str(body.get("region", "")).strip()
        if reference:
            return session_from_reference(reference, region)
        import boto3

        profile = str(body.get("aws_profile", "")).strip()
        kwargs: dict[str, str] = {}
        if region:
            kwargs["region_name"] = region
        if profile:
            kwargs["profile_name"] = profile
        return boto3.Session(**kwargs)

    def _post_run_action(self, run_id: str, action: str, body: dict[str, Any]) -> None:
        run = STORE.get_run(run_id)
        if not run:
            return self._error(HTTPStatus.NOT_FOUND, "Run not found")
        if action == "deploy":
            if body.get("approved") is not True:
                raise ValueError("Deployment approval is required")
            profile = str(body.get("aws_profile", "")).strip()
            region = str(body.get("region", "ap-south-1")).strip()
            mongodb_uri = str(body.get("mongodb_uri", "")).strip()
            credential_reference = str(body.get("credential_reference", "")).strip()
            vercel_token = str(body.get("vercel_token", "")).strip()
            DEPLOYER.start_deployment(
                run_id, profile, region, mongodb_uri, True, credential_reference, vercel_token
            )
            return self._json({"accepted": True, "run_id": run_id}, HTTPStatus.ACCEPTED)
        if action == "domains":
            name = str(body.get("domain", "")).strip()
            if body.get("remove") is True:
                return self._json(DEPLOYER.remove_vercel_domain(run_id, name))
            return self._json(DEPLOYER.add_vercel_domain(run_id, name), HTTPStatus.CREATED)
        if action == "cancel":

            if body.get("confirm") is not True:
                raise ValueError("Cancellation confirmation is required")
            return self._json(DEPLOYER.cancel(run_id), HTTPStatus.ACCEPTED)
        if action == "teardown":

            if body.get("confirm") is not True:
                raise ValueError("Teardown confirmation is required")

            return self._json(
                DEPLOYER.start_teardown(
                    run_id,
                    str(body.get("credential_reference", "")).strip(),
                    force=body.get("force") is True,
                ),
                HTTPStatus.ACCEPTED,
            )
        if action == "rollback":
            threading.Thread(target=self._rollback, args=(run_id,), daemon=True).start()
            return self._json({"accepted": True}, HTTPStatus.ACCEPTED)
        if action == "retry":
            new_id = ORCHESTRATOR.start_analysis(
                run["project_path"],
                bool(body.get("validate_container", True)),
                target_of(run),
            )
            return self._json({"run_id": new_id}, HTTPStatus.ACCEPTED)
        if action == "evidence":
            result = EXPORTER.save_evidence(run_id, str(body.get("name", "evidence")), str(body.get("data_url", "")))
            return self._json(result, HTTPStatus.CREATED)
        if action == "evidence/verify":
            STORE.verify_evidence(run_id, int(body.get("id", 0)), bool(body.get("verified", True)))
            return self._json({"updated": True})
        return self._error(HTTPStatus.NOT_FOUND, "Run action not found")

    @staticmethod
    def _rollback(run_id: str) -> None:
        try:
            DEPLOYER.rollback(run_id)
        except Exception as exc:
            STORE.update_run(run_id, error=str(exc))
            EVENTS.emit(run_id, "error", "rollback", "failed", 100, str(exc))

    @staticmethod
    def _spec_object(data: dict[str, Any]):
        from deployment_agent.models import EnvironmentVariable, ProjectSpec, RepositorySpec, ServiceSpec

        services = []
        for raw in data.get("services", []):
            raw = dict(raw)
            raw["environment"] = [EnvironmentVariable(**item) for item in raw.get("environment", [])]
            services.append(ServiceSpec(**raw))
        return ProjectSpec(
            name=data["name"],
            source_path=data["source_path"],
            staged_path=data["staged_path"],
            services=services,
            repository=RepositorySpec(**data["repository"]),
            warnings=data.get("warnings", []),
        )

    def _artifact(self, run: dict[str, Any], relative: str) -> None:
        allowed = {item["path"] for item in STORE.get_artifacts(run["id"])}
        if relative not in allowed:
            raise ValueError("Artifact path is not part of the reviewed manifest")
        staged = Path(run["staged_path"]).resolve()
        target = (staged / relative).resolve()
        if staged not in target.parents:
            raise ValueError("Invalid artifact path")
        data = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "text/plain; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _evidence_file(self, run_id: str, evidence_id: int) -> None:
        item = next((entry for entry in STORE.list_evidence(run_id) if entry["id"] == evidence_id), None)
        if not item:
            return self._error(HTTPStatus.NOT_FOUND, "Evidence image not found")
        path = Path(item["path"])
        run = STORE.get_run(run_id)
        if not run:
            return self._error(HTTPStatus.NOT_FOUND, "Run not found")
        evidence_root = (Path(run["staged_path"]).parent / "evidence").resolve()
        resolved = path.resolve()
        if evidence_root not in resolved.parents or not resolved.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "Evidence image not found")
        data = resolved.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(resolved.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _onboarding() -> dict[str, Any]:
        status = tool_status()
        profiles: list[str] = []
        aws_identities: dict[str, dict[str, Any]] = {}
        if status.get("aws", {}).get("installed"):
            result = run_command(["aws", "configure", "list-profiles"], timeout=10)
            if result.returncode == 0:
                profiles = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            for profile in profiles[:20]:

                configured_region = run_command(
                    ["aws", "configure", "get", "region", "--profile", profile],
                    timeout=10,
                )
                region = configured_region.stdout.strip() if configured_region.returncode == 0 else ""
                region = region or "ap-south-1"
                identity = run_command(
                    [
                        "aws", "sts", "get-caller-identity", "--profile", profile,
                        "--region", region, "--output", "json",
                    ],
                    timeout=15,
                )
                if identity.returncode != 0:
                    continue
                try:
                    payload = json.loads(identity.stdout)
                except json.JSONDecodeError:
                    continue
                cloudformation = run_command(
                    [
                        "aws", "cloudformation", "list-stacks",
                        "--profile", profile,
                        "--region", region,
                        "--max-items", "1",
                        "--output", "json",
                        "--no-cli-pager",
                    ],
                    timeout=20,
                )
                service_error = ""
                if cloudformation.returncode != 0:
                    raw_error = f"{cloudformation.stderr}\n{cloudformation.stdout}".lower()
                    if "optinrequired" in raw_error or "not subscribed" in raw_error or "needs a subscription" in raw_error:
                        service_error = "AWS account services are not activated (OptInRequired)"
                    elif "expired" in raw_error:
                        service_error = "AWS login session expired"
                    elif "accessdenied" in raw_error or "not authorized" in raw_error:
                        service_error = "CloudFormation access is denied for this identity"
                    else:
                        service_error = "CloudFormation preflight failed"
                aws_identities[profile] = {
                    "account": str(payload.get("Account", "")),
                    "arn": str(payload.get("Arn", "")),
                    "user_id": str(payload.get("UserId", "")),
                    "region": region,
                    "service_ready": cloudformation.returncode == 0,
                    "service_error": service_error,
                }
        github_authenticated = False
        github_account = ""
        if status.get("gh", {}).get("installed"):
            github_authenticated = run_command(["gh", "auth", "status"], timeout=15).returncode == 0
            if github_authenticated:
                account = run_command(["gh", "api", "user", "--jq", ".login"], timeout=15)
                if account.returncode == 0:
                    github_account = account.stdout.strip()
        return {
            "tools": status,
            "aws_profiles": profiles,
            "aws_authenticated": bool(aws_identities),
            "aws_identities": aws_identities,
            "github_authenticated": github_authenticated,
            "github_account": github_account,
            "cloud_notice": "Selected source context is sent to Ollama Cloud for deployment planning. Credentials and detected secret values are always redacted.",
        }

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 16 * 1024 * 1024:
            raise ValueError("Request body exceeds 16 MB")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON request body must be an object")
        return value

    def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: HTTPStatus, message: str) -> None:
        return self._json({"error": message}, status)

    def _download(self, data: bytes, filename: str, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start_http(port: int = HTTP_PORT, host: str = HTTP_HOST) -> None:
    """Block on the HTTP server."""
    global HTTP_SERVER
    HTTP_SERVER = ThreadingHTTPServer((host, port), APIHandler)
    HTTP_SERVER.serve_forever(poll_interval=0.3)


def start_reconciliation() -> None:
    """Recover deployments abandoned by a previous process, then keep watching."""
    if os.environ.get("DEPLOYMENT_AGENT_NO_SUPERVISOR") == "1":
        return
    try:
        recovered = reconcile_once(STORE, MONITOR, EVENTS.emit)
        for item in recovered:
            print(f"Recovered run {item['run_id'][:8]}: {item['from']} -> {item['to']}")
    except Exception as exc:  # noqa: BLE001 - startup must not fail on a transient cloud error
        print(f"Startup reconciliation skipped: {exc}")
    start_supervisor(STORE, MONITOR, EVENTS.emit)

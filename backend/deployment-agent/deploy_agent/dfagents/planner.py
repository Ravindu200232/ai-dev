from __future__ import annotations

import json
import re
from typing import Any, Callable

import requests
from jsonschema import Draft202012Validator

from deployment_agent.config import OLLAMA_MODEL, OLLAMA_NUM_PREDICT, OLLAMA_THINK, OLLAMA_TIMEOUT_SECONDS, OLLAMA_URL
from deployment_agent.models import DeploymentPlan, ProjectSpec
from deployment_agent.security import redact_data


class OllamaClient:
    def __init__(self, base_url: str = OLLAMA_URL, model: str = OLLAMA_MODEL,
                 headers: dict | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model

        self.headers = dict(headers or {})

        self._native_schema_supported: bool | None = (
            False if model.endswith(":cloud") or model.endswith("-cloud") else None
        )

    def ensure_model(self, progress: Callable[[str], None] | None = None) -> None:
        progress = progress or (lambda _message: None)
        response = requests.get(f"{self.base_url}/api/tags", headers=self.headers, timeout=5)
        response.raise_for_status()
        models = [item.get("name", "") for item in response.json().get("models", [])]
        if any(name == self.model for name in models):
            return
        progress(f"Pulling Ollama cloud model manifest: {self.model}")
        response = requests.post(
            f"{self.base_url}/api/pull",
            json={"name": self.model},
            headers=self.headers,
            stream=True,
            timeout=300,
        )
        response.raise_for_status()
        last_status = ""
        for line in response.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            status = chunk.get("status", "")
            if status and status != last_status:
                progress(status)
                last_status = status

    def chat_json(
        self,
        system: str,
        payload: dict[str, Any],
        schema: dict[str, Any],
        attempts: int = 2,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if attempts < 1 or attempts > 3:
            raise ValueError("Ollama repair attempts must be between 1 and 3")
        progress = progress or (lambda _message: None)
        validator = Draft202012Validator(schema)
        schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        messages = [
            {
                "role": "system",
                "content": (
                    f"{system}\n\nRequired JSON Schema:\n{schema_text}\n"
                    "Return exactly one JSON object that validates against this schema."
                ),
            },
            {"role": "user", "content": json.dumps(redact_data(payload), ensure_ascii=False)},
        ]
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = None
                modes = [False] if self._native_schema_supported is False else [True, False]
                for native_schema in modes:
                    request_body: dict[str, Any] = {
                        "model": self.model,
                        "messages": messages,
                        "stream": True,
                        "think": OLLAMA_THINK,
                        "options": {"temperature": 0, "num_predict": OLLAMA_NUM_PREDICT},
                    }
                    if native_schema:
                        request_body["format"] = schema
                    candidate = requests.post(
                        f"{self.base_url}/api/chat",
                        json=request_body,
                        headers=self.headers,
                        stream=True,
                        timeout=OLLAMA_TIMEOUT_SECONDS,
                    )
                    try:
                        candidate.raise_for_status()
                        response = candidate
                        self._native_schema_supported = native_schema
                        break
                    except Exception as exc:

                        if native_schema:
                            self._native_schema_supported = False
                            progress("Ollama native schema output is unavailable; using validated JSON fallback")
                            continue
                        raise exc
                if response is None:
                    raise RuntimeError("Ollama returned no usable structured response")
                chunks: list[str] = []
                count = 0
                thinking_seen = False
                for line in response.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    content = chunk.get("message", {}).get("content", "")
                    thinking = chunk.get("message", {}).get("thinking", "")
                    if thinking and not thinking_seen:
                        progress("Gemma thinking stream active…")
                        thinking_seen = True
                    if content:
                        chunks.append(content)
                        count += 1
                        if count == 1 or count % 25 == 0:
                            progress("Receiving redacted AI deployment plan…")
                    if chunk.get("error"):
                        raise RuntimeError(str(chunk["error"]))
                value = self._extract_json("".join(chunks))
                validation_errors = sorted(validator.iter_errors(value), key=lambda item: list(item.path))
                if validation_errors:
                    details = "; ".join(error.message for error in validation_errors[:4])
                    raise ValueError(f"Deployment plan schema validation failed: {details}")
                return value
            except Exception as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "The previous response failed JSON schema validation. Return exactly one object "
                                "matching the supplied schema; do not add fields or prose. Use short command strings "
                                "copied from the detected project specification, never source code. Validation error: "
                                + str(exc)[:1200]
                            ),
                        }
                    )
                    progress(f"Repairing invalid deployment plan ({attempt + 1}/{attempts - 1})…")
        raise RuntimeError(f"Ollama planning failed: {last_error}")

    @staticmethod
    def _extract_json(content: str) -> dict[str, Any]:
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        try:
            value = json.loads(content)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            raise ValueError("Model response contained no JSON object")
        value = json.loads(match.group(0))
        if not isinstance(value, dict):
            raise ValueError("Model response was not a JSON object")
        return value


class PlannerAgent:
    GENERATION_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "service_root",
            "package_manager",
            "install_command",
            "build_command",
            "start_command",
            "port",
            "health_path",
            "environment_contract",
            "runtime_strategy",
            "github_jobs",
            "aws_sizing",
            "required_patches",
        ],
        "properties": {
            "service_root": {"type": "string", "maxLength": 240},
            "package_manager": {"enum": ["npm", "pnpm", "yarn", "bun"]},

            "install_command": {"type": "string", "minLength": 1, "maxLength": 8192},
            "build_command": {"type": "string", "minLength": 1, "maxLength": 8192},
            "start_command": {"type": "string", "minLength": 1, "maxLength": 8192},
            "port": {"type": "integer", "minimum": 1024, "maximum": 65535},
            "health_path": {"type": "string", "pattern": "^/[A-Za-z0-9_./-]*$", "maxLength": 160},
            "environment_contract": {
                "type": "array",
                "maxItems": 40,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "secret", "scope"],
                    "properties": {
                        "name": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{0,127}$"},
                        "secret": {"type": "boolean"},
                        "scope": {"enum": ["build", "runtime", "both"]},
                    },
                },
            },
            "runtime_strategy": {"enum": ["nextjs-standalone"]},
            "github_jobs": {
                "type": "array",
                "minItems": 4,
                "maxItems": 8,
                "uniqueItems": True,
                "items": {"enum": ["validate", "build", "push", "deploy", "stabilize", "smoke-test"]},
            },
            "aws_sizing": {
                "type": "object",
                "additionalProperties": False,
                "required": ["instance_type"],
                "properties": {
                    "instance_type": {"enum": ["t3.micro", "t3.small", "t3.medium", "t4g.micro", "t4g.small"]},
                },
            },
            "required_patches": {
                "type": "array",
                "maxItems": 4,
                "uniqueItems": True,
                "items": {"enum": ["standalone-output", "health-route", "same-origin-auth", "defer-build-database"]},
            },
        },
    }
    PLAN_SCHEMA = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["risks", "recommendations", "source_patches", "generation"],
        "properties": {
            "risks": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 500}},
            "recommendations": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 500}},
            "source_patches": {
                "type": "array",
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path", "reason", "change"],
                    "properties": {
                        "path": {"type": "string", "maxLength": 240},
                        "reason": {"type": "string", "maxLength": 500},
                        "change": {"type": "string", "maxLength": 500},
                    },
                },
            },
            "generation": GENERATION_SCHEMA,
        },
    }
    BUILD_REPAIR_SCHEMA = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "actions"],
        "properties": {
            "summary": {"type": "string", "maxLength": 500},
            "actions": {
                "type": "array",
                "maxItems": 4,
                "uniqueItems": True,
                "items": {
                    "enum": [
                        "ensure-standalone-output",
                        "ensure-type-safe-health-route",
                        "same-origin-auth",
                        "normalize-alert-variant",
                        "none",
                    ]
                },
            },
        },
    }
    SYSTEM_PROMPT = """
You are a conservative DevOps deployment planner. Analyze a redacted Next.js project specification.
Return one JSON object with exactly these top-level fields:
risks (array of short strings), recommendations (array of short strings), source_patches,
and generation. generation must restate the detected service root, package manager, install/build/start
commands, port, health route, environment names without values, the nextjs-standalone runtime strategy,
required GitHub jobs, a conservative EC2 instance type, and required deployment compatibility patches.
source_patches is an array of objects containing path, reason, and change. Plan for one Next.js process managed by
systemd on a single EC2 instance behind nginx, released from S3 through SSM Run Command, with
MongoDB Atlas via AWS Secrets Manager, GitHub OIDC, CloudWatch, and CloudFormation. Never request,
repeat, infer, or output credential values. Do not split a monolith into microservices. Source patches must
be limited to deployment compatibility. JSON only.
""".strip()

    def __init__(self, client: OllamaClient | None = None, emit: Callable[..., object] | None = None):
        self.client = client or OllamaClient()
        self.emit = emit or (lambda *args, **kwargs: None)

    def plan(self, run_id: str, spec: ProjectSpec) -> DeploymentPlan:
        service = spec.services[0]
        plan = DeploymentPlan(project_slug=self._slug(spec.name), primary_service=service.name)

        plan.model = self.client.model
        self._apply_deterministic_generation(plan, service)
        self.emit(run_id, "step", "planner", "running", 18, f"Planning with {self.client.model}")
        try:
            self.client.ensure_model(
                lambda message: self.emit(run_id, "log", "planner", "running", 19, message)
            )
            result = self.client.chat_json(
                self.SYSTEM_PROMPT,
                {"project": spec.to_dict()},
                schema=self.PLAN_SCHEMA,
                attempts=2,
                progress=lambda message: self.emit(run_id, "log", "planner", "running", 23, message),
            )
            plan.risks = self._strings(result.get("risks"))
            plan.recommendations = self._strings(result.get("recommendations"))
            self._apply_generation(plan, service, result.get("generation"))
            patches = result.get("source_patches")
            if isinstance(patches, list):
                plan.source_patches = [
                    {
                        "path": str(item.get("path", ""))[:240],
                        "reason": str(item.get("reason", ""))[:500],
                        "change": str(item.get("change", ""))[:500],
                    }
                    for item in patches
                    if isinstance(item, dict)
                ][:12]
            plan.model_used = True
            self.emit(run_id, "prompt", "planner", "complete", 27, "AI deployment plan validated")
        except Exception as exc:
            plan.risks.append("Ollama planning was unavailable; deterministic safe defaults were used.")
            plan.recommendations.append("Sign in to Ollama and re-run analysis to include AI recommendations.")
            self.emit(run_id, "warning", "planner", "warning", 27, str(exc))
        if service.has_mongodb:
            plan.recommendations.append("Store MONGODB_URI only in AWS Secrets Manager and inject it at runtime.")
        if service.has_better_auth:
            plan.recommendations.append("Use same-origin Better Auth client configuration behind the ALB.")
        self.emit(run_id, "step", "planner", "complete", 29, "Deployment plan ready")
        return plan

    def repair_build(self, run_id: str, spec: ProjectSpec, plan: DeploymentPlan, build_error: str) -> list[str]:
        self.emit(run_id, "step", "repair", "running", 91, "Requesting a bounded Ollama compatibility repair")
        result = self.client.chat_json(
            """
You are diagnosing a failed Next.js production build for a reviewed deployment. Select only safe,
predefined compatibility actions from the supplied schema. A TypeScript error saying an Alert
variant such as `info` is not assignable to the project's Alert variant union may use
`normalize-alert-variant`; this action only normalizes that exact unsupported variant to the
component's default variant in the isolated staging copy. Never output source code, commands,
credentials, environment values, or arbitrary file changes. Use none when no listed action applies.
""".strip(),
            {
                "project": spec.to_dict(),
                "generation": plan.to_dict(),
                "redacted_build_error": build_error[-8000:],
            },
            schema=self.BUILD_REPAIR_SCHEMA,
            attempts=2,
            progress=lambda message: self.emit(run_id, "log", "repair", "running", 92, message),
        )
        actions = [str(item) for item in result.get("actions", []) if str(item) != "none"][:4]
        plan.repair_actions.extend(action for action in actions if action not in plan.repair_actions)
        summary = str(result.get("summary", "Compatibility repair analyzed"))[:500]

        message = (
            f"Compatibility repair selected: {', '.join(actions)}"
            if actions
            else "No safe predefined compatibility repair applies"
        )
        self.emit(run_id, "step", "repair", "complete", 93, message, {"actions": actions, "summary": summary})
        return actions

    @staticmethod
    def _apply_generation(plan: DeploymentPlan, service, value: Any) -> None:
        if not isinstance(value, dict):
            raise ValueError("Ollama generation specification is missing")

        plan.service_root = service.root
        plan.package_manager = service.package_manager
        plan.install_command = service.install_command
        plan.build_command = service.build_command
        plan.start_command = service.start_command
        plan.port = service.port
        plan.health_path = service.health_path
        plan.environment_contract = [
            {"name": item.name, "secret": item.secret, "scope": item.scope}
            for item in service.environment
        ]
        plan.runtime_strategy = "nextjs-standalone"
        allowed_jobs = ["validate", "build", "push", "deploy", "stabilize", "smoke-test"]
        requested_jobs = value.get("github_jobs") if isinstance(value.get("github_jobs"), list) else []
        plan.github_jobs = [item for item in allowed_jobs if item in requested_jobs]
        if not all(item in plan.github_jobs for item in ("build", "push", "deploy", "smoke-test")):
            plan.github_jobs = allowed_jobs
        sizing = value.get("aws_sizing") if isinstance(value.get("aws_sizing"), dict) else {}

        allowed_instances = {"t3.micro", "t3.small", "t3.medium", "t4g.micro", "t4g.small"}
        instance_type = str(sizing.get("instance_type", "t3.micro"))
        if instance_type not in allowed_instances:
            instance_type = "t3.micro"

        try:
            from deploy_agent.bridge import free_tier_only
            if free_tier_only() and instance_type not in ("t3.micro", "t4g.micro"):
                instance_type = ("t4g.micro" if instance_type.startswith("t4g")
                                 else "t3.micro")
        except Exception:                                       # noqa: BLE001
            pass
        plan.aws_sizing = {"instance_type": instance_type}
        allowed_patches = {"standalone-output", "health-route", "same-origin-auth", "defer-build-database"}
        requested_patches = value.get("required_patches") if isinstance(value.get("required_patches"), list) else []
        plan.required_patches = [str(item) for item in requested_patches if item in allowed_patches]

    @staticmethod
    def _apply_deterministic_generation(plan: DeploymentPlan, service) -> None:
        plan.service_root = service.root
        plan.package_manager = service.package_manager
        plan.install_command = service.install_command
        plan.build_command = service.build_command
        plan.start_command = service.start_command
        plan.port = service.port
        plan.health_path = service.health_path
        plan.environment_contract = [
            {"name": item.name, "secret": item.secret, "scope": item.scope}
            for item in service.environment
        ]
        plan.runtime_strategy = "nextjs-standalone"
        plan.github_jobs = ["validate", "build", "push", "deploy", "stabilize", "smoke-test"]
        plan.aws_sizing = {"instance_type": "t3.micro"}
        plan.required_patches = ["standalone-output", "health-route"]

    @staticmethod
    def _strings(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item)[:500] for item in value if isinstance(item, (str, int, float))][:20]

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
        return slug[:32] or "nextjs-app"

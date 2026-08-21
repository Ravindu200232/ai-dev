from __future__ import annotations

from .generator_shared import *
from .generator_shared import _EAGER_CONNECT, _LAZY_CONNECT, generator_class


class GeneratorCommonMixin:
    @staticmethod
    def _render_service(service: ServiceSpec, plan: DeploymentPlan) -> ServiceSpec:
        """Use the plan while discovery remains authoritative for unsafe changes."""
        return replace(
            service,
            root=plan.service_root or service.root,
            package_manager=plan.package_manager or service.package_manager,
            install_command=plan.install_command or service.install_command,
            build_command=plan.build_command or service.build_command,
            start_command=plan.start_command or service.start_command,
            port=plan.port or service.port,
            health_path=plan.health_path or service.health_path,
        )
    def repair_compatibility(
        self,
        spec: ProjectSpec,
        plan: DeploymentPlan,
        staged_root: Path,
        records: list[ArtifactRecord],
        actions: list[str],
        build_error: str,
    ) -> tuple[list[ArtifactRecord], bool]:
        """Apply bounded compatibility repairs in the review copy."""
        if not actions:
            return records, False
        service = self._render_service(spec.services[0], plan)
        record_map = {record.path: record for record in records}
        before = {path: record.sha256 for path, record in record_map.items()}
        service_root = staged_root / service.root if service.root else staged_root
        if "ensure-type-safe-health-route" in actions and (service_root / "tsconfig.json").exists():
            for app_root in (service_root / "app", service_root / "src" / "app"):
                legacy = app_root / "api" / "health" / "route.js"
                relative = legacy.relative_to(staged_root).as_posix()
                record = record_map.get(relative)
                if legacy.exists() and record and record.kind == "source-patch" and not record.original_exists:
                    legacy.unlink()
                    record_map.pop(relative, None)

        patch_records, applied = self._apply_compatibility_patches(
            spec, staged_root, service, DeploymentTarget(plan.target)
        )
        for record in patch_records:
            record_map[record.path] = record
        if "normalize-alert-variant" in actions:

            alert_pattern = re.compile(r"(<Alert\b[^>]*\bvariant\s*=\s*[\"'])info([\"'])")
            scanned = 0
            for candidate in service_root.rglob("*"):

                if scanned >= 2000:
                    break
                if not candidate.is_file() or candidate.suffix.lower() not in {".ts", ".tsx", ".js", ".jsx"}:
                    continue
                try:
                    if candidate.stat().st_size > 512_000:
                        continue
                    content = candidate.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                scanned += 1
                updated, count = alert_pattern.subn(r"\1default\2", content)
                if not count or updated == content:
                    continue
                relative = candidate.relative_to(staged_root).as_posix()
                record_map[relative] = self._write(spec, staged_root, relative, updated, "source-patch")
                applied_patch = {
                    "path": relative,
                    "reason": "Type-safe Alert variant compatibility",
                    "change": "Normalize unsupported Alert variant info to default",
                }
                if applied_patch not in applied:
                    applied.append(applied_patch)
        if applied:
            plan.source_patches.extend(item for item in applied if item not in plan.source_patches)
        changed = set(record_map) != set(before) or any(
            record.sha256 != before.get(path) for path, record in record_map.items()
        )
        if changed:
            manifest_path = staged_root / "deployment-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["generation"]["repair_actions"] = list(plan.repair_actions)
            owned = sorted(set(record_map) | {"deployment-manifest.json"})
            manifest["artifacts"] = owned
            manifest["agent_owned_files"] = owned
            record_map["deployment-manifest.json"] = self._write(
                spec,
                staged_root,
                "deployment-manifest.json",
                json.dumps(manifest, indent=2) + "\n",
                "manifest",
            )
        if not changed and "route.js" in build_error and "allowJs" in build_error:
            plan.risks.append("The bounded type-safe health repair made no change; manual application code review is required.")
        return list(record_map.values()), changed
    @staticmethod
    def diff(spec: ProjectSpec, staged_root: Path, records: list[dict]) -> str:
        source_root = Path(spec.source_path)
        chunks: list[str] = []
        for record in records:
            relative = record["path"]
            staged = staged_root / relative
            original = source_root / relative
            try:
                after = staged.read_text(encoding="utf-8").splitlines(keepends=True)
                before = original.read_text(encoding="utf-8").splitlines(keepends=True) if original.exists() else []
            except (UnicodeDecodeError, OSError):
                continue
            chunks.extend(
                difflib.unified_diff(
                    before,
                    after,
                    fromfile=f"a/{relative}",
                    tofile=f"b/{relative}",
                )
            )
        return "".join(chunks)
    def _write(self, spec: ProjectSpec, root: Path, relative: str, content: str, kind: str) -> ArtifactRecord:
        relative = relative.replace("\\", "/").lstrip("/")
        target = root / relative
        original = Path(spec.source_path) / relative
        original_exists = original.exists()
        original_hash = sha256_file(original) if original_exists and original.is_file() else ""
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        return ArtifactRecord(
            path=relative,
            kind=kind,
            sha256=sha256_file(target),
            size=target.stat().st_size,
            original_exists=original_exists,
            original_sha256=original_hash,
        )
    def _apply_compatibility_patches(
        self,
        spec: ProjectSpec,
        staged_root: Path,
        service: ServiceSpec,
        target: DeploymentTarget = DeploymentTarget.AWS_EC2,
    ) -> tuple[list[ArtifactRecord], list[dict[str, str]]]:
        records: list[ArtifactRecord] = []
        patches: list[dict[str, str]] = []
        service_root = staged_root / service.root if service.root else staged_root
        prefix = f"{service.root}/" if service.root else ""

        config = next(
            (
                service_root / name
                for name in ("next.config.mjs", "next.config.js", "next.config.cjs", "next.config.ts")
                if (service_root / name).exists()
            ),
            None,
        )

        wants_standalone = target in (DeploymentTarget.AWS_EC2, DeploymentTarget.AWS_ECS)
        if not wants_standalone:
            pass
        elif config:
            content = config.read_text(encoding="utf-8")
            updated = content
            if not re.search(r"\boutput\s*:\s*['\"]standalone['\"]", content):
                updated, count = re.subn(
                    r"const\s+nextConfig\s*=\s*\{\s*\}\s*;?",
                    'const nextConfig = {\n  output: "standalone",\n};',
                    content,
                    count=1,
                )
                if count == 0:
                    updated, count = re.subn(
                        r"(const\s+(?:nextConfig|config)\s*=\s*\{)",
                        r'\1\n  output: "standalone",',
                        content,
                        count=1,
                    )
                if count == 0:
                    updated, count = re.subn(
                        r"((?:module\.exports\s*=|export\s+default)\s*\{)",
                        r'\1\n  output: "standalone",',
                        content,
                        count=1,
                    )
                if count == 0:
                    updated = content
            if updated != content:
                relative = f"{prefix}{config.name}"
                records.append(self._write(spec, staged_root, relative, updated, "source-patch"))
                patches.append({"path": relative, "reason": "Next.js standalone build output", "change": "Add output: standalone"})
        elif wants_standalone:
            relative = f"{prefix}next.config.mjs"
            content = 'const nextConfig = {\n  output: "standalone",\n};\n\nexport default nextConfig;\n'
            records.append(self._write(spec, staged_root, relative, content, "source-patch"))
            patches.append({"path": relative, "reason": "Next.js standalone build output", "change": "Create a minimal standalone Next.js config"})

        app_root = service_root / "app"
        if not app_root.exists() and (service_root / "src" / "app").exists():
            app_root = service_root / "src" / "app"
        typescript_project = (service_root / "tsconfig.json").exists()
        suffixes = (".ts", ".tsx", ".js", ".jsx") if typescript_project else (".js", ".jsx", ".ts", ".tsx")
        health_candidates = [app_root / "api" / "health" / f"route{suffix}" for suffix in suffixes]
        health = app_root / "api" / "health" / ("route.ts" if typescript_project else "route.js")
        if app_root.exists() and not any(path.exists() for path in health_candidates):
            relative = health.relative_to(staged_root).as_posix()
            health_content = textwrap.dedent(
                """
                export const dynamic = "force-dynamic";

                export async function GET() {
                  return Response.json({ status: "ok", service: "nextjs", timestamp: new Date().toISOString() });
                }
                """
            ).lstrip()
            records.append(self._write(spec, staged_root, relative, health_content, "source-patch"))
            patches.append({"path": relative, "reason": "nginx and release health checks", "change": "Add safe GET /api/health endpoint"})
        elif not app_root.exists() and (service_root / "pages").exists():
            pages_health = service_root / "pages" / "api" / "health.js"
            pages_candidates = [pages_health.with_suffix(suffix) for suffix in (".js", ".ts", ".jsx", ".tsx")]
            if not any(path.exists() for path in pages_candidates):
                relative = pages_health.relative_to(staged_root).as_posix()
                health_content = textwrap.dedent(
                    """
                    export default function handler(_request, response) {
                      response.status(200).json({ status: "ok", service: "nextjs", timestamp: new Date().toISOString() });
                    }
                    """
                ).lstrip()
                records.append(self._write(spec, staged_root, relative, health_content, "source-patch"))
                patches.append({"path": relative, "reason": "nginx and release health checks", "change": "Add safe GET /api/health endpoint"})

        for name in ("mongodb", "db", "mongo"):
            for suffix in (".ts", ".js"):
                mongo_lib = service_root / "lib" / f"{name}{suffix}"
                if not mongo_lib.exists():
                    continue
                source = mongo_lib.read_text(encoding="utf-8")
                if "function connection()" in source:
                    continue
                updated, count = _EAGER_CONNECT.subn(_LAZY_CONNECT, source, count=1)
                if not count:
                    continue

                updated = updated.replace("await clientPromise", "await connection()")
                relative = mongo_lib.relative_to(staged_root).as_posix()
                records.append(self._write(spec, staged_root, relative, updated, "source-patch"))
                patches.append({
                    "path": relative,
                    "reason": "next build imports every module and has no database",
                    "change": "Connect to MongoDB on first use instead of at import",
                })
                break

        for suffix in (".ts", ".js", ".tsx", ".jsx"):
            auth_route = app_root / "api" / "auth" / "[...all]" / f"route{suffix}"
            if not auth_route.exists():
                continue
            source = auth_route.read_text(encoding="utf-8")
            eager = re.search(r"toNextJsHandler\s*\(\s*auth\.handler\s*\)", source)
            deferred = "await import(" in source
            if not eager or deferred:
                break
            relative = auth_route.relative_to(staged_root).as_posix()
            patched = textwrap.dedent(
                """
                import { toNextJsHandler } from 'better-auth/next-js'

                // Build auth lazily so compilation never opens MongoDB.
                let handlers
                async function ready() {
                  if (!handlers) {
                    const { auth } = await import('@/lib/auth')
                    handlers = toNextJsHandler(auth.handler)
                  }
                  return handlers
                }

                export const dynamic = 'force-dynamic'

                export async function GET(request) {
                  return (await ready()).GET(request)
                }

                export async function POST(request) {
                  return (await ready()).POST(request)
                }
                """
            ).lstrip()
            records.append(self._write(spec, staged_root, relative, patched, "source-patch"))
            patches.append({
                "path": relative,
                "reason": "next build imports route modules and has no database",
                "change": "Build the Better Auth handler on first request, not at import",
            })
            break

        auth_client = next(
            (service_root / "lib" / f"auth-client{suffix}" for suffix in (".js", ".ts", ".jsx", ".tsx") if (service_root / "lib" / f"auth-client{suffix}").exists()),
            None,
        )
        if auth_client:
            content = auth_client.read_text(encoding="utf-8")
            if "process.env.BETTER_AUTH_URL" in content:
                updated = re.sub(
                    r"createAuthClient\(\s*\{[\s\S]*?baseURL\s*:\s*process\.env\.BETTER_AUTH_URL[\s\S]*?\}\s*\)",
                    "createAuthClient()",
                    content,
                    count=1,
                )
                if updated != content:
                    relative = auth_client.relative_to(staged_root).as_posix()
                    records.append(self._write(spec, staged_root, relative, updated, "source-patch"))
                    patches.append({"path": relative, "reason": "Same-origin authentication behind nginx", "change": "Use Better Auth same-origin client defaults"})

        if app_root.exists():
            reads_db = re.compile(
                r"@/lib/mongodb|getCollection|getSessionUser|ensureSeeded|\bgetDb\b"
            )
            declares = re.compile(r"export\s+const\s+dynamic\b")
            for candidate in sorted(app_root.rglob("*")):
                if candidate.name.split(".")[0] not in ("page", "layout"):
                    continue
                if candidate.suffix not in (".js", ".jsx", ".ts", ".tsx"):
                    continue
                try:
                    content = candidate.read_text(encoding="utf-8")
                except OSError:
                    continue
                if not reads_db.search(content) or declares.search(content):
                    continue

                lines = content.splitlines()
                insert_at = 0
                for index, line in enumerate(lines):
                    if line.startswith("import ") or line.startswith("'use ") or line.startswith('"use '):
                        insert_at = index + 1
                updated = "\n".join(
                    lines[:insert_at]
                    + ["", "export const dynamic = 'force-dynamic'"]
                    + lines[insert_at:]
                ) + "\n"
                relative = candidate.relative_to(staged_root).as_posix()
                records.append(self._write(spec, staged_root, relative, updated, "source-patch"))
                patches.append({
                    "path": relative,
                    "reason": "Reads the database at request time",
                    "change": "Add export const dynamic = 'force-dynamic' so it is not prerendered",
                })
        return records, patches

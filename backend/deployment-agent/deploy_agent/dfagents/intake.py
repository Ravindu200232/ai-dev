from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Callable

from deployment_agent.config import GIT_TIMEOUT_SECONDS, SKIP_DIRS, SOURCE_EXTENSIONS
from deployment_agent.models import EnvironmentVariable, ProjectSpec, RepositorySpec, ServiceSpec
from deployment_agent.security import is_secret_name
from deployment_agent.tools import command_exists, run_command


ENV_RE = re.compile(r"process\.env(?:\.([A-Z][A-Z0-9_]+)|\[['\"]([A-Z][A-Z0-9_]+)['\"]\])")


class IntakeAgent:
    def __init__(self, emit: Callable[..., object] | None = None):
        self.emit = emit or (lambda *args, **kwargs: None)

    def stage_and_analyze(self, run_id: str, source: Path, staged_root: Path) -> ProjectSpec:
        source = source.resolve()
        if not source.is_dir():
            raise ValueError(f"Project folder does not exist: {source}")
        self.emit(run_id, "step", "intake", "running", 4, "Copying project into an isolated review workspace")
        if staged_root.exists():
            shutil.rmtree(staged_root)
        shutil.copytree(source, staged_root, ignore=self._ignore)
        repository = self._repository_spec(source)
        services = self._discover_services(staged_root)
        if not services:
            raise ValueError("No Next.js application was found. v1 requires a package.json with a Next.js dependency.")
        warnings: list[str] = []
        lock_warnings = self._ensure_lockfiles(run_id, staged_root, services)
        if any(not service.lockfile for service in services):
            services = self._discover_services(staged_root)
        warnings.extend(lock_warnings)
        if len(services) > 1:
            warnings.append("Multiple Next.js services were detected; the first service is the primary deployment target.")
        if repository.dirty_files:
            warnings.append("The source repository has uncommitted files. Only agent-owned files will be staged on deploy.")
        project_name = self._project_name(source, services[0])
        spec = ProjectSpec(
            name=project_name,
            source_path=str(source),
            staged_path=str(staged_root),
            services=services,
            repository=repository,
            warnings=warnings,
        )
        self.emit(
            run_id,
            "step",
            "intake",
            "complete",
            14,
            f"Detected {len(services)} Next.js service(s); primary root: {services[0].root or '.'}",
            {"services": len(services), "primary_root": services[0].root or "."},
        )
        return spec

    @staticmethod
    def _ignore(directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in SKIP_DIRS or name.startswith(".env")}

    def _discover_services(self, root: Path) -> list[ServiceSpec]:
        candidates: list[tuple[Path, dict]] = []
        for package_path in root.rglob("package.json"):
            if any(part in SKIP_DIRS for part in package_path.parts):
                continue
            try:
                package = json.loads(package_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
            if "next" in dependencies:
                candidates.append((package_path.parent, package))
        candidates.sort(key=lambda item: (len(item[0].relative_to(root).parts), str(item[0])))
        return [self._service_spec(root, service_root, package) for service_root, package in candidates]

    def _service_spec(self, repo_root: Path, root: Path, package: dict) -> ServiceSpec:
        relative_root = root.relative_to(repo_root).as_posix()
        if relative_root == ".":
            relative_root = ""
        dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
        scripts = package.get("scripts", {})
        package_manager, install_command, lockfile = self._package_manager(root, package)
        environment = self._scan_environment(root)
        routes = self._scan_routes(root)
        name = package.get("name") or root.name
        version = str(dependencies.get("next", "unknown"))
        return ServiceSpec(
            name=self._slug(name),
            root=relative_root,
            framework="nextjs",
            version=version,
            package_manager=package_manager,
            install_command=install_command,
            build_command=f"{package_manager} run build" if scripts.get("build") else "",
            start_command=f"{package_manager} run start" if scripts.get("start") else "node server.js",
            port=self._detect_port(scripts),
            routes=routes,
            environment=environment,
            dependencies=sorted(dependencies.keys()),
            scripts={str(key): str(value) for key, value in scripts.items()},
            lockfile=lockfile,
            has_mongodb="mongodb" in dependencies or "mongoose" in dependencies,
            has_better_auth="better-auth" in dependencies,
        )

    @staticmethod
    def _package_manager(root: Path, package: dict | None = None) -> tuple[str, str, str]:
        if (root / "pnpm-lock.yaml").exists():
            return "pnpm", "pnpm install --frozen-lockfile", "pnpm-lock.yaml"
        if (root / "yarn.lock").exists():
            return "yarn", "yarn install --frozen-lockfile", "yarn.lock"
        if (root / "bun.lockb").exists() or (root / "bun.lock").exists():
            name = "bun.lock" if (root / "bun.lock").exists() else "bun.lockb"
            return "bun", "bun install --frozen-lockfile", name
        if (root / "package-lock.json").exists():
            return "npm", "npm ci", "package-lock.json"
        declared = str((package or {}).get("packageManager", "")).split("@", 1)[0].lower()
        if declared in {"pnpm", "yarn", "bun"}:
            return declared, f"{declared} install --frozen-lockfile", ""
        return "npm", "npm ci", ""

    def _ensure_lockfiles(self, run_id: str, staged_root: Path, services: list[ServiceSpec]) -> list[str]:
        warnings: list[str] = []
        for service in services:
            if service.lockfile:
                continue
            root = staged_root / service.root if service.root else staged_root
            if service.package_manager != "npm" or not command_exists("npm"):
                warnings.append(
                    f"A reproducible {service.package_manager} lockfile is missing for {service.root or '.'}."
                )
                continue
            self.emit(run_id, "step", "lockfile", "running", 12, "Generating package-lock.json in the isolated workspace")
            result = run_command(
                ["npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"],
                cwd=root,
                timeout=600,
            )
            if result.returncode or not (root / "package-lock.json").is_file():
                warnings.append("package-lock.json generation failed; the package lock gate will block deployment.")
        return warnings

    def _scan_environment(self, root: Path) -> list[EnvironmentVariable]:
        found: dict[str, set[str]] = {}
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SOURCE_EXTENSIONS:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for match in ENV_RE.finditer(content):
                name = match.group(1) or match.group(2)
                if name:
                    found.setdefault(name, set()).add(path.relative_to(root).as_posix())
        result = []
        for name, sources in sorted(found.items()):
            scope = "build" if name.startswith("NEXT_PUBLIC_") else "runtime"
            result.append(
                EnvironmentVariable(
                    name=name,
                    required=True,
                    secret=is_secret_name(name),
                    scope=scope,
                    sources=sorted(sources),
                )
            )
        return result

    @staticmethod
    def _scan_routes(root: Path) -> list[dict[str, str]]:
        app_dir = root / "app"
        if not app_dir.exists():
            app_dir = root / "src" / "app"
        routes: list[dict[str, str]] = []
        if not app_dir.exists():
            return routes
        for path in app_dir.rglob("*"):
            if not path.is_file() or path.name not in {"page.js", "page.jsx", "page.ts", "page.tsx", "route.js", "route.ts"}:
                continue
            rel = path.parent.relative_to(app_dir)
            parts = [part for part in rel.parts if not part.startswith("(")]
            route = "/" + "/".join(parts)
            route = route.replace("/page", "") or "/"
            routes.append({"path": route, "type": "api" if path.name.startswith("route") else "page"})
        return sorted(routes, key=lambda item: item["path"])

    @staticmethod
    def _detect_port(scripts: dict) -> int:
        text = " ".join(str(value) for value in scripts.values())
        match = re.search(r"(?:--port|-p)\s+(\d{2,5})", text)
        return int(match.group(1)) if match else 3000

    @staticmethod
    def _repository_spec(source: Path) -> RepositorySpec:
        if not (source / ".git").exists() or not command_exists("git"):
            return RepositorySpec(is_git=False)
        branch = run_command(["git", "branch", "--show-current"], cwd=source).stdout.strip() or "main"
        remote_proc = run_command(["git", "remote", "get-url", "origin"], cwd=source)

        status = run_command(
            ["git", "status", "--porcelain"], cwd=source, timeout=GIT_TIMEOUT_SECONDS
        ).stdout.splitlines()
        return RepositorySpec(
            is_git=True,
            branch=branch,
            remote=remote_proc.stdout.strip() if remote_proc.returncode == 0 else "",
            dirty_files=[line[3:] if len(line) > 3 else line for line in status],
        )

    @staticmethod
    def _project_name(source: Path, service: ServiceSpec) -> str:
        return IntakeAgent._slug(source.name or service.name)

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return slug[:40] or "nextjs-app"

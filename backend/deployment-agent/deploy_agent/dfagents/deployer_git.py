from __future__ import annotations

from .deployer_shared import *
from .deployer_shared import _ACTIVE_PROJECTS, _ACTIVE_LOCK, _PERSISTED_ACTIVE_STATES, _CANCELLABLE_STATES


class DeploymentGitMixin:
    @staticmethod
    def _set_github_variables(repo: str, values: dict[str, str]) -> None:
        for name, value in values.items():
            if not value:
                raise RuntimeError(f"Bootstrap output is missing {name}")
            run_command(["gh", "variable", "set", name, "--body", value, "--repo", repo], check=True)
    def _apply_reviewed_artifacts(self, run_id: str, source: Path, staged: Path) -> list[str]:
        records = self.store.get_artifacts(run_id)
        backup_root = staged.parent / "backup"
        source_root = source.resolve()
        staged_root = staged.resolve()
        applied: list[str] = []
        for record in records:
            relative = record["path"]
            target = source / relative
            staged_file = staged / relative
            resolved_target = target.resolve()
            resolved_staged = staged_file.resolve()
            if source_root not in resolved_target.parents or staged_root not in resolved_staged.parents:
                raise RuntimeError(f"Reviewed artifact path escapes the project workspace: {relative}")
            if not staged_file.exists():
                raise RuntimeError(f"Reviewed artifact disappeared: {relative}")

            if target.exists() and sha256_file(target) == record["sha256"]:
                applied.append(relative)
                continue
            if record.get("original_exists"):
                if not target.exists() or sha256_file(target) != record.get("original_sha256"):
                    raise RuntimeError(f"Source file changed after review; re-run analysis: {relative}")
                backup = backup_root / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            elif target.exists() and sha256_file(target) != record["sha256"]:
                raise RuntimeError(f"A new conflicting file appeared after review: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staged_file, target)
            applied.append(relative)
        self.emit(run_id, "step", "apply", "complete", 58, f"Applied {len(applied)} reviewed files to the source repository")
        return applied
    def _commit_and_push(
        self,
        run_id: str,
        source: Path,
        repo: str,
        branch: str,
        applied: list[str],
        profile: TargetProfile | None = None,
    ) -> dict[str, str]:
        profile = profile or profile_for(None)
        previous_workflow_url = self._latest_workflow_url(source, repo)

        run_command(
            [
                "git", "add", "-A", "--", ".",
                ":(exclude)**/.env", ":(exclude)**/.env.local", ":(exclude)**/.env.production",
                ":(exclude)**/.env.development", ":(exclude)**/node_modules/**", ":(exclude)**/.next/**",
                ":(exclude)**/.vercel/**",
            ],
            cwd=source,
            timeout=GIT_TIMEOUT_SECONDS,
            check=True,
        )
        for relative in applied:

            if relative.endswith(".env.example"):
                run_command(["git", "add", "-f", "--", relative], cwd=source, timeout=GIT_TIMEOUT_SECONDS, check=True)
        staged_names = run_command(
            ["git", "diff", "--cached", "--name-only"], cwd=source, timeout=GIT_TIMEOUT_SECONDS, check=True
        ).stdout.splitlines()
        secret_files = [
            name for name in staged_names
            if Path(name).name.startswith(".env") and Path(name).name != ".env.example"
        ]
        if secret_files:
            raise RuntimeError("Refusing to commit environment value files: " + ", ".join(secret_files[:10]))

        if staged_names:
            commit = run_command(
                ["git", "commit", "-m", profile.commit_subject.format(run=run_id[:8])],
                cwd=source,
                timeout=GIT_TIMEOUT_SECONDS,
            )
            if commit.returncode != 0:
                raise RuntimeError(commit.stderr or commit.stdout)

        head_sha = run_command(["git", "rev-parse", "HEAD"], cwd=source).stdout.strip()
        push = run_command(
            ["git", *github_credential_args(), "push", "origin", f"HEAD:{branch}"],
            cwd=source,
            timeout=GIT_TIMEOUT_SECONDS,
        )
        if push.returncode == 0:
            if not staged_names:

                run_command(
                    ["gh", "workflow", "run", "deploy.yml", "--repo", repo, "--ref", branch],
                    cwd=source,
                    timeout=60,
                    check=True,
                )
            self.emit(run_id, "step", "github", "complete", 68, f"Pushed reviewed deployment commit to {repo}:{branch}")
            return {
                "mode": "direct",
                "branch": branch,
                "previous_workflow_url": previous_workflow_url,
                "head_sha": head_sha,
            }
        fallback = f"deployment-agent/{run_id[:8]}"
        run_command(
            ["git", *github_credential_args(), "push", "origin", f"HEAD:{fallback}"],
            cwd=source,
            timeout=GIT_TIMEOUT_SECONDS,
            check=True,
        )
        pr = run_command(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                repo,
                "--base",
                branch,
                "--head",
                fallback,
                "--title",
                profile.pr_title,
                "--body",
                f"Generated and reviewed by Deployment Agent run `{run_id}`.",
            ],
            cwd=source,
            timeout=120,
            check=True,
        )
        return {
            "mode": "pull_request",
            "branch": fallback,
            "url": pr.stdout.strip(),
            "head_sha": head_sha,
        }
    @staticmethod
    def _latest_workflow_url(source: Path, repo: str) -> str:
        result = run_command(
            [
                "gh", "run", "list", "--repo", repo, "--workflow", "deploy.yml",
                "--limit", "1", "--json", "url",
            ],
            cwd=source,
            timeout=30,
        )
        if result.returncode:
            return ""
        try:
            runs = json.loads(result.stdout or "[]")
            return str(runs[0].get("url", "")) if runs else ""
        except (json.JSONDecodeError, IndexError, AttributeError):
            return ""
    def _wait_for_workflow(
        self,
        run_id: str,
        source: Path,
        repo: str,
        timeout: int = 900,
        previous_url: str = "",
        head_sha: str = "",
    ) -> str:
        deadline = time.time() + timeout
        seen = False
        last_reported = ""
        while time.time() < deadline:
            result = run_command(
                [
                    "gh",
                    "run",
                    "list",
                    "--repo",
                    repo,
                    "--workflow",
                    "deploy.yml",
                    "--limit",
                    "10",
                    "--json",
                    "status,conclusion,url,displayTitle,createdAt,headSha",
                ],
                cwd=source,
                timeout=30,
            )
            if result.returncode == 0:
                runs = json.loads(result.stdout or "[]")
                current = select_workflow_run(runs, head_sha, previous_url)
                if current:
                    seen = True
                    signature = f"{current.get('status', '')}:{current.get('conclusion', '')}"
                    if signature != last_reported:
                        last_reported = signature
                        self.emit(
                            run_id,
                            "monitor",
                            "github",
                            current.get("status", "running"),
                            76,
                            f"GitHub Actions: {current.get('displayTitle', 'deployment')}",
                            current,
                        )
                    if current.get("status") == "completed":
                        return current.get("conclusion") or "unknown"
            time.sleep(10 if seen else 5)
        return "running" if seen else "not_found"

from __future__ import annotations

from .deployer_shared import *
from .deployer_shared import _ACTIVE_PROJECTS, _ACTIVE_LOCK, _PERSISTED_ACTIVE_STATES, _CANCELLABLE_STATES


class DeploymentCoreMixin:
    def _validate_request(self, run_id: str, mongodb_uri: str, approved: bool) -> tuple[dict[str, Any], str]:
        run = self.store.get_run(run_id)
        if not run:
            raise ValueError("Run not found")
        if not approved:
            raise ValueError("The reviewed deployment must be explicitly approved")
        if run["state"] not in {RunState.REVIEW_READY.value, RunState.FAILED.value}:
            raise ValueError(f"Run cannot be deployed from state {run['state']}")
        plan = run.get("plan") or {}
        if plan.get("model_used") is not True:
            raise ValueError("A validated Ollama deployment plan is required before deployment")
        readiness = run.get("readiness") or {}
        gates = readiness.get("gates") or {}
        build_score = readiness.get("categories", {}).get("build")
        if gates.get("build_validation") is not True or build_score is None or int(build_score) < 15:
            raise ValueError("Local build validation must pass before deployment")
        if gates.get("security_validation") is not True or gates.get("artifacts_valid") is not True:
            raise ValueError("Artifact and security validation must pass before deployment")
        manifest_path = Path(run["staged_path"]) / "deployment-manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Reviewed deployment manifest is missing or invalid; re-run analysis") from exc
        if manifest.get("version") != ARTIFACT_SCHEMA_VERSION:
            raise ValueError("Reviewed artifacts were produced by an older renderer; re-run analysis before deployment")
        if manifest.get("model_used") is not True:
            raise ValueError("Reviewed artifacts were not generated from a validated Ollama plan")
        generation = manifest.get("generation") or {}
        expected_strategy = profile_for(target_of(run)).runtime_strategy
        if generation.get("runtime_strategy") != expected_strategy:
            raise ValueError("Reviewed generation specification is missing or invalid; re-run analysis")
        records = self.store.get_artifacts(run_id)
        owned = set(manifest.get("agent_owned_files") or [])
        if not records or {record["path"] for record in records} != owned:
            raise ValueError("Reviewed artifact manifest does not match persisted run state; re-run analysis")
        staged_root = Path(run["staged_path"]).resolve()
        for record in records:
            path = (staged_root / record["path"]).resolve()
            if staged_root not in path.parents or not path.is_file() or sha256_file(path) != record.get("sha256"):
                raise ValueError(f"Reviewed artifact changed after validation; re-run analysis: {record['path']}")
        self._validate_mongodb_uri(mongodb_uri)
        project_key = str(Path(run["project_path"]).resolve()).lower()
        return run, project_key
    @staticmethod
    def _validate_mongodb_uri(value: str) -> None:
        if not value or len(value) > 8192 or any(character.isspace() for character in value):
            raise ValueError("MONGODB_URI is missing or malformed")
        try:
            parsed = urlsplit(value)
        except ValueError as exc:
            raise ValueError("MONGODB_URI is malformed") from exc
        if parsed.scheme not in {"mongodb", "mongodb+srv"} or not parsed.netloc or not parsed.hostname:
            raise ValueError("MONGODB_URI must contain a MongoDB host and use mongodb:// or mongodb+srv://")
    def _reserve_project(self, run_id: str, project_key: str) -> None:
        with _ACTIVE_LOCK:
            if project_key in _ACTIVE_PROJECTS:
                raise DeploymentConflictError("Another production deployment is already active for this project")
            for existing in self.store.list_runs(limit=250):
                if existing["id"] == run_id or existing["state"] not in _PERSISTED_ACTIVE_STATES:
                    continue
                existing_key = str(Path(existing["project_path"]).resolve()).lower()
                if existing_key == project_key:
                    raise DeploymentConflictError(
                        f"Run {existing['id'][:12]} already owns the active production deployment for this project"
                    )
            _ACTIVE_PROJECTS.add(project_key)
    def _deploy_reserved(
        self,
        run_id: str,
        aws_profile: str,
        region: str,
        mongodb_uri: str,
        project_key: str,
        credential_reference: str = "",
        vercel_token: str = "",
    ) -> None:
        try:
            self._deploy(run_id, aws_profile, region, mongodb_uri, credential_reference, vercel_token)
        except Exception as exc:

            current = (self.store.get_run(run_id) or {}).get("state", "")
            if current == RunState.CANCELLED.value:
                self.emit(run_id, "step", "cancel", "complete", 100,
                          "Deployment worker stopped after the run was cancelled")
            else:
                self.store.transition_run(run_id, RunState.FAILED, error=str(exc))
                self.emit(run_id, "error", "deploy", "failed", 100, str(exc))
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE_PROJECTS.discard(project_key)
    def _deploy(
        self,
        run_id: str,
        profile: str,
        region: str,
        mongodb_uri: str,
        credential_reference: str = "",
        vercel_token: str = "",
    ) -> None:
        run = self.store.get_run(run_id)
        assert run
        spec = run["spec"]
        plan = run["plan"]
        source = Path(run["project_path"])
        staged = Path(run["staged_path"])
        slug = plan["project_slug"]
        branch = spec.get("repository", {}).get("branch") or "main"

        target_profile = profile_for(target_of(run))
        self._require_tools(target_profile)
        self.store.transition_run(run_id, RunState.BOOTSTRAPPING, error="")
        self.emit(run_id, "step", "bootstrap", "running", 5, "Validating provider identity and GitHub repository")

        repo = self._ensure_github_repository(source, slug)
        github_identity = self._github_repository_identity(repo)
        branch = str(github_identity.get("default_branch") or self._github_default_branch(repo, branch))
        oidc_subjects = self._github_oidc_subjects(repo, branch, github_identity)

        if target_profile.target is DeploymentTarget.VERCEL:
            prep = self._prepare_vercel(run_id, run, spec, plan, staged, mongodb_uri, vercel_token)
        elif target_profile.target is DeploymentTarget.AWS_ECS:
            prep = self._prepare_ecs(
                run_id, run, spec, plan, staged, profile, region, mongodb_uri, credential_reference, oidc_subjects
            )
        else:
            prep = self._prepare_aws(
                run_id, run, spec, plan, staged, profile, region, mongodb_uri, credential_reference, oidc_subjects
            )

        self._set_github_variables(repo, prep.github_variables)
        self._set_github_secrets(repo, prep.github_secrets)
        applied = self._apply_reviewed_artifacts(run_id, source, staged)
        push = self._commit_and_push(run_id, source, repo, branch, applied, target_profile)
        repo_state = {
            "repository": repo,
            "branch": branch,
            "push": push,
            "oidc_subjects": oidc_subjects,
            "deployed_at": datetime.now(timezone.utc).isoformat(),
            **prep.repo_state,
        }
        self.store.transition_run(run_id, RunState.CI_RUNNING, repo_json=repo_state)
        if push.get("mode") == "pull_request":
            self.emit(
                run_id,
                "step",
                "github",
                "waiting",
                70,
                "Branch protection requires the generated pull request to be merged before deployment",
                {"pull_request": push.get("url", "")},
            )
            return
        self.store.transition_run(run_id, RunState.DEPLOYING)
        self.emit(run_id, "step", "github", "running", 70, "GitHub Actions deployment triggered")
        conclusion = self._wait_for_workflow(
            run_id,
            source,
            repo,
            previous_url=push.get("previous_workflow_url", ""),
            head_sha=push.get("head_sha", ""),
        )
        if conclusion == "success":
            self.store.transition_run(run_id, RunState.VALIDATING)
            # Complete the step that triggered the workflow.
            self.emit(run_id, "step", "github", "complete", 92,
                      "GitHub Actions workflow completed")
            self.emit(run_id, "step", "deploy", "complete", 93, "GitHub Actions completed; validating the live service")
        elif conclusion in {"failure", "cancelled", "timed_out", "action_required"}:
            raise RuntimeError(f"GitHub deployment workflow concluded with {conclusion}")
        else:
            self.emit(run_id, "warning", "github", "waiting", 78, "Workflow is still running; monitoring will continue from the dashboard")

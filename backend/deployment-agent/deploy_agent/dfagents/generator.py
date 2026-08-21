from __future__ import annotations

from .generator_shared import *
from .generator_common import GeneratorCommonMixin
from .generator_runtime import GeneratorRuntimeMixin
from .generator_workflows import GeneratorWorkflowMixin
from .generator_ecs import GeneratorEcsMixin
from .generator_ec2 import GeneratorEc2Mixin
from .generator_review import GeneratorReviewMixin


class ArtifactGeneratorAgent(GeneratorCommonMixin, GeneratorRuntimeMixin, GeneratorWorkflowMixin, GeneratorEcsMixin, GeneratorEc2Mixin, GeneratorReviewMixin):
    def __init__(self, emit: Callable[..., object] | None = None):
        self.emit = emit or (lambda *args, **kwargs: None)
    def generate(
        self,
        run_id: str,
        spec: ProjectSpec,
        plan: DeploymentPlan,
        staged_root: Path,
        target: DeploymentTarget = DeploymentTarget.AWS_EC2,
        contract: EnvironmentContract | None = None,
    ) -> list[ArtifactRecord]:
        service = self._render_service(spec.services[0], plan)
        contract = contract or EnvironmentContractResolver.discover(
            service,
            staged_root / service.root if service.root else staged_root,
        )
        plan.target = target.value

        plan.runtime_strategy = profile_for(target).runtime_strategy
        plan.environment = contract.to_dict()
        self.emit(run_id, "step", "runtime", "running", 32, "Generating runtime assets")
        records: list[ArtifactRecord] = []
        prefix = f"{service.root}/" if service.root else ""
        records.append(
            self._write(spec, staged_root, f"{prefix}.env.example", self._env_example(service, contract), "environment")
        )
        self.emit(run_id, "step", "runtime", "complete", 44, "Runtime assets generated")

        patch_records, applied_patches = self._apply_compatibility_patches(spec, staged_root, service, target)
        records.extend(patch_records)
        plan.source_patches.extend(applied_patches)

        self.emit(run_id, "step", "cicd", "running", 48, "Generating GitHub Actions workflows")
        records.append(
            self._write(spec, staged_root, ".github/workflows/ci.yml", self._ci_workflow(service, spec, target), "cicd")
        )

        if target == DeploymentTarget.AWS_EC2:
            deploy_workflow = self._deploy_workflow(service, spec, plan)
        elif target == DeploymentTarget.AWS_ECS:
            deploy_workflow = self._ecs_deploy_workflow(service, spec, plan)
        else:
            deploy_workflow = self._vercel_deploy_workflow(service, spec, plan)
        records.append(
            self._write(spec, staged_root, ".github/workflows/deploy.yml", deploy_workflow, "cicd")
        )
        self.emit(run_id, "step", "cicd", "complete", 57, "CI/CD workflows generated")

        if target == DeploymentTarget.AWS_EC2:

            self.emit(run_id, "step", "aws", "running", 60, "Generating AWS EC2 artifacts")
            records.extend(
                [
                    self._write(spec, staged_root, "infra/bootstrap.yml", self._bootstrap_template(service, plan), "aws"),
                    self._write(
                        spec,
                        staged_root,
                        "infra/parameters.example.json",
                        json.dumps(self._parameters_example(spec, plan, service), indent=2) + "\n",
                        "aws",
                    ),
                    self._write(spec, staged_root, "deploy/release.sh", self._release_script(service, contract), "aws"),
                ]
            )
            self.emit(run_id, "step", "aws", "complete", 70, "AWS EC2 artifacts generated")
        elif target == DeploymentTarget.AWS_ECS:

            self.emit(run_id, "step", "aws", "running", 60, "Generating AWS ECS artifacts")
            records.extend(
                [
                    self._write(spec, staged_root, "infra/bootstrap.yml",
                                self._ecs_bootstrap_template(service, plan), "aws"),
                    self._write(
                        spec,
                        staged_root,
                        "infra/parameters.example.json",
                        json.dumps(self._parameters_example(spec, plan, service), indent=2) + "\n",
                        "aws",
                    ),

                    self._write(spec, staged_root, f"{prefix}Dockerfile",
                                self._dockerfile(service), "aws"),
                    self._write(spec, staged_root, f"{prefix}.dockerignore",
                                self._dockerignore(), "aws"),
                    self._write(
                        spec,
                        staged_root,
                        "deploy/task-definition.json",
                        json.dumps(self._task_definition(service, plan, contract), indent=2) + "\n",
                        "aws",
                    ),
                ]
            )
            self.emit(run_id, "step", "aws", "complete", 70, "AWS ECS artifacts generated")
        else:
            self.emit(run_id, "step", "provider", "running", 60, "Generating Vercel artifacts")
            records.append(
                self._write(
                    spec,
                    staged_root,
                    "deploy/vercel-environment.json",
                    json.dumps(self._vercel_environment(contract), indent=2) + "\n",
                    "vercel",
                )
            )

            config_path = f"{prefix}vercel.json"
            if (staged_root / config_path).is_file():
                plan.recommendations.append(
                    f"Kept your existing {config_path}; the agent did not overwrite it."
                )
            else:
                records.append(
                    self._write(spec, staged_root, config_path, self._vercel_config(service), "vercel")
                )
            self.emit(run_id, "step", "provider", "complete", 70, "Vercel artifacts generated")

        readiness = self._initial_readiness(records, target)
        report = self._report(spec, plan, readiness, target)
        records.append(self._write(spec, staged_root, "deployment-report.md", report, "report"))
        records.append(
            self._write(
                spec,
                staged_root,
                "readiness-score.json",
                json.dumps(readiness, indent=2) + "\n",
                "report",
            )
        )
        manifest = {
            "version": ARTIFACT_SCHEMA_VERSION,
            "project": spec.name,
            "project_slug": plan.project_slug,
            "primary_service": service.name,
            "service_root": service.root,
            "model": plan.model,
            "model_used": plan.model_used,
            "target": target.value,
            "environment_contract": contract.to_dict(),
            "generation": {
                "service_root": plan.service_root,
                "package_manager": plan.package_manager,
                "install_command": plan.install_command,
                "build_command": plan.build_command,
                "start_command": plan.start_command,
                "port": plan.port,
                "health_path": plan.health_path,
                "environment_contract": plan.environment_contract,
                "runtime_strategy": plan.runtime_strategy,
                "github_jobs": plan.github_jobs,
                "aws_sizing": plan.aws_sizing,
                "required_patches": plan.required_patches,
            },
            "artifacts": [record.path for record in records] + ["deployment-manifest.json"],
            "agent_owned_files": [record.path for record in records] + ["deployment-manifest.json"],
        }
        records.append(
            self._write(
                spec,
                staged_root,
                "deployment-manifest.json",
                json.dumps(manifest, indent=2) + "\n",
                "manifest",
            )
        )
        self.emit(run_id, "step", "generator", "complete", 76, f"Generated {len(records)} reviewed artifacts")
        return records

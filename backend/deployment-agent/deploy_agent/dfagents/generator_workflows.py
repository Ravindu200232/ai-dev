from __future__ import annotations

from .generator_shared import *
from .generator_shared import _EAGER_CONNECT, _LAZY_CONNECT, generator_class


class GeneratorWorkflowMixin:
    @staticmethod
    def _toolchain(service: ServiceSpec) -> dict[str, str]:
        """Package-manager specific workflow fragments shared by both workflows."""
        lock_name = {
            "npm": "package-lock.json",
            "pnpm": "pnpm-lock.yaml",
            "yarn": "yarn.lock",
            "bun": "bun.lock",
        }.get(service.package_manager, "package-lock.json")
        dependency = f"{service.root}/{lock_name}" if service.root else lock_name
        preinstall = "corepack enable\n" if service.package_manager in {"pnpm", "yarn"} else ""
        if service.package_manager == "bun":
            setup_step = "      - uses: oven-sh/setup-bun@v2"
            audit_command = "bun audit"
        else:
            cache_options = ""
            if not (service.package_manager == "npm" and service.install_command == "npm install"):
                cache_options = (
                    f"          cache: {service.package_manager}\n"
                    f"          cache-dependency-path: {dependency}"
                )
            setup_step = (
                "      - uses: actions/setup-node@v4\n"
                "        with:\n"
                "          node-version: 20"
                + ("\n" + cache_options if cache_options else "")
            )
            audit_command = {
                "pnpm": "pnpm audit --prod --audit-level high",
                "yarn": "yarn audit --groups dependencies --level high",
            }.get(service.package_manager, "npm audit --omit=dev --audit-level=high")
        return {
            "dependency": dependency,
            "preinstall": preinstall,
            "setup_step": setup_step,
            "audit_command": audit_command,
        }
    @staticmethod
    def _ci_workflow(
        service: ServiceSpec,
        spec: ProjectSpec,
        target: DeploymentTarget = DeploymentTarget.AWS_EC2,
    ) -> str:
        root = service.root or "."
        toolchain = generator_class()._toolchain(service)
        dependency = toolchain["dependency"]
        preinstall = toolchain["preinstall"]
        setup_step = toolchain["setup_step"]
        audit_command = toolchain["audit_command"]
        install = service.install_command
        build_env = generator_class()._build_env_yaml(service, 22)
        build_services = generator_class()._build_services_yaml(16)
        paths = f"      - '{service.root}/**'\n" if service.root else "      - '**'\n"
        workflow = textwrap.dedent(
            f"""
            name: CI

            on:
              pull_request:
              push:
                branches: [{spec.repository.branch or 'main'}]
                paths:
            {paths.rstrip()}

            permissions:
              contents: read

            jobs:
              validate:
                runs-on: ubuntu-latest
                {build_services}
                defaults:
                  run:
                    working-directory: {root}
                steps:
                  - uses: actions/checkout@v4
            __SETUP_STEP__
                  - name: Install dependencies
                    run: |
                      {preinstall.rstrip() if preinstall else '# package manager is ready'}
                      {install}
                  - name: Build application
                    env:
                      {build_env}
                    run: {service.build_command or 'npm run build'}
                  - name: Dependency audit
                    if: ${{{{ always() && hashFiles('{dependency}') != '' }}}}
                    continue-on-error: true
                    run: {audit_command}
            __STANDALONE_CHECK__
            """
        ).lstrip()

        standalone_check = (
            "      - name: Verify standalone output\n        run: test -f .next/standalone/server.js\n"
            if target == DeploymentTarget.AWS_EC2
            else ""
        )
        workflow = workflow.replace("__STANDALONE_CHECK__\n", standalone_check)
        return workflow.replace("__SETUP_STEP__", setup_step)
    @staticmethod
    def _ecs_deploy_workflow(service: ServiceSpec, spec: ProjectSpec, plan: DeploymentPlan) -> str:
        """Build the image on a runner, push it to ECR, roll the ECS service."""
        root = service.root or "."
        build_services = generator_class()._build_services_yaml(16)
        branch = spec.repository.branch or "main"
        trigger_branches = ", ".join(dict.fromkeys([branch, "main", "master"]))
        return textwrap.dedent(
            f"""
            name: Deploy to AWS ECS

            on:
              push:
                branches: [{trigger_branches}]
              workflow_dispatch:

            permissions:
              contents: read
              id-token: write

            concurrency:
              group: production-{plan.project_slug}
              cancel-in-progress: false

            jobs:
              deploy:
                runs-on: ubuntu-latest
                {build_services}
                steps:
                  - uses: actions/checkout@v4
                  - uses: aws-actions/configure-aws-credentials@v4
                    with:
                      role-to-assume: ${{{{ vars.AWS_DEPLOY_ROLE_ARN }}}}
                      aws-region: ${{{{ vars.AWS_REGION }}}}
                  - id: ecr
                    uses: aws-actions/amazon-ecr-login@v2
                  - name: Build and push the image
                    # Tagged with the commit, never :latest. Rollback and the
                    # readiness commit-match both read this tag.
                    run: |
                      IMAGE="${{{{ vars.ECR_REPOSITORY_URI }}}}:${{{{ github.sha }}}}"
                      # Let Next reach MongoDB on the runner.
                      docker build --network=host -t "$IMAGE" {root}
                      docker push "$IMAGE"
                      echo "IMAGE=$IMAGE" >> "$GITHUB_ENV"
                  - name: Render the task definition
                    run: |
                      python3 - <<'PY'
                      import json, os
                      spec = json.load(open("deploy/task-definition.json"))
                      spec["executionRoleArn"] = os.environ["EXECUTION_ROLE_ARN"]
                      spec["taskRoleArn"] = os.environ["TASK_ROLE_ARN"]
                      container = spec["containerDefinitions"][0]
                      container["image"] = os.environ["IMAGE"]
                      for item in container.get("secrets", []):
                          item["valueFrom"] = item["valueFrom"].replace(
                              "__RUNTIME_SECRET_ARN__", os.environ["RUNTIME_SECRET_ID"])
                      container["logConfiguration"]["options"]["awslogs-region"] = \\
                          os.environ["AWS_REGION"]
                      json.dump(spec, open("task-definition.rendered.json", "w"), indent=2)
                      PY
                    env:
                      EXECUTION_ROLE_ARN: ${{{{ vars.ECS_EXECUTION_ROLE_ARN }}}}
                      TASK_ROLE_ARN: ${{{{ vars.ECS_TASK_ROLE_ARN }}}}
                      RUNTIME_SECRET_ID: ${{{{ vars.RUNTIME_SECRET_ID }}}}
                      AWS_REGION: ${{{{ vars.AWS_REGION }}}}
                  - id: register
                    name: Register the revision
                    run: |
                      ARN="$(aws ecs register-task-definition \\
                        --cli-input-json file://task-definition.rendered.json \\
                        --query 'taskDefinition.taskDefinitionArn' --output text)"
                      echo "Registered $ARN"
                      echo "arn=$ARN" >> "$GITHUB_OUTPUT"
                  - name: Roll the service
                    run: |
                      # --desired-count 1 as well as the new revision.
                      aws ecs update-service \\
                        --cluster "${{{{ vars.ECS_CLUSTER }}}}" \\
                        --service "${{{{ vars.ECS_SERVICE }}}}" \\
                        --task-definition "${{{{ steps.register.outputs.arn }}}}" \\
                        --desired-count 1 \\
                        --no-cli-pager
                      # Wait for healthy tasks and drain old ones.
                      aws ecs wait services-stable \\
                        --cluster "${{{{ vars.ECS_CLUSTER }}}}" \\
                        --services "${{{{ vars.ECS_SERVICE }}}}"
                  - name: Smoke test
                    run: |
                      URL="$(aws cloudformation describe-stacks \\
                        --stack-name "${{{{ vars.PROJECT_SLUG }}}}-bootstrap" \\
                        --query "Stacks[0].Outputs[?OutputKey=='ApplicationUrl'].OutputValue" \\
                        --output text)"
                      echo "Checking $URL"
                      curl --fail --retry 12 --retry-delay 5 "$URL{service.health_path or '/api/health'}"
            """
        ).lstrip()
    @staticmethod
    def _deploy_workflow(service: ServiceSpec, spec: ProjectSpec, plan: DeploymentPlan) -> str:
        root = service.root or "."
        toolchain = generator_class()._toolchain(service)
        preinstall = toolchain["preinstall"]

        branch = spec.repository.branch or "main"
        trigger_branches = ", ".join(dict.fromkeys([branch, "main", "master"]))
        build_env = generator_class()._build_env_yaml(service, 22)
        build_services = generator_class()._build_services_yaml(16)
        return textwrap.dedent(
            f"""
            name: Deploy to AWS EC2

            on:
              push:
                branches: [{trigger_branches}]
              workflow_dispatch:

            permissions:
              contents: read
              id-token: write

            concurrency:
              group: production-{plan.project_slug}
              cancel-in-progress: false

            jobs:
              deploy:
                runs-on: ubuntu-latest
                {build_services}
                defaults:
                  run:
                    working-directory: {root}
                steps:
                  - uses: actions/checkout@v4
            __SETUP_STEP__
                  - name: Install dependencies
                    run: |
                      {preinstall.rstrip() if preinstall else '# package manager is ready'}
                      {service.install_command}
                  - name: Build application
                    env:
                      {build_env}
                    run: {service.build_command or 'npm run build'}
                  - name: Package standalone release
                    run: |
                      test -f .next/standalone/server.js
                      # Next.js emits static assets and public/ outside the
                      # standalone tree; the runtime expects them alongside it.
                      mkdir -p .next/standalone/.next
                      cp -r .next/static .next/standalone/.next/static
                      if [ -d public ]; then cp -r public .next/standalone/public; fi
                      tar -czf /tmp/app.tar.gz -C .next/standalone .
                  - uses: aws-actions/configure-aws-credentials@v4
                    with:
                      role-to-assume: ${{{{ vars.AWS_DEPLOY_ROLE_ARN }}}}
                      aws-region: ${{{{ vars.AWS_REGION }}}}
                  - name: Upload release to S3
                    run: |
                      aws s3 cp /tmp/app.tar.gz \\
                        "s3://${{{{ vars.ARTIFACT_BUCKET }}}}/releases/${{{{ github.sha }}}}/app.tar.gz"
                      aws s3 cp deploy/release.sh \\
                        "s3://${{{{ vars.ARTIFACT_BUCKET }}}}/releases/${{{{ github.sha }}}}/release.sh"
                    working-directory: .
                  - name: Release via SSM
                    run: |
                      COMMAND_ID=$(aws ssm send-command \\
                        --instance-ids "${{{{ vars.INSTANCE_ID }}}}" \\
                        --document-name AWS-RunShellScript \\
                        --comment "Deploy ${{{{ github.sha }}}}" \\
                        --timeout-seconds 600 \\
                        --parameters commands='[
                          "set -euo pipefail",
                          "aws s3 cp s3://${{{{ vars.ARTIFACT_BUCKET }}}}/releases/${{{{ github.sha }}}}/release.sh /tmp/release-${{{{ github.sha }}}}.sh",
                          "chmod +x /tmp/release-${{{{ github.sha }}}}.sh",
                          "BUCKET=${{{{ vars.ARTIFACT_BUCKET }}}} SHA=${{{{ github.sha }}}} SECRET_ID=${{{{ vars.RUNTIME_SECRET_ID }}}} /tmp/release-${{{{ github.sha }}}}.sh"
                        ]' \\
                        --query Command.CommandId --output text)
                      echo "SSM command $COMMAND_ID dispatched"
                      # SSM needs a short polling delay after dispatch.
                      STATUS=Pending
                      for _ in $(seq 1 60); do
                        sleep 5
                        STATUS=$(aws ssm get-command-invocation \\
                          --command-id "$COMMAND_ID" \\
                          --instance-id "${{{{ vars.INSTANCE_ID }}}}" \\
                          --query Status --output text 2>/dev/null || echo Pending)
                        case "$STATUS" in
                          Success|Failed|Cancelled|TimedOut) break ;;
                        esac
                      done
                      aws ssm get-command-invocation \\
                        --command-id "$COMMAND_ID" \\
                        --instance-id "${{{{ vars.INSTANCE_ID }}}}" \\
                        --query 'StandardOutputContent' --output text || true
                      if [ "$STATUS" != "Success" ]; then
                        echo "::error::Release finished with status $STATUS"
                        aws ssm get-command-invocation \\
                          --command-id "$COMMAND_ID" \\
                          --instance-id "${{{{ vars.INSTANCE_ID }}}}" \\
                          --query 'StandardErrorContent' --output text || true
                        exit 1
                      fi
                    working-directory: .
                  - name: Smoke test
                    run: |
                      BASE_URL=$(aws cloudformation describe-stacks \\
                        --stack-name "${{{{ vars.PROJECT_SLUG }}}}-bootstrap" \\
                        --query "Stacks[0].Outputs[?OutputKey=='ApplicationUrl'].OutputValue" \\
                        --output text)
                      curl --fail --retry 12 --retry-delay 10 "$BASE_URL{service.health_path}"
                    working-directory: .
            """
        ).lstrip().replace("__SETUP_STEP__", toolchain["setup_step"])

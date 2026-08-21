from __future__ import annotations

from .generator_shared import *
from .generator_shared import _EAGER_CONNECT, _LAZY_CONNECT, generator_class


class GeneratorRuntimeMixin:
    @staticmethod
    def _build_environment(service: ServiceSpec) -> list[tuple[str, str]]:
        """Placeholders every build site must set, in one place."""
        env = [("MONGODB_URI", BUILD_PLACEHOLDER_MONGODB_URI)]
        if service.has_better_auth:
            env.append(("BETTER_AUTH_SECRET", BUILD_PLACEHOLDER_AUTH_SECRET))
            env.append(("BETTER_AUTH_URL", BUILD_PLACEHOLDER_AUTH_URL))
        return env
    @staticmethod
    def _build_env_yaml(service: ServiceSpec, indent: int) -> str:
        """The build placeholders as YAML `env:` entries, already indented."""
        pad = " " * indent
        return f"\n{pad}".join(
            f"{name}: {value}"
            for name, value in generator_class()._build_environment(service))
    @staticmethod
    def _build_env_docker(service: ServiceSpec, indent: int) -> str:
        """The same placeholders as Dockerfile `ENV` instructions."""
        pad = " " * indent
        return f"\n{pad}".join(
            f"ENV {name}={value}"
            for name, value in generator_class()._build_environment(service))
    @staticmethod
    def _build_services_yaml(indent: int) -> str:
        """A real MongoDB on 127.0.0.1:27017 for the duration of the build job."""
        pad = " " * indent
        lines = [
            "services:",
            "  mongodb:",
            "    image: mongo:7",
            "    ports:",
            "      - 27017:27017",
            "    options: >-",
            '      --health-cmd "mongosh --quiet --eval \'db.runCommand({ping:1})\'"',
            "      --health-interval 10s",
            "      --health-timeout 5s",
            "      --health-retries 10",
        ]
        return f"\n{pad}".join(lines)
    @staticmethod
    def _dockerfile(service: ServiceSpec) -> str:
        """The image, built in GitHub Actions and never on the user's machine."""
        port = service.port or 3000
        install = service.install_command or "npm ci"
        build = service.build_command or "npm run build"
        build_env = generator_class()._build_env_docker(service, 12)
        return textwrap.dedent(
            f"""
            # syntax=docker/dockerfile:1 Written by the deployment agent.

            FROM node:20-alpine AS deps
            WORKDIR /app
            COPY package.json package-lock.json* ./
            RUN {install}

            FROM node:20-alpine AS build
            WORKDIR /app
            COPY --from=deps /app/node_modules ./node_modules
            COPY . .
            # Runtime values come from Secrets Manager.
            # These placeholders only keep the build parseable.
            {build_env}
            ENV NEXT_TELEMETRY_DISABLED=1
            RUN {build}
            # Both must exist before the runtime stage copies them.
            RUN test -f .next/standalone/server.js && mkdir -p public .next/static

            FROM node:20-alpine AS runtime
            WORKDIR /app
            ENV NODE_ENV=production
            ENV NEXT_TELEMETRY_DISABLED=1
            ENV PORT={port}
            ENV HOSTNAME=0.0.0.0

            # Not root. A container that is compromised should not also be
            # privileged inside its own filesystem.
            RUN addgroup --system --gid 1001 nodejs \\
             && adduser  --system --uid 1001 nextjs

            COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
            # Copy assets missing from the standalone tree.
            COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static
            COPY --from=build --chown=nextjs:nodejs /app/public ./public

            USER nextjs
            EXPOSE {port}
            CMD ["node", "server.js"]
            """
        ).lstrip()
    @staticmethod
    def _dockerignore() -> str:
        """Keep the build context small and secrets out of it entirely."""
        return textwrap.dedent(
            """
            # Written by the deployment agent.
            node_modules
            .next
            .git
            .github
            .agentforge
            coverage
            test-results
            playwright-report
            # Never into the build context: an image layer is readable by anyone
            # who can pull the image.
            .env
            .env.*
            !.env.example
            npm-debug.log*
            Dockerfile
            .dockerignore
            """
        ).lstrip()
    @staticmethod
    def _task_definition(service: ServiceSpec, plan: DeploymentPlan,
                         contract: EnvironmentContract | None = None) -> dict:
        """The Fargate task definition, rendered by the workflow at deploy time."""
        port = service.port or 3000
        entries = generator_class()._runtime_secret_entries(contract)
        secrets = [
            {"name": entry.name, "valueFrom": f"__RUNTIME_SECRET_ARN__:{entry.name}::"}
            for entry in entries
        ]
        return {
            "family": f"{plan.project_slug}-task",
            "networkMode": "awsvpc",
            "requiresCompatibilities": ["FARGATE"],
            "cpu": "256",
            "memory": "512",
            "executionRoleArn": "__EXECUTION_ROLE_ARN__",
            "taskRoleArn": "__TASK_ROLE_ARN__",
            "containerDefinitions": [
                {
                    "name": f"{plan.project_slug}-app",
                    "image": "__IMAGE__",
                    "essential": True,
                    "portMappings": [{"containerPort": port, "protocol": "tcp"}],
                    "environment": [
                        {"name": "NODE_ENV", "value": "production"},
                        {"name": "PORT", "value": str(port)},
                        {"name": "HOSTNAME", "value": "0.0.0.0"},
                    ],
                    "secrets": secrets,
                    "logConfiguration": {
                        "logDriver": "awslogs",
                        "options": {
                            "awslogs-group": f"/deployment-agent/{plan.project_slug}",
                            "awslogs-region": "__AWS_REGION__",
                            "awslogs-stream-prefix": "app",
                        },
                    },
                }
            ],
        }
    @staticmethod
    def _release_script(service: ServiceSpec, contract: EnvironmentContract | None = None) -> str:
        """The script SSM Run Command executes on the instance for each release."""
        contract = contract or EnvironmentContract([])
        secret_names = [entry.name for entry in generator_class()._runtime_secret_entries(contract)]

        allowed = json.dumps(secret_names)
        return textwrap.dedent(
            f"""
            #!/usr/bin/env bash
            # Deployment Agent release script. Runs on the EC2 instance via SSM.
            # Usage: BUCKET=... SHA=... SECRET_ID=... release.sh
            set -euo pipefail

            : "${{BUCKET:?BUCKET is required}}"
            : "${{SHA:?SHA is required}}"
            : "${{SECRET_ID:?SECRET_ID is required}}"

            APP_DIR=/opt/app
            RELEASES="$APP_DIR/releases"
            SHARED="$APP_DIR/shared"
            RELEASE="$RELEASES/$SHA"
            PORT={service.port}
            HEALTH_PATH={service.health_path}

            mkdir -p "$RELEASES" "$SHARED"

            echo "==> Downloading release $SHA"
            aws s3 cp "s3://$BUCKET/releases/$SHA/app.tar.gz" /tmp/"$SHA".tar.gz

            echo "==> Unpacking"
            rm -rf "$RELEASE"
            mkdir -p "$RELEASE"
            tar -xzf /tmp/"$SHA".tar.gz -C "$RELEASE"
            rm -f /tmp/"$SHA".tar.gz

            echo "==> Refreshing runtime environment"
            env_tmp=$(mktemp)
            chmod 0600 "$env_tmp"
            {{
              echo "NODE_ENV=production"
              echo "PORT=$PORT"
              echo "HOSTNAME=127.0.0.1"
              aws secretsmanager get-secret-value --secret-id "$SECRET_ID" \\
                --query SecretString --output text \\
                | jq -r --argjson allowed '{allowed}' \\
                    'to_entries[] | select(.key as $k | $allowed | index($k)) | "\\(.key)=\\(.value|@sh)"'
            }} > "$env_tmp"
            install -o root -g nextjs -m 0640 "$env_tmp" "$SHARED/.env"
            rm -f "$env_tmp"

            chown -R nextjs:nextjs "$RELEASE"
            ln -sfn "$RELEASE" "$APP_DIR/current.new"
            mv -Tf "$APP_DIR/current.new" "$APP_DIR/current"

            echo "==> Restarting service"
            systemctl restart nextjs

            echo "==> Waiting for health check"
            for attempt in $(seq 1 30); do
              if curl -fsS --max-time 5 "http://127.0.0.1:$PORT$HEALTH_PATH" >/dev/null 2>&1; then
                echo "Health check passed on attempt $attempt"
                # Keep the five most recent releases so rollback has somewhere to go.
                ls -1dt "$RELEASES"/*/ 2>/dev/null | tail -n +6 | xargs -r rm -rf
                echo "$SHA" > "$SHARED/current-sha"
                exit 0
              fi
              sleep 2
            done

            echo "Health check failed; dumping recent service logs" >&2
            journalctl -u nextjs -n 50 --no-pager >&2 || true
            exit 1
            """
        ).lstrip()
    @staticmethod
    def _env_example(service: ServiceSpec, contract: EnvironmentContract | None = None) -> str:
        lines = ["# Copy to .env for local use. Never commit real values."]
        contract = contract or EnvironmentContractResolver.discover(service)
        names: set[str] = set()
        for entry in contract.entries:
            if entry.name in names or entry.resolution == "provider_managed" or entry.development_only:
                continue
            names.add(entry.name)
            suffix = " # optional" if not entry.required else ""
            lines.append(f"{entry.name}={suffix}")
        lines.append("")
        return "\n".join(lines)
    @staticmethod
    def _vercel_config(service: ServiceSpec) -> str:
        value = {
            "$schema": "https://openapi.vercel.sh/vercel.json",
            "framework": "nextjs",
            "installCommand": service.install_command,
            "buildCommand": service.build_command or "npm run build",
        }
        return json.dumps(value, indent=2) + "\n"
    @staticmethod
    def _vercel_deploy_workflow(service: ServiceSpec, spec: ProjectSpec, plan: DeploymentPlan) -> str:
        root = service.root or "."
        toolchain = generator_class()._toolchain(service)
        preinstall = toolchain["preinstall"]
        branch = spec.repository.branch or "main"
        trigger_branches = ", ".join(dict.fromkeys([branch, "main", "master"]))
        return textwrap.dedent(
            f"""
            name: Deploy to Vercel

            on:
              push:
                branches: [{trigger_branches}]
              workflow_dispatch:

            permissions:
              contents: read

            concurrency:
              group: production-{plan.project_slug}
              cancel-in-progress: false

            jobs:
              deploy:
                runs-on: ubuntu-latest
                defaults:
                  run:
                    working-directory: {root}
                env:
                  VERCEL_ORG_ID: ${{{{ vars.VERCEL_ORG_ID }}}}
                  VERCEL_PROJECT_ID: ${{{{ vars.VERCEL_PROJECT_ID }}}}
                  VERCEL_TOKEN: ${{{{ secrets.VERCEL_TOKEN }}}}
                steps:
                  - uses: actions/checkout@v4
            __SETUP_STEP__
                  - name: Install dependencies
                    run: |
                      {preinstall.rstrip() if preinstall else '# package manager is ready'}
                      {service.install_command}
                  - name: Install the Vercel CLI
                    run: npm install --global vercel@latest
                  - name: Pull the production environment
                    # Also supplies the production environment variables the
                    # Next.js build itself reads.
                    run: vercel pull --yes --environment=production --token="$VERCEL_TOKEN"
                  - name: Build
                    run: vercel build --prod --token="$VERCEL_TOKEN"
                  - id: deploy
                    name: Deploy prebuilt output
                    run: |
                      url="$(vercel deploy --prebuilt --prod --token="$VERCEL_TOKEN")"
                      echo "Deployed to $url"
                      echo "url=$url" >> "$GITHUB_OUTPUT"
                  - name: Smoke test
                    run: |
                      curl --fail --retry 12 --retry-delay 5 \\
                        "${{{{ steps.deploy.outputs.url }}}}{service.health_path}"
            """
        ).lstrip().replace("__SETUP_STEP__", toolchain["setup_step"])
    @staticmethod
    def _vercel_environment(contract: EnvironmentContract) -> dict:
        variables = []
        for entry in EnvironmentContractResolver.production_entries(contract):
            if entry.resolution in {"provider_managed", "optional"}:
                continue
            variables.append(
                {
                    "name": entry.name,
                    "targets": ["production", "preview"],
                    "type": "sensitive" if entry.secret else "plain",
                    "scope": entry.scope,
                    "resolution": entry.resolution,
                }
            )
        return {"variables": variables}

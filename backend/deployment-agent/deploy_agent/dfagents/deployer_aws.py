from __future__ import annotations

from .deployer_shared import *
from .deployer_shared import _ACTIVE_PROJECTS, _ACTIVE_LOCK, _PERSISTED_ACTIVE_STATES, _CANCELLABLE_STATES


class DeploymentAwsMixin:
    def _set_github_secrets(self, repo: str, values: dict[str, str]) -> None:
        """Write Actions secrets with the value on stdin."""
        for name, value in values.items():
            if not value:
                continue
            run_command(["gh", "secret", "set", name, "--repo", repo], timeout=60, check=True, input=value)
    @staticmethod
    def _aws_session(profile: str, region: str, credential_reference: str = ""):

        if credential_reference:
            from deployment_agent.aws_onboarding import session_from_reference

            return session_from_reference(credential_reference, region)
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is not installed. Run setup.ps1.") from exc
        kwargs: dict[str, str] = {"region_name": region}
        if profile:
            kwargs["profile_name"] = profile
        return boto3.Session(**kwargs)
    def _ensure_github_repository(self, source: Path, slug: str) -> str:
        if not (source / ".git").exists():
            run_command(["git", "init"], cwd=source, timeout=GIT_TIMEOUT_SECONDS, check=True)
            run_command(["git", "branch", "-M", "main"], cwd=source, timeout=GIT_TIMEOUT_SECONDS, check=True)
        remote = run_command(["git", "remote", "get-url", "origin"], cwd=source)
        if remote.returncode == 0 and remote.stdout.strip():
            parsed = self._parse_github_repo(remote.stdout.strip())
            if not parsed:
                raise RuntimeError("The existing origin is not a GitHub repository")
            return parsed
        user = run_command(["gh", "api", "user", "--jq", ".login"], check=True).stdout.strip()
        repo = f"{user}/{slug}"
        create = run_command(["gh", "repo", "create", repo, "--private"], cwd=source)
        if create.returncode != 0:
            suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
            repo = f"{user}/{slug}-{suffix}"
            run_command(["gh", "repo", "create", repo, "--private"], cwd=source, check=True)
        run_command(["git", "remote", "add", "origin", f"https://github.com/{repo}.git"], cwd=source, check=True)
        return repo
    @staticmethod
    def _parse_github_repo(remote: str) -> str:
        match = re.search(r"github\.com[/:]([^/\s]+/[^/\s]+?)(?:\.git)?$", remote)
        return match.group(1).removesuffix(".git") if match else ""
    @staticmethod
    def _github_default_branch(repo: str, fallback: str = "main") -> str:
        result = run_command(
            ["gh", "repo", "view", repo, "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name"],
            timeout=30,
        )
        branch = result.stdout.strip() if result.returncode == 0 else ""
        return branch or fallback or "main"
    @staticmethod
    def _github_repository_identity(repo: str) -> dict[str, Any]:
        result = run_command(["gh", "api", f"repos/{repo}"], timeout=30, check=True)
        try:
            identity = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("GitHub repository identity response was not valid JSON") from exc
        oidc = run_command(["gh", "api", f"repos/{repo}/actions/oidc/customization/sub"], timeout=30)
        if oidc.returncode == 0:
            try:
                customization = json.loads(oidc.stdout or "{}")
            except json.JSONDecodeError:
                customization = {}
            if customization.get("use_default") is False:
                raise RuntimeError(
                    "This repository uses a custom GitHub OIDC subject template; reset it to the default before deployment"
                )
        return identity
    @staticmethod
    def _github_oidc_subjects(repo: str, branch: str, identity: dict[str, Any]) -> list[str]:
        owner, name = repo.split("/", 1)
        subjects = [f"repo:{owner}/{name}:ref:refs/heads/{branch}"]
        owner_id = str((identity.get("owner") or {}).get("id") or "")
        repository_id = str(identity.get("id") or "")
        if owner_id and repository_id:
            subjects.append(f"repo:{owner}@{owner_id}/{name}@{repository_id}:ref:refs/heads/{branch}")
        return subjects
    @staticmethod
    def _find_github_oidc(session, stack_name: str = "") -> str:
        """An existing GitHub OIDC provider, but never one this stack owns."""
        iam = session.client("iam")
        arn = ""
        for item in iam.list_open_id_connect_providers().get("OpenIDConnectProviderList", []):
            candidate = item.get("Arn", "")
            try:
                provider = iam.get_open_id_connect_provider(OpenIDConnectProviderArn=candidate)
                if provider.get("Url") == "token.actions.githubusercontent.com":
                    arn = candidate
                    break
            except Exception:
                continue
        if not arn or not stack_name:
            return arn
        try:
            cfn = session.client("cloudformation")
            for page in cfn.get_paginator("list_stack_resources").paginate(StackName=stack_name):
                for res in page.get("StackResourceSummaries", []):
                    if res.get("ResourceType") == "AWS::IAM::OIDCProvider":
                        return ""
        except Exception:

            pass
        return arn
    @staticmethod
    def _template_declares(template_path: Path, parameter: str) -> bool:
        """Does this template take that parameter?"""
        try:
            return f"{parameter}:" in template_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
    def _apply_bootstrap_stack(
        self,
        run_id: str,
        session,
        template_path: Path,
        slug: str,
        oidc_subjects: list[str],
        port: int,
        oidc_arn: str,
        network: dict[str, str] | None = None,
    ) -> dict[str, str]:
        cfn = session.client("cloudformation")
        stack_name = f"{slug}-bootstrap"
        exists = True
        try:
            stack_status = cfn.describe_stacks(StackName=stack_name)["Stacks"][0].get("StackStatus", "")

            if stack_status == "ROLLBACK_COMPLETE":
                cfn.delete_stack(StackName=stack_name)
                cfn.get_waiter("stack_delete_complete").wait(
                    StackName=stack_name,
                    WaiterConfig={"Delay": 5, "MaxAttempts": 120},
                )
                exists = False
            elif stack_status == "ROLLBACK_IN_PROGRESS":
                for _ in range(120):
                    time.sleep(5)
                    try:
                        stack_status = cfn.describe_stacks(StackName=stack_name)["Stacks"][0].get("StackStatus", "")
                    except cfn.exceptions.ClientError:
                        stack_status = ""
                        break
                    if stack_status != "ROLLBACK_IN_PROGRESS":
                        break
                if stack_status == "ROLLBACK_COMPLETE":
                    cfn.delete_stack(StackName=stack_name)
                    cfn.get_waiter("stack_delete_complete").wait(
                        StackName=stack_name,
                        WaiterConfig={"Delay": 5, "MaxAttempts": 120},
                    )
                    exists = False
        except cfn.exceptions.ClientError as exc:
            if "does not exist" in str(exc):
                exists = False
            else:
                raise
        change_set = f"deployment-agent-{int(time.time())}"
        parameters = [
            {"ParameterKey": "ProjectSlug", "ParameterValue": slug},
            {"ParameterKey": "GitHubSubjects", "ParameterValue": ",".join(oidc_subjects)},
            {"ParameterKey": "AppPort", "ParameterValue": str(port)},
            {"ParameterKey": "ExistingOidcProviderArn", "ParameterValue": oidc_arn},
        ]
        if network:
            parameters.extend(
                [
                    {"ParameterKey": "ExistingVpcId", "ParameterValue": network["vpc_id"]},
                    {"ParameterKey": "ExistingSubnetA", "ParameterValue": network["subnet_a"]},
                ]
            )

            if network.get("subnet_b") and self._template_declares(
                template_path, "ExistingSubnetB"
            ):
                parameters.append(
                    {"ParameterKey": "ExistingSubnetB",
                     "ParameterValue": network["subnet_b"]}
                )
        cfn.create_change_set(
            StackName=stack_name,
            ChangeSetName=change_set,
            ChangeSetType="UPDATE" if exists else "CREATE",
            Description="Reviewed Deployment Agent bootstrap change set",
            TemplateBody=template_path.read_text(encoding="utf-8"),
            Parameters=parameters,
            Capabilities=["CAPABILITY_NAMED_IAM"],
        )
        self.emit(run_id, "step", "bootstrap", "running", 15, "CloudFormation change set created")
        no_changes = False
        while True:
            detail = cfn.describe_change_set(StackName=stack_name, ChangeSetName=change_set)
            status = detail.get("Status")
            if status == "CREATE_COMPLETE":
                break
            if status == "FAILED":
                reason = detail.get("StatusReason", "")
                if "didn't contain changes" in reason or "No updates" in reason:
                    no_changes = True
                    break
                raise RuntimeError(f"CloudFormation change set failed: {reason}")
            time.sleep(3)
        if not no_changes:
            changes = []
            for change in detail.get("Changes", []):
                resource = change.get("ResourceChange", {})
                changes.append(
                    {
                        "action": resource.get("Action", ""),
                        "logical_resource_id": resource.get("LogicalResourceId", ""),
                        "resource_type": resource.get("ResourceType", ""),
                        "replacement": resource.get("Replacement", ""),
                    }
                )
            self.emit(
                run_id,
                "change_set",
                "bootstrap",
                "preview",
                24,
                f"CloudFormation preview contains {len(changes)} reviewed resource change(s)",
                {"stack": stack_name, "changes": changes},
            )
            cfn.execute_change_set(StackName=stack_name, ChangeSetName=change_set)
            waiter_name = "stack_update_complete" if exists else "stack_create_complete"
            cfn.get_waiter(waiter_name).wait(
                StackName=stack_name,
                WaiterConfig={"Delay": 10, "MaxAttempts": 120},
            )
        stack = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]
        self.emit(run_id, "step", "bootstrap", "complete", 40, "AWS bootstrap stack is ready")
        return {item["OutputKey"]: item.get("OutputValue", "") for item in stack.get("Outputs", [])}
    def _select_bootstrap_network(self, run_id: str, session) -> dict[str, str] | None:
        """Use the generated dedicated VPC normally."""
        ec2 = session.client("ec2")
        vpcs = ec2.describe_vpcs().get("Vpcs", [])

        if len(vpcs) < 5:
            return None
        defaults = [vpc for vpc in vpcs if vpc.get("IsDefault")]
        candidates = defaults or [vpc for vpc in vpcs if vpc.get("State") == "available"]
        for vpc in candidates:
            vpc_id = vpc.get("VpcId", "")
            if not vpc_id:
                continue
            subnets = ec2.describe_subnets(
                Filters=[
                    {"Name": "vpc-id", "Values": [vpc_id]},
                    {"Name": "state", "Values": ["available"]},
                ]
            ).get("Subnets", [])
            public = [item for item in subnets if item.get("MapPublicIpOnLaunch")]
            if len(public) < 2:
                public = subnets
            by_az: dict[str, dict[str, Any]] = {}
            for subnet in sorted(public, key=lambda item: (item.get("AvailabilityZone", ""), item.get("SubnetId", ""))):
                by_az.setdefault(subnet.get("AvailabilityZone", ""), subnet)
            selected = list(by_az.values())[:2]
            if len(selected) == 2:
                network = {
                    "vpc_id": vpc_id,
                    "subnet_a": selected[0]["SubnetId"],
                    "subnet_b": selected[1]["SubnetId"],
                }
                self.emit(
                    run_id,
                    "warning",
                    "bootstrap",
                    "complete",
                    8,
                    "Regional VPC quota is full; reusing two existing public subnets safely",
                    {"vpc_id": vpc_id, "subnets": [network["subnet_a"], network["subnet_b"]]},
                )
                return network
        raise RuntimeError("AWS VPC quota is exhausted and no pair of public subnets is available for safe reuse")

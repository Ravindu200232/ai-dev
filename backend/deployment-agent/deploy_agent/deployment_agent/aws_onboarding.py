"""AWS onboarding, SSO, quotas, and permission checks."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .credentials import CREDENTIAL_VAULT
from .security import redact_text
from .aws_sso import SsoFlow, SsoDeviceFlowManager, _boto3, _unsigned_client


CLIENT_NAME = "deployment-agent"
CLIENT_TYPE = "public"
SSO_SCOPES = ["sso:account:access"]


QUOTA_CHECKS = (
    {
        "key": "vpc",
        "label": "VPCs per Region",
        "service_code": "vpc",
        "quota_code": "L-F678F1CE",
        "needed": 1,
    },
    {
        "key": "eip",
        "label": "Elastic IPs per Region",
        "service_code": "ec2",
        "quota_code": "L-0263D0A3",
        "needed": 1,
    },
    {
        "key": "vcpu",
        "label": "Running On-Demand Standard instances (vCPU)",
        "service_code": "ec2",
        "quota_code": "L-1216C47A",
        "needed": 2,
    },
)


ECS_QUOTA_CHECKS = (
    {
        "key": "vpc",
        "label": "VPCs per Region",
        "service_code": "vpc",
        "quota_code": "L-F678F1CE",
        "needed": 1,
    },
    {
        "key": "fargate_vcpu",
        "label": "Fargate On-Demand vCPU",
        "service_code": "fargate",
        "quota_code": "L-3032A538",
        "needed": 1,
    },
    {
        "key": "alb",
        "label": "Application Load Balancers per Region",
        "service_code": "elasticloadbalancing",
        "quota_code": "L-53DA6B97",
        "needed": 1,
    },
)


REQUIRED_ACTIONS = (
    "sts:GetCallerIdentity",
    "cloudformation:CreateChangeSet",
    "cloudformation:ExecuteChangeSet",
    "cloudformation:DescribeChangeSet",
    "cloudformation:DescribeStacks",
    "cloudformation:DescribeStackEvents",
    "cloudformation:DeleteStack",
    "ec2:DescribeVpcs",
    "ec2:DescribeSubnets",
    "ec2:DescribeInstances",
    "ec2:CreateVpc",
    "ec2:CreateSubnet",
    "ec2:CreateInternetGateway",
    "ec2:CreateSecurityGroup",
    "ec2:AuthorizeSecurityGroupIngress",
    "ec2:AllocateAddress",
    "ec2:AssociateAddress",
    "ec2:RunInstances",
    "ec2:CreateTags",
    "iam:CreateRole",
    "iam:PutRolePolicy",
    "iam:AttachRolePolicy",
    "iam:CreateInstanceProfile",
    "iam:AddRoleToInstanceProfile",
    "iam:PassRole",
    "iam:CreateOpenIDConnectProvider",
    "iam:ListOpenIDConnectProviders",
    "s3:CreateBucket",
    "s3:PutEncryptionConfiguration",
    "s3:PutBucketVersioning",
    "s3:PutBucketPublicAccessBlock",
    "s3:PutLifecycleConfiguration",
    "secretsmanager:CreateSecret",
    "secretsmanager:PutSecretValue",
    "logs:CreateLogGroup",
    "logs:PutRetentionPolicy",
    "logs:FilterLogEvents",

    "ssm:GetParameters",
    "ssm:SendCommand",
    "ssm:GetCommandInvocation",
    "ssm:ListCommandInvocations",
)


_SHARED_ACTIONS = (
    "sts:GetCallerIdentity",
    "cloudformation:CreateChangeSet",
    "cloudformation:ExecuteChangeSet",
    "cloudformation:DescribeChangeSet",
    "cloudformation:DescribeStacks",
    "cloudformation:DescribeStackEvents",
    "cloudformation:DeleteStack",
    "ec2:DescribeVpcs",
    "ec2:DescribeSubnets",
    "ec2:CreateVpc",
    "ec2:CreateSubnet",
    "ec2:CreateInternetGateway",
    "ec2:CreateSecurityGroup",
    "ec2:AuthorizeSecurityGroupIngress",
    "ec2:CreateTags",
    "iam:CreateRole",
    "iam:PutRolePolicy",
    "iam:AttachRolePolicy",
    "iam:PassRole",
    "iam:CreateOpenIDConnectProvider",
    "iam:ListOpenIDConnectProviders",
    "secretsmanager:CreateSecret",
    "secretsmanager:PutSecretValue",
    "logs:CreateLogGroup",
    "logs:PutRetentionPolicy",
    "logs:FilterLogEvents",
)


ECS_REQUIRED_ACTIONS = _SHARED_ACTIONS + (
    "ecr:CreateRepository",
    "ecr:DescribeRepositories",
    "ecr:GetAuthorizationToken",
    "ecr:PutLifecyclePolicy",
    "ecr:ListImages",
    "ecr:BatchDeleteImage",
    "ecr:DeleteRepository",
    "ecs:CreateCluster",
    "ecs:CreateService",
    "ecs:RegisterTaskDefinition",
    "ecs:UpdateService",
    "ecs:DescribeServices",
    "ecs:DescribeClusters",
    "ecs:DeleteService",
    "ecs:DeleteCluster",
    "elasticloadbalancing:CreateLoadBalancer",
    "elasticloadbalancing:CreateTargetGroup",
    "elasticloadbalancing:CreateListener",
    "elasticloadbalancing:DescribeLoadBalancers",
    "elasticloadbalancing:DeleteLoadBalancer",
)


def actions_for(target) -> tuple[str, ...]:
    """The IAM actions a preflight should simulate for this target."""
    from deployment_agent.models import DeploymentTarget

    if target == DeploymentTarget.AWS_ECS:
        return ECS_REQUIRED_ACTIONS
    if target == DeploymentTarget.VERCEL:
        return ()
    return REQUIRED_ACTIONS


def quotas_for(target) -> tuple[dict[str, Any], ...]:
    """The service quotas this target consumes."""
    from deployment_agent.models import DeploymentTarget

    if target == DeploymentTarget.AWS_ECS:
        return ECS_QUOTA_CHECKS
    if target == DeploymentTarget.VERCEL:
        return ()
    return QUOTA_CHECKS


def session_from_reference(credential_reference: str, region: str = ""):
    """Build a boto3 session from vaulted short-lived credentials."""
    bundle = CREDENTIAL_VAULT.get(credential_reference)
    values = bundle.credentials
    return _boto3().Session(
        aws_access_key_id=values.get("aws_access_key_id"),
        aws_secret_access_key=values.get("aws_secret_access_key"),
        aws_session_token=values.get("aws_session_token"),
        region_name=region or values.get("region") or None,
    )


def persist_sso_profile(
    profile_name: str,
    start_url: str,
    region: str,
    account_id: str,
    role_name: str,
    deployment_region: str = "",
) -> str:
    """Write an sso-session profile to ~/.aws/config."""
    import configparser
    from pathlib import Path

    profile_name = str(profile_name or "deployment-agent").strip() or "deployment-agent"
    config_path = Path.home() / ".aws" / "config"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    parser = configparser.ConfigParser()
    if config_path.is_file():
        parser.read(config_path, encoding="utf-8")
    session_section = f"sso-session {profile_name}"
    profile_section = f"profile {profile_name}"
    for section in (session_section, profile_section):
        if not parser.has_section(section):
            parser.add_section(section)
    parser[session_section]["sso_start_url"] = start_url
    parser[session_section]["sso_region"] = region
    parser[session_section]["sso_registration_scopes"] = "sso:account:access"
    parser[profile_section]["sso_session"] = profile_name
    parser[profile_section]["sso_account_id"] = str(account_id)
    parser[profile_section]["sso_role_name"] = str(role_name)

    parser[profile_section]["region"] = deployment_region or region

    parser.remove_option(profile_section, "login_session")
    with config_path.open("w", encoding="utf-8") as handle:
        parser.write(handle)
    return profile_name


def check_quotas(session, target=None) -> list[dict[str, Any]]:
    """Compare the quotas this target's stack consumes against current usage."""
    checks = quotas_for(target) if target is not None else QUOTA_CHECKS
    if not checks:
        return []
    quotas = session.client("service-quotas")
    ec2 = session.client("ec2")
    usage: dict[str, int | None] = {"vpc": None, "eip": None, "vcpu": None}
    try:
        usage["vpc"] = len(ec2.describe_vpcs().get("Vpcs", []))
    except Exception:
        pass
    try:
        usage["eip"] = len(ec2.describe_addresses().get("Addresses", []))
    except Exception:
        pass

    results: list[dict[str, Any]] = []
    for check in checks:
        entry: dict[str, Any] = {
            "key": check["key"],
            "label": check["label"],
            "service_code": check["service_code"],
            "quota_code": check["quota_code"],
            "needed": check["needed"],
            "limit": None,
            "used": usage.get(check["key"]),
            "status": "unknown",
            "message": "",
        }
        try:
            value = quotas.get_service_quota(
                ServiceCode=check["service_code"], QuotaCode=check["quota_code"]
            )["Quota"]["Value"]
            entry["limit"] = int(value)
        except Exception as exc:
            entry["message"] = redact_text(str(exc))
            results.append(entry)
            continue
        limit = entry["limit"] or 0
        used = entry["used"]
        if limit < check["needed"]:
            entry["status"] = "failed"
            entry["message"] = f"Quota is {limit}; the deployment needs at least {check['needed']}."
        elif used is not None and (limit - used) < check["needed"]:
            entry["status"] = "failed"
            entry["message"] = f"{used} of {limit} already in use; no headroom for this deployment."
        else:
            entry["status"] = "passed"
        results.append(entry)
    return results


def request_quota_increase(session, service_code: str, quota_code: str, desired: float) -> dict[str, Any]:
    response = session.client("service-quotas").request_service_quota_increase(
        ServiceCode=str(service_code), QuotaCode=str(quota_code), DesiredValue=float(desired)
    )
    request = response.get("RequestedQuota", {})
    return {
        "id": request.get("Id", ""),
        "status": request.get("Status", ""),
        "desired": request.get("DesiredValue"),
        "quota_code": request.get("QuotaCode", ""),
    }


def check_permissions(session, actions: tuple[str, ...] = REQUIRED_ACTIONS) -> dict[str, Any]:
    """Ask IAM whether the signed-in principal may perform each required action."""
    identity = session.client("sts").get_caller_identity()
    arn = identity.get("Arn", "")

    source = arn
    if ":assumed-role/" in arn:
        account = identity.get("Account", "")
        role_name = arn.split(":assumed-role/")[1].split("/")[0]
        source = f"arn:aws:iam::{account}:role/{role_name}"
    allowed: list[str] = []
    denied: list[str] = []
    try:
        iam = session.client("iam")
        for index in range(0, len(actions), 25):
            batch = list(actions[index : index + 25])
            response = iam.simulate_principal_policy(PolicySourceArn=source, ActionNames=batch)
            for item in response.get("EvaluationResults", []):
                name = item.get("EvalActionName", "")
                if item.get("EvalDecision") == "allowed":
                    allowed.append(name)
                else:
                    denied.append(name)
    except Exception as exc:
        return {
            "status": "unknown",
            "principal": source,
            "allowed": [],
            "denied": [],
            "message": redact_text(
                "Permission simulation is unavailable for this identity "
                f"({type(exc).__name__}); deployment will surface any missing permission directly."
            ),
        }
    return {
        "status": "passed" if not denied else "failed",
        "principal": source,
        "allowed": sorted(allowed),
        "denied": sorted(denied),
        "message": "" if not denied else f"{len(denied)} required action(s) are denied for this identity.",
    }


def bootstrap_role_template(trusted_principal_arn: str = "") -> str:
    """CloudFormation an administrator applies once to grant deploy permissions."""
    import textwrap

    principal = str(trusted_principal_arn or "").strip()
    header = textwrap.dedent(
        """
        AWSTemplateFormatVersion: '2010-09-09'
        Description: Deployment Agent bootstrap permissions role

        Parameters:
          TrustedPrincipalArn:
            Type: String
            Default: __PRINCIPAL__
            Description: IAM principal (user or role) allowed to assume this deployment role

        Resources:
          DeploymentAgentRole:
            Type: AWS::IAM::Role
            Properties:
              RoleName: deployment-agent-bootstrap
              AssumeRolePolicyDocument:
                Version: '2012-10-17'
                Statement:
                  - Effect: Allow
                    Principal: {AWS: !Ref TrustedPrincipalArn}
                    Action: 'sts:AssumeRole'
              Policies:
                - PolicyName: DeploymentAgentBootstrap
                  PolicyDocument:
                    Version: '2012-10-17'
                    Statement:
                      - Effect: Allow
                        Resource: '*'
                        Action:
        __ACTIONS__

        Outputs:
          RoleArn:
            Value: !GetAtt DeploymentAgentRole.Arn
        """
    ).lstrip()
    actions = "\n".join(f"                          - {name}" for name in sorted(REQUIRED_ACTIONS))
    return header.replace("__ACTIONS__", actions).replace("__PRINCIPAL__", principal or "''")

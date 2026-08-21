from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .credentials import CREDENTIAL_VAULT

CLIENT_NAME = "deployment-agent"
CLIENT_TYPE = "public"
SSO_SCOPES = ["sso:account:access"]

def _boto3():
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError("boto3 is not installed. Run setup.ps1.") from exc
    return boto3

def _unsigned_client(service: str, region: str):
    """Sso-oidc and sso device-flow calls are unauthenticated by design."""
    from botocore import UNSIGNED
    from botocore.config import Config

    return _boto3().client(service, region_name=region, config=Config(signature_version=UNSIGNED))

@dataclass
class SsoFlow:
    flow_id: str
    region: str
    start_url: str
    client_id: str = ""
    client_secret: str = ""
    device_code: str = ""
    interval: int = 5
    expires_at: float = 0.0
    token_reference: str = ""
    created_at: float = field(default_factory=time.time)

class SsoDeviceFlowManager:
    """Drives the AWS IAM Identity Center device authorization flow."""

    def __init__(self, ttl_seconds: int = 900):
        self.ttl_seconds = ttl_seconds
        self._flows: dict[str, SsoFlow] = {}
        self._lock = threading.RLock()

    def start(self, start_url: str, region: str) -> dict[str, Any]:
        start_url = str(start_url or "").strip()
        region = str(region or "").strip()
        if not start_url.startswith("https://"):
            raise ValueError("An IAM Identity Center start URL beginning with https:// is required")
        if not region:
            raise ValueError("An AWS region is required")
        oidc = _unsigned_client("sso-oidc", region)
        registration = oidc.register_client(clientName=CLIENT_NAME, clientType=CLIENT_TYPE, scopes=SSO_SCOPES)
        authorization = oidc.start_device_authorization(
            clientId=registration["clientId"],
            clientSecret=registration["clientSecret"],
            startUrl=start_url,
        )
        import secrets as _secrets

        flow = SsoFlow(
            flow_id="sso_" + _secrets.token_urlsafe(18),
            region=region,
            start_url=start_url,
            client_id=registration["clientId"],
            client_secret=registration["clientSecret"],
            device_code=authorization["deviceCode"],
            interval=int(authorization.get("interval", 5)) or 5,
            expires_at=time.time() + int(authorization.get("expiresIn", 600)),
        )
        with self._lock:
            self._prune_locked()
            self._flows[flow.flow_id] = flow
        return {
            "flow_id": flow.flow_id,
            "verification_uri_complete": authorization.get("verificationUriComplete", ""),
            "verification_uri": authorization.get("verificationUri", ""),
            "user_code": authorization.get("userCode", ""),
            "interval": flow.interval,
            "expires_in": int(authorization.get("expiresIn", 600)),
        }

    def poll(self, flow_id: str) -> dict[str, Any]:
        """One non-blocking poll. The UI calls this on the interval AWS gave us."""
        flow = self._flow(flow_id)
        if flow.token_reference:
            return {"status": "complete", "accounts": self.list_accounts(flow_id)}
        if time.time() > flow.expires_at:
            raise ValueError("The AWS sign-in request expired; start the connection again")
        oidc = _unsigned_client("sso-oidc", flow.region)
        try:
            token = oidc.create_token(
                clientId=flow.client_id,
                clientSecret=flow.client_secret,
                grantType="urn:ietf:params:oauth:grant-type:device_code",
                deviceCode=flow.device_code,
            )
        except Exception as exc:
            name = type(exc).__name__

            if "AuthorizationPending" in name or "SlowDown" in name:
                return {"status": "pending"}
            if "ExpiredToken" in name:
                raise ValueError("The AWS sign-in request expired; start the connection again") from exc
            raise ValueError(redact_text(str(exc))) from exc
        reference = CREDENTIAL_VAULT.put(credentials={"access_token": token["accessToken"]})
        with self._lock:
            flow.token_reference = reference
        return {"status": "complete", "accounts": self.list_accounts(flow_id)}

    def list_accounts(self, flow_id: str) -> list[dict[str, str]]:
        flow = self._flow(flow_id)
        token = self._access_token(flow)
        sso = _unsigned_client("sso", flow.region)
        accounts: list[dict[str, str]] = []
        paginator = sso.get_paginator("list_accounts")
        for page in paginator.paginate(accessToken=token):
            for item in page.get("accountList", []):
                accounts.append(
                    {
                        "account_id": item.get("accountId", ""),
                        "account_name": item.get("accountName", ""),
                        "email": item.get("emailAddress", ""),
                    }
                )
        return sorted(accounts, key=lambda item: item["account_name"].lower())

    def list_roles(self, flow_id: str, account_id: str) -> list[str]:
        flow = self._flow(flow_id)
        token = self._access_token(flow)
        sso = _unsigned_client("sso", flow.region)
        roles: list[str] = []
        paginator = sso.get_paginator("list_account_roles")
        for page in paginator.paginate(accessToken=token, accountId=str(account_id)):
            roles.extend(item.get("roleName", "") for item in page.get("roleList", []))
        return sorted(name for name in roles if name)

    def select(self, flow_id: str, account_id: str, role_name: str) -> dict[str, Any]:
        """Exchange the SSO token for role credentials and vault them."""
        flow = self._flow(flow_id)
        token = self._access_token(flow)
        sso = _unsigned_client("sso", flow.region)
        role = sso.get_role_credentials(
            roleName=str(role_name), accountId=str(account_id), accessToken=token
        )["roleCredentials"]
        reference = CREDENTIAL_VAULT.put(
            credentials={
                "aws_access_key_id": role["accessKeyId"],
                "aws_secret_access_key": role["secretAccessKey"],
                "aws_session_token": role["sessionToken"],
                "region": flow.region,
            }
        )
        return {
            "credential_reference": reference,
            "account_id": str(account_id),
            "role_name": str(role_name),
            "region": flow.region,

            "expires_at": int(role.get("expiration", 0)) // 1000,
        }

    def start_url(self, flow_id: str) -> str:
        return self._flow(flow_id).start_url

    def forget(self, flow_id: str) -> None:
        with self._lock:
            flow = self._flows.pop(flow_id, None)
        if flow and flow.token_reference:
            CREDENTIAL_VAULT.clear(flow.token_reference)

    def _flow(self, flow_id: str) -> SsoFlow:
        with self._lock:
            self._prune_locked()
            flow = self._flows.get(str(flow_id))
        if not flow:
            raise ValueError("The AWS sign-in session expired; start the connection again")
        return flow

    def _access_token(self, flow: SsoFlow) -> str:
        if not flow.token_reference:
            raise ValueError("AWS sign-in has not completed yet")
        return CREDENTIAL_VAULT.get(flow.token_reference).credentials["access_token"]

    def _prune_locked(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        for flow_id, flow in list(self._flows.items()):
            if flow.created_at < cutoff:
                self._flows.pop(flow_id, None)
                if flow.token_reference:
                    CREDENTIAL_VAULT.clear(flow.token_reference)

from __future__ import annotations

from .deployer_shared import *
from .deployer_shared import _PERSISTED_ACTIVE_STATES
from .deployer_core import DeploymentCoreMixin
from .deployer_prepare import DeploymentPrepareMixin
from .deployer_aws import DeploymentAwsMixin
from .deployer_git import DeploymentGitMixin
from .deployer_lifecycle import DeploymentLifecycleMixin


class DeploymentAgent(DeploymentCoreMixin, DeploymentPrepareMixin, DeploymentAwsMixin, DeploymentGitMixin, DeploymentLifecycleMixin):
    def __init__(self, store: StateStore, emit: Callable[..., object]):
        self.store = store
        self.emit = emit
    def start_deployment(
        self,
        run_id: str,
        aws_profile: str,
        region: str,
        mongodb_uri: str,
        approved: bool,
        credential_reference: str = "",
        vercel_token: str = "",
    ) -> None:
        _run, project_key = self._validate_request(run_id, mongodb_uri, approved)
        self._reserve_project(run_id, project_key)
        threading.Thread(
            target=self._deploy_reserved,
            args=(run_id, aws_profile, region, mongodb_uri, project_key, credential_reference, vercel_token),
            daemon=True,
        ).start()
    def deploy(
        self,
        run_id: str,
        aws_profile: str,
        region: str,
        mongodb_uri: str,
        approved: bool,
        credential_reference: str = "",
        vercel_token: str = "",
    ) -> None:
        _run, project_key = self._validate_request(run_id, mongodb_uri, approved)
        self._reserve_project(run_id, project_key)
        self._deploy_reserved(
            run_id, aws_profile, region, mongodb_uri, project_key, credential_reference, vercel_token
        )

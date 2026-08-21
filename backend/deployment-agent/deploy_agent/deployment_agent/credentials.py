from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Mapping


@dataclass
class CredentialBundle:
    credentials: dict[str, str] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class InMemoryCredentialVault:
    """Short-lived credential storage that has no serialization surface."""

    def __init__(self, ttl_seconds: int = 3600):
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, CredentialBundle] = {}
        self._lock = threading.RLock()

    def put(
        self,
        credentials: Mapping[str, str] | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> str:
        reference = "vault_" + secrets.token_urlsafe(24)
        bundle = CredentialBundle(
            credentials={str(key): str(value) for key, value in (credentials or {}).items() if value is not None},
            environment={str(key): str(value) for key, value in (environment or {}).items() if value is not None},
        )
        with self._lock:
            self._prune_locked()
            self._items[reference] = bundle
        return reference

    def get(self, reference: str) -> CredentialBundle:
        with self._lock:
            self._prune_locked()
            bundle = self._items.get(reference)
            if not bundle:
                raise ValueError("Deployment credentials expired or were not supplied; reconnect the provider and retry")
            return bundle

    def pop(self, reference: str) -> CredentialBundle:
        with self._lock:
            self._prune_locked()
            bundle = self._items.pop(reference, None)
            if not bundle:
                raise ValueError("Deployment credentials expired or were not supplied; reconnect the provider and retry")
            return bundle

    def clear(self, reference: str) -> None:
        with self._lock:
            bundle = self._items.pop(reference, None)
            if bundle:
                for mapping in (bundle.credentials, bundle.environment):
                    for key in list(mapping):
                        mapping[key] = ""
                    mapping.clear()

    def _prune_locked(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        for reference, bundle in list(self._items.items()):
            if bundle.created_at < cutoff:
                self._items.pop(reference, None)


CREDENTIAL_VAULT = InMemoryCredentialVault()

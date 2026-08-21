from __future__ import annotations

import re
import secrets
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from .models import EnvironmentContract, EnvironmentEntry, ServiceSpec


RESOLUTIONS = {
    "user_required",
    "auto_generate",
    "provider_managed",
    "optional",
    "static_non_secret",
}

_PROVIDER_MANAGED = {
    "NODE_ENV",
    "PORT",
    "VERCEL",
    "VERCEL_ENV",
    "VERCEL_URL",
    "VERCEL_REGION",
    "CI",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
}
_AUTO_GENERATED = {"AUTH_SECRET", "NEXTAUTH_SECRET"}


DEPLOYER_INJECTED = ("BETTER_AUTH_URL", "BASE_URL")
_DEVELOPMENT_NAMES = {"AGENTFORGE_DEVTOOLS_SRC", "NEXT_PUBLIC_AGENTFORGE_DEVTOOLS_SRC"}
_DEV_CONDITION_RE = re.compile(
    r"NODE_ENV\s*(?:===|==)\s*['\"]development['\"]|NODE_ENV\s*(?:!==|!=)\s*['\"]production['\"]",
    re.IGNORECASE,
)


class EnvironmentContractResolver:
    """Build and resolve one authoritative, value-free production contract."""

    @classmethod
    def discover(cls, service: ServiceSpec, service_root: Path | None = None) -> EnvironmentContract:
        entries: dict[str, EnvironmentEntry] = {}
        for variable in service.environment:
            name = variable.name.upper()
            development_only = cls._development_only(name, variable.sources, service_root)
            public = name.startswith("NEXT_PUBLIC_")
            required = bool(variable.required) and not development_only
            secret = bool(variable.secret) and not public
            resolution = "user_required"
            value_present = False
            if name in _PROVIDER_MANAGED:
                resolution = "provider_managed"
                required = True
                secret = False
                value_present = True
            elif name in _AUTO_GENERATED:
                resolution = "auto_generate"
                required = True
                secret = True
                value_present = True

            elif name == "MONGODB_URI":
                resolution = "user_required"
                required = True
                secret = True
            elif name == "BETTER_AUTH_SECRET":
                resolution = "auto_generate" if service.has_better_auth else "optional"
                required = service.has_better_auth
                secret = service.has_better_auth
                value_present = True
            elif development_only:
                resolution = "optional"
                required = False
                secret = False if public else secret
                value_present = True
            elif name.startswith("NEXT_PUBLIC_"):
                resolution = "user_required" if required else "optional"
                secret = False
            entries[name] = EnvironmentEntry(
                name=name,
                required=required,
                secret=secret,
                scope="both" if public else variable.scope,
                sources=sorted(set(variable.sources)),
                resolution=resolution,
                value_present=value_present,
                public=public,
                development_only=development_only,
            )

        if service.has_mongodb and "MONGODB_URI" not in entries:
            entries["MONGODB_URI"] = EnvironmentEntry(
                name="MONGODB_URI",
                required=True,
                secret=True,
                scope="runtime",
                resolution="user_required",
            )
        if service.has_better_auth and "BETTER_AUTH_SECRET" not in entries:
            entries["BETTER_AUTH_SECRET"] = EnvironmentEntry(
                name="BETTER_AUTH_SECRET",
                required=True,
                secret=True,
                scope="runtime",
                resolution="auto_generate",
                value_present=True,
            )
        return EnvironmentContract(entries=[entries[name] for name in sorted(entries)])

    @staticmethod
    def resolve(
        contract: EnvironmentContract,
        supplied: Mapping[str, str] | None = None,
    ) -> tuple[EnvironmentContract, dict[str, str]]:
        supplied = {str(key).upper(): str(value) for key, value in (supplied or {}).items() if value is not None}
        resolved_entries: list[EnvironmentEntry] = []
        values: dict[str, str] = {}
        seen: set[str] = set()
        for entry in contract.entries:
            if entry.name in seen:
                raise ValueError(f"Duplicate environment variable: {entry.name}")
            seen.add(entry.name)
            present = entry.value_present
            if entry.resolution == "user_required":
                value = supplied.get(entry.name, "").strip()
                present = bool(value)
                if value:
                    values[entry.name] = value
            elif entry.resolution == "auto_generate":
                value = supplied.get(entry.name, "").strip() or secrets.token_urlsafe(48)
                values[entry.name] = value
                present = True
            elif entry.resolution == "static_non_secret":
                value = supplied.get(entry.name, "").strip()
                if value:
                    values[entry.name] = value
                present = bool(value) or not entry.required
            elif entry.resolution in {"provider_managed", "optional"}:
                value = supplied.get(entry.name, "").strip()
                if value:
                    values[entry.name] = value
                present = True
            else:
                present = False
            resolved_entries.append(replace(entry, value_present=present))
        return EnvironmentContract(resolved_entries), values

    @staticmethod
    def validate(contract: EnvironmentContract) -> list[str]:
        errors: list[str] = []
        seen: set[str] = set()
        for entry in contract.entries:
            if entry.name in seen:
                errors.append(f"Duplicate environment variable: {entry.name}")
            seen.add(entry.name)
            if entry.resolution not in RESOLUTIONS:
                errors.append(f"Environment variable {entry.name} has no supported resolution")
            if entry.required and not entry.value_present:
                errors.append(f"Required environment variable {entry.name} is unresolved")
            if entry.public and entry.secret:
                errors.append(f"Public environment variable {entry.name} cannot be classified as a secret")
            if entry.name in _PROVIDER_MANAGED and entry.resolution != "provider_managed":
                errors.append(f"Platform variable {entry.name} must be provider managed")
        return errors

    @staticmethod
    def production_entries(contract: EnvironmentContract) -> list[EnvironmentEntry]:
        return [
            entry
            for entry in contract.entries
            if not entry.development_only and entry.resolution != "optional"
        ]

    @staticmethod
    def _development_only(name: str, sources: list[str], service_root: Path | None) -> bool:
        if name in _DEVELOPMENT_NAMES:
            return True
        if not service_root or not sources:
            return False
        occurrences = 0
        guarded = 0
        for source in sources:
            path = service_root / source
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for match in re.finditer(rf"process\.env(?:\.{re.escape(name)}|\[['\"]{re.escape(name)}['\"]\])", text):
                occurrences += 1

                context = text[max(0, match.start() - 300): match.start()]
                if _DEV_CONDITION_RE.search(context):
                    guarded += 1
        return bool(occurrences) and occurrences == guarded


def contract_from_dict(value: dict) -> EnvironmentContract:
    raw_entries = value.get("entries", value if isinstance(value, list) else [])
    return EnvironmentContract([EnvironmentEntry(**item) for item in raw_entries if isinstance(item, dict)])

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


SECRET_NAME_RE = re.compile(
    r"(?:SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE_KEY|ACCESS_KEY|API_KEY|MONGODB_URI|DATABASE_URL)",
    re.IGNORECASE,
)
SECRET_VALUE_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"mongodb(?:\+srv)?://[^\s'\"<>]+", re.IGNORECASE),
    re.compile(r"https?://[^/\s:@]+:[^/\s@]+@[^\s'\"<>]+", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes()) if path.exists() else ""


def is_secret_name(name: str) -> bool:
    return bool(SECRET_NAME_RE.search(name))


def redact_text(value: str) -> str:
    redacted = value
    for pattern in SECRET_VALUE_PATTERNS:
        redacted = pattern.sub("***REDACTED***", redacted)
    redacted = re.sub(
        r"(?im)^([A-Z][A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|URI|KEY)[A-Z0-9_]*\s*=\s*).+$",
        r"\1***REDACTED***",
        redacted,
    )
    return redacted


def redact_data(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            result[key] = "***REDACTED***" if is_secret_name(str(key)) else redact_data(item)
        return result
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def json_dumps_safe(value: Any) -> str:
    return json.dumps(redact_data(value), ensure_ascii=False, separators=(",", ":"))

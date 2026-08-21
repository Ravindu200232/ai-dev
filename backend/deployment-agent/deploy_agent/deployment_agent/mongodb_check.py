"""Verify a MongoDB Atlas URI before it is committed to a deployment."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .security import redact_text


_ALLOWLIST_HINTS = ("timed out", "no replica set members", "connection refused", "ServerSelectionTimeoutError")
_AUTH_HINTS = ("auth failed", "authentication failed", "bad auth")


_KNOWN_OPTIONS = {
    "appname", "authmechanism", "authmechanismproperties", "authsource",
    "compressors", "connecttimeoutms", "directconnection", "heartbeatfrequencyms",
    "journal", "loadbalanced", "localthresholdms", "maxconnecting",
    "maxidletimems", "maxpoolsize", "maxstalenessseconds", "minpoolsize",
    "proxyhost", "proxypassword", "proxyport", "proxyusername",
    "readconcernlevel", "readpreference", "readpreferencetags", "replicaset",
    "retryreads", "retrywrites", "servermonitoringmode",
    "serverselectiontimeoutms", "serverselectiontryonce", "sockettimeoutms",
    "srvmaxhosts", "srvservicename", "ssl", "timeoutms", "tls",
    "tlsallowinvalidcertificates", "tlsallowinvalidhostnames",
    "tlscafile", "tlscertificatekeyfile", "tlscertificatekeyfilepassword",
    "tlsdisablecertificaterevocationcheck", "tlsdisableocspendpointcheck",
    "tlsinsecure", "uuidrepresentation", "w", "waitqueuetimeoutms", "wtimeoutms",
    "zlibcompressionlevel",
}


def _database_from_uri(uri: str) -> str:
    try:
        path = urlsplit(uri).path or ""
    except ValueError:
        return ""
    return path.lstrip("/").split("?")[0]


def _unknown_options(uri: str) -> list[str]:
    """Options this URI carries that the Node driver would reject."""
    try:
        query = urlsplit(uri).query or ""
    except ValueError:
        return []
    return [k for k, _ in parse_qsl(query, keep_blank_values=True)
            if k.lower() not in _KNOWN_OPTIONS]


def check_mongodb_uri(uri: str, timeout_ms: int = 6000) -> dict[str, Any]:
    """Connect, ping, and report what the deployment will actually get."""
    uri = str(uri or "").strip()
    if not uri or any(character.isspace() for character in uri):
        return {"ok": False, "code": "malformed", "message": "The connection string is empty or contains whitespace."}
    try:
        parsed = urlsplit(uri)
    except ValueError:
        return {"ok": False, "code": "malformed", "message": "The connection string could not be parsed."}
    if parsed.scheme not in {"mongodb", "mongodb+srv"} or not parsed.hostname:
        return {
            "ok": False,
            "code": "malformed",
            "message": "The URI must start with mongodb:// or mongodb+srv:// and include a host.",
        }

    unknown = _unknown_options(uri)
    if unknown:
        names = ", ".join(sorted(unknown))
        guess = " Did you mean appName?" if any(
            u.lower() not in _KNOWN_OPTIONS and len(u) <= 12 for u in unknown) else ""
        return {
            "ok": False,
            "code": "malformed",
            "message": (f"The URI has an option MongoDB does not support: {names}."
                        f"{guess} Copy the connection string from Atlas again — "
                        "the app's driver rejects unknown options at build time, "
                        "even though this check could otherwise connect."),
            "unknown_options": sorted(unknown),
        }

    database = _database_from_uri(uri)
    try:
        from pymongo import MongoClient
    except ImportError:
        return {
            "ok": False,
            "code": "unavailable",
            "message": "pymongo is not installed. Run setup.ps1 to install the current dependencies.",
        }

    client = None
    try:
        client = MongoClient(
            uri,
            serverSelectionTimeoutMS=timeout_ms,
            connectTimeoutMS=timeout_ms,
            socketTimeoutMS=timeout_ms,
            appname="deployment-agent-check",
        )
        client.admin.command("ping")
        info = client.server_info()
        names: list[str] = []
        try:
            names = sorted(client.list_database_names())
        except Exception:

            names = []

        return {
            "ok": True,
            "code": "connected",
            "message": ("Connected successfully."
                        if database else
                        "Connected, but the URI names no database — everything "
                        "would be written to `test`. Add the database to the "
                        "end of the host, before the ?options."),
            "server_version": str(info.get("version", "")),
            "database": database,
            "database_in_uri": bool(database),
            "databases": names,
        }
    except Exception as exc:
        text = f"{type(exc).__name__}: {exc}"
        lowered = text.lower()
        if any(hint.lower() in lowered for hint in _AUTH_HINTS):
            code, message = "auth", "Authentication failed. Check the username and password in the URI."
        elif "nodename nor servname" in lowered or "name or service not known" in lowered or "dns" in lowered:
            code, message = "dns", "The cluster hostname could not be resolved. Check the cluster address."
        elif any(hint.lower() in lowered for hint in _ALLOWLIST_HINTS):
            code, message = (
                "unreachable",
                "Could not reach the cluster. The most common cause is Atlas Network Access: "
                "add this machine's IP, and after deployment add the EC2 public IP.",
            )
        else:
            code, message = "failed", "Connection failed."
        return {"ok": False, "code": code, "message": message, "detail": redact_text(text)[:400], "database": database}
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

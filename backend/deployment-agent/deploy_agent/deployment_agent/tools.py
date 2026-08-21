from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

from .config import OLLAMA_MODEL, OLLAMA_URL
from .security import redact_text


TOOL_COMMANDS = {
    "git": ["git", "--version"],
    "gh": ["gh", "--version"],
    "aws": ["aws", "--version"],
    "node": ["node", "--version"],
    "vercel": ["vercel", "--version"],
    "ollama": ["ollama", "--version"],
}


def _windows_tool_directories() -> list[str]:
    """Return common per-machine CLI install directories."""
    if os.name != "nt":
        return []
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    local_app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return [
        str(program_files / "Git" / "cmd"),
        str(program_files / "GitHub CLI"),
        str(program_files / "Amazon" / "AWSCLIV2"),
        str(program_files / "nodejs"),
        str(local_app_data / "Programs" / "Ollama"),
        str(local_app_data / "Programs" / "nodejs"),
        str(local_app_data / "GitHubDesktop" / "bin"),

        str(Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "npm"),

        str(program_files / "nodejs" / "bin"),
    ]


def _runtime_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    merged = os.environ.copy()
    if extra:
        merged.update(extra)
    current = merged.get("PATH", "")
    additions = [path for path in _windows_tool_directories() if Path(path).is_dir()]
    if additions:
        merged["PATH"] = os.pathsep.join(additions + ([current] if current else []))
    return merged


def resolve_command(name: str) -> str | None:
    return shutil.which(name, path=_runtime_environment().get("PATH"))


def run_command(
    args: list[str],
    cwd: Path | str | None = None,
    timeout: int = 30,
    env: dict[str, str] | None = None,
    check: bool = False,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = _runtime_environment(env)
    resolved_args = list(args)
    resolved_args[0] = resolve_command(resolved_args[0]) or resolved_args[0]

    if cwd and Path(resolved_args[0]).name.lower() in {"git", "git.exe"}:
        safe_directory = str(Path(cwd).resolve())
        resolved_args[1:1] = ["-c", f"safe.directory={safe_directory}"]
    result = subprocess.run(
        resolved_args,
        cwd=str(cwd) if cwd else None,
        env=merged_env,

        input=input,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )
    result.stdout = redact_text(result.stdout or "")
    result.stderr = redact_text(result.stderr or "")
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"Command failed: {args[0]}")
    return result


def command_exists(name: str) -> bool:
    return resolve_command(name) is not None


def tool_status() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, command in TOOL_COMMANDS.items():
        path = resolve_command(command[0])
        item: dict[str, Any] = {"installed": bool(path), "path": path or "", "version": ""}
        if path:
            try:
                proc = run_command(command, timeout=8)
                item["version"] = (proc.stdout or proc.stderr).strip().splitlines()[0][:160]
                if name == "node":
                    item["ready"] = proc.returncode == 0
            except Exception as exc:
                item["error"] = str(exc)
        result[name] = item

    try:
        from deploy_agent.bridge import ollama_probe
        result["ollama"].update(ollama_probe())
    except Exception:                                           # noqa: BLE001
        try:
            import requests

            response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
            response.raise_for_status()
            names = [model.get("name", "") for model in response.json().get("models", [])]
            result["ollama"]["ready"] = True
            result["ollama"]["model_ready"] = OLLAMA_MODEL in names
        except Exception as exc:
            result["ollama"]["ready"] = False
            result["ollama"]["model_ready"] = False
            result["ollama"]["error"] = str(exc)

    result["ports"] = {"7834_available": _port_available(7834)}
    return result


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def login_command(tool: str, profile: str = "", region: str = "") -> list[str]:
    if tool == "aws-configure":
        args = ["aws", "configure", "sso"]
        if profile:
            args.extend(["--profile", profile])
    elif tool == "aws-console-login":
        args = [
            "aws",
            "login",
            "--profile",
            profile or "deployment-agent",
            "--region",
            region or "ap-south-1",
            "--no-cli-pager",
        ]
    elif tool == "aws-login":
        args = ["aws", "sso", "login"]
        if profile:
            args.extend(["--profile", profile])
    elif tool == "vercel":
        args = ["vercel", "login"]
    elif tool == "github":
        args = [
            "gh",
            "auth",
            "login",
            "--hostname",
            "github.com",
            "--web",
            "--clipboard",
            "--git-protocol",
            "https",
        ]
    elif tool == "ollama":
        args = ["ollama", "signin"]
    else:
        raise ValueError("Unsupported login tool")
    return args


def start_login(tool: str, profile: str = "", region: str = "") -> subprocess.Popen[str]:
    args = login_command(tool, profile, region)
    args[0] = resolve_command(args[0]) or args[0]
    return subprocess.Popen(
        args,
        env=_runtime_environment(),
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )

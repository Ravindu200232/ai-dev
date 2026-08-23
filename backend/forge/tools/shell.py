"""Running things. A short allow-list, a timeout, and a capped transcript."""
from __future__ import annotations

import shlex
import subprocess

from .spec import Param, Tool

TIMEOUT = 600
MAX_OUTPUT = 5_000

# Only the commands a Next.js project actually needs during a build or a QA run.
ALLOWED = {
    "npm": {"install", "i", "ci", "run", "test", "ls", "why", "uninstall"},
    "npx": None,
    "node": None,
}
ALLOWED_SCRIPTS = {"build", "lint", "test", "test:unit", "test:e2e", "typecheck"}

BANNED = {"-g", "--global", "--prefix", "--unsafe-perm"}


def validate(command: str):
    """`(argv, None)` when this may run, `(None, why)` when it may not."""
    text = str(command or "").strip()
    if not text:
        return None, "empty command"
    if any(ch in text for ch in ";|&`$><"):
        return None, "shell operators are not allowed — run one command"
    try:
        argv = shlex.split(text)
    except ValueError as e:
        return None, f"could not parse: {e}"
    if argv[0] not in ALLOWED:
        return None, f"{argv[0]} is not allowed. Allowed: {', '.join(sorted(ALLOWED))}"
    if any(flag in BANNED for flag in argv[1:]):
        return None, "that flag changes the machine, not the project"
    verbs = ALLOWED[argv[0]]
    if verbs is not None:
        if len(argv) < 2 or argv[1] not in verbs:
            return None, f"{argv[0]} {' '.join(argv[1:2])} is not allowed"
        if argv[1] == "run" and argv[2:3] and argv[2] not in ALLOWED_SCRIPTS:
            return None, f"npm run {argv[2]} is not an allowed script"
    return argv, None


def run_command(root, command: str, timeout: int = TIMEOUT) -> str:
    """Run it in the project and hand back what a person would read."""
    argv, refusal = validate(command)
    if refusal:
        return f"$ {command}\nREFUSED: {refusal}"
    try:
        done = subprocess.run(argv, cwd=str(root), capture_output=True,
                              text=True, timeout=int(timeout or TIMEOUT),
                              encoding="utf-8", errors="replace", shell=False)
    except subprocess.TimeoutExpired:
        return f"$ {command}\n[timed out after {timeout}s]"
    except OSError as e:
        return f"$ {command}\n[could not start: {e}]"
    output = ((done.stdout or "") + (done.stderr or "")).strip()
    if len(output) > MAX_OUTPUT:
        # The end of a failing build carries the error; the head carries noise.
        output = f"… {len(output) - MAX_OUTPUT} characters cut …\n" + output[-MAX_OUTPUT:]
    return f"$ {command}\n[exit {done.returncode}]\n{output}"


def shell_tools(root) -> list:
    """The one tool that executes something."""
    return [
        Tool("run_command", "Run an allowed npm/npx/node command in the project.",
             lambda command, timeout=TIMEOUT: run_command(root, command, timeout),
             (Param("command", "For example `npm run build` or `npx vitest run`."),
              Param("timeout", "Seconds to wait.", "integer", False)),
             writes=True, cost="slow"),
    ]

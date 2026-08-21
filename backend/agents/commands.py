"""The agent's command tool."""
import logging
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("commands")

DEFAULT_TIMEOUT = 300
MAX_OUTPUT = 4000
MAX_CALLS = 20
MAX_COMMAND_CHARS = 4096


ALLOWED = {

    "npm": {"install", "i", "add", "uninstall", "remove", "un",
            "ls", "list", "run", "why", "view", "info", "dedupe", "prune",
            "audit"},
    "npx": None,
    "node": None,
    "yarn": {"add", "remove", "install", "list", "why"},
    "pnpm": {"add", "remove", "install", "list", "why"},
}


ALLOWED_NPM_SCRIPTS = {"build", "dev", "start", "lint"}


DANGEROUS = re.compile(
    r"(^|[^\w])(rm|rmdir|del|rd|format|mkfs|dd|shutdown|reboot|kill|taskkill|"
    r"chmod|chown|icacls|reg|sudo|su|curl|wget|iwr|invoke-webrequest|"
    r"powershell|pwsh|cmd|bash|sh|zsh|python|pip|git)([^\w]|$)", re.I)


BANNED_FLAGS = {"--prefix", "-g", "--global", "--ignore-scripts=false",
                "--unsafe-perm", "--allow-same-version"}


_PKG_RE = re.compile(r"^(@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*(@[\w.^~><=*\-+.]+)?$", re.I)


class CommandResult:
    def __init__(self, ok: bool, command: str, output: str, code=None):
        self.ok = ok
        self.command = command
        self.output = output
        self.code = code

    def as_feedback(self) -> str:
        head = f"$ {self.command}\n"
        if not self.ok and self.code is None:
            return head + f"REFUSED: {self.output}"
        status = "exit 0" if self.code == 0 else f"exit {self.code}"
        return head + f"[{status}]\n{self.output}".rstrip()


def _refuse(command: str, why: str) -> CommandResult:
    return CommandResult(False, command, why)


def validate(command: str):
    """Return (argv, None) when the command may run, else (None, reason)."""
    command = (command or "").strip()
    if not command:
        return None, "empty command"
    if len(command) > MAX_COMMAND_CHARS:
        return None, (f"command too long ({len(command)} chars; "
                      f"limit {MAX_COMMAND_CHARS})")
    if "\n" in command or "\r" in command:
        return None, "one command per block — no newlines"

    for ch in ("&", "|", ">", "<", ";", "`", "$("):
        if ch in command:
            return None, (f"'{ch}' is not supported — commands run without a "
                          f"shell. Send one plain command per block.")

    try:
        argv = shlex.split(command, posix=(os.name != "nt"))
    except ValueError as e:
        return None, f"could not parse: {e}"
    if not argv:
        return None, "empty command"

    prog = Path(argv[0]).name.lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if prog.endswith(suffix):
            prog = prog[: -len(suffix)]

    if prog not in ALLOWED:
        return None, (f"'{prog}' is not allowed. Available: "
                      f"{', '.join(sorted(ALLOWED))}.")

    rest = argv[1:]
    if DANGEROUS.search(" ".join(rest)):
        return None, "that command touches the system — refused"

    sub = ALLOWED[prog]
    if sub is not None:
        if not rest:
            return None, f"'{prog}' needs a subcommand ({', '.join(sorted(sub))})"
        if rest[0] not in sub:
            return None, (f"'{prog} {rest[0]}' is not allowed. Allowed: "
                          f"{', '.join(sorted(sub))}.")
        if prog == "npm" and rest[0] == "run":
            script = rest[1] if len(rest) > 1 else ""
            if script not in ALLOWED_NPM_SCRIPTS:
                return None, (f"only these scripts may be run: "
                              f"{', '.join(sorted(ALLOWED_NPM_SCRIPTS))}")

    for token in rest:
        if token.lower() in BANNED_FLAGS:
            return None, f"'{token}' is not allowed"
        if token.startswith("-"):
            continue

        if prog in ("npm", "yarn", "pnpm") and rest[0] in (
                "install", "i", "add", "uninstall", "remove", "un"):
            if token == rest[0]:
                continue
            if not _PKG_RE.match(token):
                return None, (f"'{token}' is not a plain package name — "
                              f"install from the npm registry only")

    return argv, None


class CommandRunner:
    """Executes validated commands inside one project directory."""

    def __init__(self, project_dir: Path, npm_bin: str = "npm",
                 node_bin: str = "node", on_log=None, on_event=None,
                 max_calls: int = MAX_CALLS):
        self.project_dir = Path(project_dir)
        self.npm_bin = npm_bin
        self.node_bin = node_bin
        self.on_log = on_log
        self.on_event = on_event

        self.max_calls = max_calls
        self.calls = 0
        self.history = []

    def _log(self, lvl, txt):
        if self.on_log:
            self.on_log(lvl, txt)
        log.info(txt)

    def _resolve(self, argv: list) -> list:
        """Use the same npm/node AgentForge itself resolved, not whatever is on PATH."""
        prog = Path(argv[0]).name.lower()
        if prog.startswith("npx"):
            return [self._npx()] + argv[1:]
        if prog.startswith("npm"):
            return [self.npm_bin] + argv[1:]
        if prog.startswith("node"):
            return [self.node_bin] + argv[1:]
        return argv

    def _npx(self) -> str:
        """The real `npx` executable."""
        name = "npx.cmd" if os.name == "nt" else "npx"
        try:
            sibling = Path(self.npm_bin).parent / name
            if sibling.is_file():
                return str(sibling)
        except Exception as e:
            log.debug(f"npx beside {self.npm_bin}: {e}")
        return shutil.which("npx") or shutil.which(name) or "npx"

    def run(self, command: str, timeout: int = DEFAULT_TIMEOUT) -> CommandResult:

        argv, reason = validate(command)
        if reason:
            self._log("WARN", f"   ⛔ refused: {command}  ({reason})")
            if self.on_event:
                self.on_event({"command": command, "status": "refused",
                               "output": reason})
            return _refuse(command, reason)

        self.calls += 1
        if self.calls > self.max_calls:

            why = f"command limit ({self.max_calls}) reached for this build"
            self._log("WARN", f"   ⛔ refused: {command}  ({why})")
            if self.on_event:
                self.on_event({"command": command, "status": "refused",
                               "output": why})
            return _refuse(command, why)

        argv = self._resolve(argv)
        self._log("INFO", f"   $ {command}")
        if self.on_event:
            self.on_event({"command": command, "status": "running"})

        lock = None
        if argv and str(argv[0]).lower().split(".")[0] in {"npm", "npx", "yarn", "pnpm"}:
            try:
                from qa_agent.harness import NPM_LOCK, npm_busy
                if npm_busy():
                    self._log("INFO", "   ⏸ waiting for the other npm to finish")
                lock = NPM_LOCK
            except Exception:
                lock = None

        if lock is not None:
            lock.acquire()
        try:
            r = subprocess.run(
                argv, cwd=str(self.project_dir), capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=timeout,
                env={**os.environ, "CI": "true", "NO_COLOR": "1",
                     "FORCE_COLOR": "0", "NPM_CONFIG_FUND": "false",
                     "NPM_CONFIG_AUDIT": "false"},

                shell=False)
        except subprocess.TimeoutExpired:
            out = f"timed out after {timeout}s"
            self._log("WARN", f"   ⛔ {command}: {out}")
            if self.on_event:
                self.on_event({"command": command, "status": "error", "output": out})
            return CommandResult(False, command, out, code=-1)
        except FileNotFoundError:
            out = f"{argv[0]} not found on this machine"
            self._log("WARN", f"   ⛔ {out}")
            if self.on_event:
                self.on_event({"command": command, "status": "error", "output": out})
            return CommandResult(False, command, out, code=-1)
        except Exception as e:
            self._log("WARN", f"   ⛔ {command}: {e}")
            if self.on_event:
                self.on_event({"command": command, "status": "error", "output": str(e)})
            return CommandResult(False, command, str(e), code=-1)
        finally:
            if lock is not None:
                lock.release()

        output = ((r.stdout or "") + ("\n" + r.stderr if r.stderr else "")).strip()
        if len(output) > MAX_OUTPUT:
            output = output[:MAX_OUTPUT] + "\n… (truncated)"
        ok = r.returncode == 0
        self._log("INFO" if ok else "WARN",
                  f"   {'✅' if ok else '❌'} exit {r.returncode}"
                  + (f" — {output.splitlines()[-1][:100]}" if output else ""))
        if self.on_event:
            self.on_event({"command": command,
                           "status": "ok" if ok else "failed",
                           "code": r.returncode, "output": output[:600]})
        res = CommandResult(ok, command, output or "(no output)", r.returncode)
        self.history.append(res)
        return res

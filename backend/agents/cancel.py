"""Stopping a build, at any point, and leaving nothing behind."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from pathlib import Path

__all__ = [
    "BuildCancelled", "begin", "note", "request", "check", "cancelled",
    "track", "finish", "cleanup", "state", "run",
]


class BuildCancelled(BaseException):
    """Raised at the next checkpoint after a cancel was asked for."""


_lock = threading.RLock()
_flag = threading.Event()
_running = False
_project = ""
_srs_id = ""
_procs: set = set()

log = None  # Set by the server so this module never


def _say(level: str, text: str) -> None:
    if log is not None:
        try:
            log(level, text)
        except Exception:
            pass


def begin() -> None:
    """A new run starts: no cancel pending, nothing yet to remove."""
    global _running, _project, _srs_id
    with _lock:
        _flag.clear()
        _procs.clear()
        _running = True
        _project = ""
        _srs_id = ""


def note(project: str = "", srs_id: str = "") -> None:
    """Record what this run would have to remove if it were cancelled."""
    global _project, _srs_id
    with _lock:
        if project:
            _project = str(project)
        if srs_id:
            _srs_id = str(srs_id)


def finish() -> None:
    """The run ended on its own terms."""
    global _running
    with _lock:
        _running = False
        _flag.clear()
        _procs.clear()


def state() -> dict:
    with _lock:
        return {
            "running": _running,
            "cancelling": _flag.is_set(),
            "project": _project,
            "srs_id": _srs_id,
        }


def request() -> dict:
    """Ask the running build to stop, and make the ask land immediately."""
    with _lock:
        if not _running:
            return {"ok": False, "error": "no build is running"}
        _flag.set()
        victims = list(_procs)
        who = {"project": _project, "srs_id": _srs_id}

    for p in victims:
        _kill(p)
    _say("WARN", f"   ⏹ cancel requested — stopping {len(victims)} child process(es)")
    return {"ok": True, **who}


def cancelled() -> bool:
    return _flag.is_set()


def check() -> None:
    """Raise if a cancel is pending."""
    if _flag.is_set():
        raise BuildCancelled()


class track:
    """Context manager that makes one child process killable by `request()`."""

    def __init__(self, proc):
        self.proc = proc

    def __enter__(self):
        if self.proc is not None:
            with _lock:
                _procs.add(self.proc)
            if _flag.is_set():
                _kill(self.proc)
        return self.proc

    def __exit__(self, *exc):
        with _lock:
            _procs.discard(self.proc)
        return False


def _kill(proc) -> None:
    """Kill a child and everything it started."""
    try:
        if proc.poll() is not None:
            return
    except Exception:
        return

    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
        else:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
    except Exception as e:
        _say("WARN", f"   ⚠ could not stop pid {getattr(proc, 'pid', '?')}: {e}")


def cleanup(prod_dir: Path, delete_project=None) -> dict:
    """Remove what the cancelled run produced: the project, and its spec."""
    with _lock:
        project, srs_id = _project, _srs_id

    out = {"project": project, "srs_id": srs_id,
           "project_removed": False, "srs_removed": False}

    if project and delete_project is not None:
        try:
            res = delete_project(project)
            out["project_removed"] = not res.get("error")
            if res.get("error"):
                out["project_error"] = res["error"]
        except Exception as e:
            out["project_error"] = f"{type(e).__name__}: {e}"

    if srs_id:
        staged = Path(prod_dir) / ".srs" / str(srs_id)
        try:
            resolved = staged.resolve()
            resolved.relative_to((Path(prod_dir) / ".srs").resolve())
            if resolved.is_dir():
                shutil.rmtree(resolved, ignore_errors=True)
            out["srs_removed"] = not resolved.exists()
        except (ValueError, OSError) as e:
            out["srs_error"] = f"{type(e).__name__}: {e}"

    return out


def run(argv, *, timeout=None, capture_output=False, check=False, **kw):
    """`subprocess.run`, but the child can be killed from another thread."""
    if capture_output:
        kw.setdefault("stdout", subprocess.PIPE)
        kw.setdefault("stderr", subprocess.PIPE)

    proc = subprocess.Popen(argv, **kw)
    with track(proc):
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(proc)
            try:
                proc.communicate(timeout=10)
            except Exception:
                pass
            raise

    done = subprocess.CompletedProcess(argv, proc.returncode, out, err)
    if check and done.returncode:
        raise subprocess.CalledProcessError(done.returncode, argv, out, err)
    return done

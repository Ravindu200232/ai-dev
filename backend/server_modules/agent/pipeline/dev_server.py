"""Lifecycle for the development server used by the legacy pipeline."""
from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import DEV_PORT, log

active_proc = {"proc": None}
_lifecycle_registered = False


def stop_vite() -> None:
    """Stop the current Vite process without touching unrelated Node processes."""
    proc = active_proc.get("proc")
    if not proc:
        return
    try:
        log.info("🛑 Stopping existing Vite process...")
        if os.name != "nt":
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("⚠ Force killing Vite...")
            if os.name != "nt":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        log.info("   ✅ Vite stopped")
    except Exception as exc:  # Process cleanup must never hide shutdown
        log.warning(f"   ⚠ Could not fully stop Vite: {exc}")
    finally:
        active_proc["proc"] = None


def start_vite(project_dir: Path) -> None:
    """Start one Vite dev server and stream its stdout into the pipeline log."""
    if active_proc.get("proc"):
        stop_vite()
        time.sleep(1)

    project_dir = Path(project_dir)
    log.info(f"🌐 Starting Vite on port {DEV_PORT}...")

    def run() -> None:
        try:
            kwargs = {
                "cwd": project_dir,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
            }
            if os.name != "nt":
                kwargs["preexec_fn"] = os.setsid
            else:
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            proc = subprocess.Popen(
                ["npm", "run", "dev", "--", "--port", str(DEV_PORT), "--host"],
                **kwargs,
            )
            active_proc["proc"] = proc
            for line in proc.stdout or []:
                line = line.strip()
                if line:
                    log.info(f"   [vite] {line}")
        except FileNotFoundError:
            log.error("❌ npm not found! Install Node.js: https://nodejs.org")
        except Exception as exc:
            log.error(f"❌ Vite error: {exc}")
        finally:
            proc = active_proc.get("proc")
            if proc is not None and proc.poll() is not None:
                active_proc["proc"] = None

    threading.Thread(target=run, daemon=True, name="pipeline-vite").start()


def shutdown_backend() -> None:
    log.info("🛑 Backend shutting down...")
    stop_vite()


def handle_signal(_sig, _frame) -> None:
    shutdown_backend()
    raise SystemExit(0)


def register_lifecycle() -> None:
    """Install shutdown hooks once, when the watcher is actually started."""
    global _lifecycle_registered
    if _lifecycle_registered:
        return
    _lifecycle_registered = True
    atexit.register(shutdown_backend)
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

"""Filesystem watcher entrypoint for the legacy idea-file pipeline."""
from __future__ import annotations

import time
from pathlib import Path

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:  # Importing pipeline helpers should not require
    class FileSystemEventHandler:
        pass
    Observer = None

from .config import (BUILD_MODEL, DEV_PORT, IDEAS_DIR, PROD_DIR, REFINE_MODEL,
                     ensure_directories, log)
from .dev_server import active_proc, register_lifecycle
from .runner import run_pipeline


class IdeaFileHandler(FileSystemEventHandler):
    """Debounce `.txt` changes so one save runs one pipeline."""
    def __init__(self):
        self.processing = set()

    def on_created(self, event):
        self._handle(event.src_path)

    def on_modified(self, event):
        self._handle(event.src_path)

    def _handle(self, path):
        item = Path(path)
        if item.suffix.lower() != ".txt" or item in self.processing:
            return
        time.sleep(0.5)
        self.processing.add(item)
        try:
            run_pipeline(item)
        finally:
            self.processing.discard(item)



def _known_types() -> str:
    """The site types the refiner really has, wrapped for the banner."""
    try:
        from agents.refiner import SITE_TYPES
        names = sorted(SITE_TYPES)
    except Exception as e:                                      # noqa: BLE001
        log.debug(f"site types unavailable: {e}")
        return "see agents/refiner.py"
    lines, row = [], ""
    for name in names:
        if len(row) + len(name) + 2 > 58:
            lines.append(row.rstrip(", "))
            row = ""
        row += name + ", "
    lines.append(row.rstrip(", "))
    return ("\n       ").join(lines)


def main() -> None:
    if Observer is None:
        raise RuntimeError("watchdog is required to run the folder watcher pipeline")
    ensure_directories()
    register_lifecycle()
    log.info("🤖 Web Agent Pipeline — React Edition")
    log.info(f"   👁️  Watching : {IDEAS_DIR}")
    log.info(f"   📦 Output   : {PROD_DIR}")
    log.info(f"   🧠 Refiner  : {REFINE_MODEL}")
    log.info(f"   🏗️  Builder  : {BUILD_MODEL}")
    log.info(f"   🌐 Dev URL  : http://localhost:{DEV_PORT}")
    log.info("\nDrop a .txt file into ideas/ to start!")
    log.info("Types: " + _known_types() + "\n")

    handler = IdeaFileHandler()
    observer = Observer()
    observer.schedule(handler, str(IDEAS_DIR), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("\n⛔ Stopping...")
        proc = active_proc.get("proc")
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass
        observer.stop()
    observer.join()

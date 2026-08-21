"""The bytes of the files a fix is about to touch, so a bad fix costs nothing."""
import logging
from pathlib import Path

log = logging.getLogger("qa.snapshot")


class FileSnapshot:
    """Capture, compare, restore."""

    def __init__(self, project_dir):
        self.project_dir = Path(project_dir)
        self._saved = {}

    def capture(self, rels) -> int:
        """Remember the current bytes of each path."""
        n = 0
        for rel in rels or ():
            key = self._key(rel)
            if not key or key in self._saved:
                continue
            fp = self.project_dir / key
            try:
                self._saved[key] = fp.read_bytes() if fp.is_file() else None
                n += 1
            except Exception as e:
                log.warning(f"snapshot: cannot read {key}: {e}")
        return n

    def _key(self, rel):
        return (rel or "").strip().lstrip("./").replace("\\", "/")

    def paths(self) -> list:
        return sorted(self._saved)

    def __len__(self):
        return len(self._saved)

    def changed(self) -> list:
        """Which captured paths differ from what was captured."""
        out = []
        for key, before in self._saved.items():
            fp = self.project_dir / key
            try:
                now = fp.read_bytes() if fp.is_file() else None
            except Exception:
                now = None
            if now != before:
                out.append(key)
        return sorted(out)

    def restore(self, rels=None) -> list:
        """Put the captured bytes back."""
        keys = ([self._key(r) for r in rels] if rels is not None
                else list(self._saved))
        done = []
        for key in keys:
            if key not in self._saved:
                continue
            before = self._saved[key]
            fp = self.project_dir / key
            try:
                now = fp.read_bytes() if fp.is_file() else None
                if now == before:
                    continue
                if before is None:
                    if fp.is_file():
                        fp.unlink()
                else:
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_bytes(before)
                done.append(key)
            except Exception as e:
                log.warning(f"snapshot: cannot restore {key}: {e}")
        return sorted(done)

    def forget(self, rels=None):
        """Accept the current state as final. Called once a round is kept."""
        if rels is None:
            self._saved.clear()
            return
        for rel in rels:
            self._saved.pop(self._key(rel), None)

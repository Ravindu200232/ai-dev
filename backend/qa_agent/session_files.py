"""Focused files responsibilities for QASession."""
from .session_common import *


class QASessionFilesMixin:
    def read_source(self, rel):
        """One generated file, from disk."""
        try:
            fp = self.arch._safe_path(rel) if self.arch else (self.project_dir / rel)
            if not fp.is_file():
                return None
            return fp.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            log.debug(f"read_source {rel}: {e}")
            return None

    def write_test_file(self, rel, content, *, target="", phase=0, tier=0):
        """Write one test, outside `arch.files` entirely."""
        rel = (rel or "").strip().lstrip("./").replace("\\", "/")
        if not TEST_PATH_RE.match(rel):
            self._log("WARN", f"   ⛔ {rel} is not a test path — skipped")
            return False

        target_src = (self.read_source(target) or "") if target else ""
        content = add_helper_imports(
            ensure_mocks(drop_redundant_mocks(content), target_src))
        try:
            fp = self.arch._safe_path(rel) if self.arch else (self.project_dir / rel)
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content.rstrip() + "\n", encoding="utf-8")
        except Exception as e:
            self._log("ERROR", f"   ❌ could not write {rel}: {e}")
            return False

        with self._lock:
            self.manifest[rel] = {"target": target, "phase": phase,
                                  "tier": tier, "stale": False}
            self.report.written = sorted(self.manifest)
        self._save_manifest()
        size = f"{len(content) / 1024:.1f}KB" if len(content) >= 1024 else f"{len(content)}B"
        self._fire("on_file_written", rel, size, content)
        self._log("INFO", f"   🧪 {rel}  ({size})")
        return True

    def _save_manifest(self):
        try:
            d = self.project_dir / QA_DIR
            d.mkdir(parents=True, exist_ok=True)

            with self._lock:
                snapshot = dict(self.manifest)
            (self.project_dir / MANIFEST).write_text(
                json.dumps(snapshot, indent=2), encoding="utf-8")
        except Exception as e:
            log.debug(f"manifest: {e}")

    def load_manifest(self):
        """Restore the manifest when the pipeline reopens a project."""
        try:
            fp = self.project_dir / MANIFEST
            if fp.is_file():
                self.manifest = json.loads(fp.read_text(encoding="utf-8"))
                self.report.written = sorted(self.manifest)
        except Exception as e:
            log.debug(f"manifest load: {e}")
        return self.manifest

    def adopt_orphans(self) -> int:
        """Register test files that are on disk but not in the manifest."""
        found = 0
        for fp in sorted((self.project_dir / "tests" / "unit").rglob("*.test.*")):
            rel = str(fp.relative_to(self.project_dir)).replace("\\", "/")
            if not TEST_PATH_RE.match(rel):
                continue
            entry = self.manifest.get(rel)
            if entry and entry.get("target"):
                continue

            if entry:
                target = self._infer_target(rel)
                if not target:
                    continue
                entry["target"] = target
            else:
                self.manifest[rel] = {"target": self._infer_target(rel),
                                      "phase": 0, "tier": 0, "stale": False,
                                      "adopted": True}
            found += 1
        if found:
            self.report.written = sorted(self.manifest)
            self._save_manifest()
        return found

    def _infer_target(self, test_rel: str) -> str:
        """The app file a test path implies, or '' when nothing matches."""
        stem = test_rel[len("tests/unit/"):].rsplit(".test.", 1)[0]
        if stem.startswith("api/"):
            cand = [f"app/api/{stem[4:]}/route.js"]
        else:
            cand = [f"{stem}.jsx", f"{stem}.js",
                    f"components/{stem.split('/')[-1]}.jsx"]
        for c in cand:
            if (self.project_dir / c).is_file():
                return c
        return ""

    def target_of(self, test_path):
        return (self.manifest.get(test_path) or {}).get("target", "")

    def mark_stale(self, rewritten):
        """Note tests whose subject changed after they were written."""
        touched = {(p or "").lstrip("./").replace("\\", "/") for p in (rewritten or ())}
        n = 0
        for test, meta in self.manifest.items():
            if meta.get("target") in touched:
                meta["stale"] = True
                n += 1
        if n:
            self._save_manifest()
            self._log("INFO", f"   🧪 {n} test(s) marked stale — their target was repaired")
        return n

    def drain(self, timeout: float = 900):
        """Finish the queued work, or give up cleanly."""
        if not self.enabled or not self.arch:
            return self.report
        self.flush_files()
        if not self._q.empty():
            self._ensure_workers()
        deadline = time.time() + max(0.0, timeout)
        while time.time() < deadline:
            if self._q.empty() and not self._busy():
                break
            time.sleep(0.1)
        if not self._q.empty() or self._busy():
            self._cancel.set()
            self._log("WARN", f"   ⏳ QA authoring gave up after {timeout:.0f}s "
                              f"with {self._q.qsize()} batch(es) still queued — "
                              f"the backfill will pick them up, serially")
        for w in self._workers:
            w.join(timeout=5)
        if self.manifest:
            self._log("INFO", f"   🧪 {len(self.manifest)} test file(s) written "
                              f"while the app was being generated")
        return self.report

    def stop(self):
        self._cancel.set()
        for w in self._workers:
            w.join(timeout=5)

    def has_tests(self):
        return bool(self.manifest)



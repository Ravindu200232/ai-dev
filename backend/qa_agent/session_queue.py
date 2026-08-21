"""Focused queue responsibilities for QASession."""
from .session_common import *


class QASessionQueueMixin:
    def _note_file(self, path):
        """A freshly written file until there are enough for one turn."""
        rel = (path or "").strip().lstrip("./").replace("\\", "/")
        if not rel.endswith((".js", ".jsx")):
            return
        with self._lock:
            if rel in self._queued or rel in self._buffer:
                return
            self._buffer.append(rel)
            if len(self._buffer) < MAX_PER_PHASE:
                return
            batch, self._buffer = self._buffer, []
        self._enqueue(self._phase_of(batch[0]), batch)

    def flush_files(self):
        """Queue a part-full batch — a turn ended, or generation is over."""
        with self._lock:
            batch, self._buffer = self._buffer, []
        if batch:
            self._enqueue(self._phase_of(batch[0]), batch)

    def _phase_of(self, rel: str) -> int:
        """Which plan task a file belongs to, for the authoring prompt's "## This phase"."""
        try:
            for i, ph in enumerate(self.arch.plan.get("phases") or [], start=1):
                for f in ph.get("files") or []:
                    got = (f.get("path") if isinstance(f, dict) else f) or ""
                    if got.lstrip("./").replace("\\", "/") == rel:
                        return i
        except Exception:
            pass
        return 0

    def _enqueue(self, phase, files):
        with self._lock:
            files = [f for f in files if f not in self._queued]
            if not files:
                return
            self._queued.update(files)
            self._q.put((phase, list(files)))
        if self.concurrent:
            self._ensure_workers()

    def _ensure_workers(self):
        """Enough workers for what is queued, up to `QA_AUTHOR_WORKERS`."""
        cap = QA_AUTHOR_WORKERS if self.concurrent else 1
        with self._lock:
            self._workers = [w for w in self._workers if w.is_alive()]
            want = min(cap, len(self._workers) + self._q.qsize())
            new = [threading.Thread(target=self._pump, daemon=True,
                                    name=f"agentforge-qa-{i}")
                   for i in range(len(self._workers), want)]
            self._workers.extend(new)
        for w in new:
            w.start()

    def _busy(self) -> bool:
        return any(w.is_alive() for w in self._workers)

    def _pump(self):
        while not self._cancel.is_set():
            try:
                phase, files = self._q.get(timeout=0.25)
            except queue.Empty:
                return
            try:
                self._run_job(phase, files)
            except Exception as e:
                self._log("WARN", f"   ⚠ QA phase {phase}: {e}")
                log.exception("qa job")
            finally:
                self._q.task_done()

    def ensure_runner(self) -> bool:
        """Harness on disk and vitest installed, once per session."""
        if getattr(self, "_runner_ready", False):
            return True
        with self._runner_lock:
            if getattr(self, "_runner_ready", False):
                return True
            from .harness import TestHarness
            try:
                h = TestHarness(self.project_dir, callbacks=self.cb, cmd=self.cmd)
                h.materialise()
                self._runner_ready = bool(h.install())
            except Exception as e:
                self._log("WARN", f"   ⚠ could not prepare the test runner: {e}")
                log.exception("qa: ensure_runner")
                self._runner_ready = False
            return self._runner_ready

    def _run_job(self, phase, files):
        targets = select_targets(files, self.read_source, phase=phase,
                                 already=len(self.manifest),
                                 protected=getattr(self.arch, "NEXT_PROTECTED", None))
        if not targets:
            log.debug(f"qa: phase {phase} has nothing worth testing")
            return
        self._fire("on_phase", {"phase": -14, "title": f"Writing tests — phase {phase}",
                                "status": "active"})
        self.ensure_runner()
        written = self.author.write_for(targets, phase)
        self.jobs_done += 1
        self._fire("on_phase", {"phase": -14, "title": f"Writing tests — phase {phase}",
                                "status": "done", "written": len(written),
                                "files": written})



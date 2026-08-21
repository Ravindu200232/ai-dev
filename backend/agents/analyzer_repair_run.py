"""Analyzer repair flow and runtime pass."""
from .analyzer_common import *

class AnalyzerRepairRunMixin:
    def run(self, *, use_model: bool = True, max_rounds: int = 2,
            semantic: bool = True) -> AnalyzerReport:
        """Scan, repair structural breaks, then prove the planned flows."""
        self._fire("on_phase", {"phase": -5, "title": "Analyzing project",
                                "status": "active"})
        report = self.scan()
        self._log("INFO", f"🔍 Analyzer: {len(report.planned)} files planned, "
                          f"{len(report.routes)} routes served — {report.summary()}")
        for f in report.findings[:8]:
            self._log("WARN", f"   • {f.line()[:150]}")

        first_findings = list(report.findings)
        total_written = 0

        # Package installation changes the truth the scan sees.
        if use_model and self.unresolved_packages():
            pkgs = " ".join(self.unresolved_packages())
            self._log("INFO", f"   📦 Installing {pkgs}")
            self.cmd.run(f"npm install {pkgs}")
            self._files_cache = None
            report = self.scan()

        def structural_targets(rep):
            return [f for f in rep.findings
                    if f.severity == "blocker" or f.code in REPAIRABLE_MAJOR]

        # First make the project structurally trustworthy.
        self._rewritten_this_stage = set()

        rounds = 0
        while use_model and structural_targets(report) and rounds < max_rounds:
            rounds += 1
            targets = structural_targets(report)
            before = len(targets)
            # Repair only the findings that made this round necessary.
            focus = AnalyzerReport()
            focus.findings = list(targets)
            focus.missing = list(report.missing or [])
            n = self.repair(focus)
            if not n:
                break
            total_written += n
            self._log("INFO", f"   ✍️  Wrote {n} file(s) for structural/flow repair")
            self._files_cache = None
            report = self.scan()
            after = len(structural_targets(report))
            if after >= before:
                break

        if semantic and use_model and not report.blockers():
            promises = self.unbuilt_promises()
            if promises:
                report.findings.extend(promises)
                first_findings.extend(promises)
                self._log("INFO", f"   📋 {len(promises)} planned flow promise(s) "
                                  f"are not connected end-to-end")
                for f in promises:
                    self._log("WARN", f"   • {f.line()[:150]}")

                semantic_fix = AnalyzerReport()
                semantic_fix.findings = list(promises)
                semantic_fix.missing = []
                n = self.repair(semantic_fix)
                if n:
                    total_written += n
                    self._log("INFO", f"   🔗 Reconnected {n} file(s) from the "
                                      f"end-to-end flow audit")
                    self._files_cache = None
                    report = self.scan()

                    # Verify the meaning again after the edit.
                    if not report.blockers():
                        remaining = self.unbuilt_promises(max_reads=8)
                        if remaining:
                            report.findings.extend(remaining)
                            self._log("WARN", f"   ⚠ {len(remaining)} semantic "
                                              f"flow issue(s) remain after repair")

        report.written = total_written
        self._fire("on_phase", {"phase": -5, "title": "Analyzing project",
                                "status": "done", "written": report.written})
        self._log("INFO", f"🔍 Analyzer done — {report.summary()}")

        try:
            from . import lessons
            lessons.record(self.project_dir.name,
                           lessons.from_findings(first_findings))
        except Exception as e:                                  # noqa: BLE001
            log.debug(f"lessons: {e}")

        return report

    def run_runtime(self, mongo=None, node_bin: str = "node",
                    use_model: bool = True, dev_log=None,
                    probe_apis: bool = False, skip_root: bool = False) -> AnalyzerReport:
        """The checks that need the app actually running."""
        self._fire("on_phase", {"phase": -5, "title": "Verifying app",
                                "status": "active"})
        report = self.scan()
        self.probe_pages(report, skip_root=skip_root)
        if probe_apis:
            self.probe_api_routes(report)
        self.probe_linked_dynamic_routes(report)
        self.verify_credentials(report)
        self._announce_credentials(report)

        bad_login = [f for f in report.findings if f.code == "BAD_CREDENTIALS"]
        if bad_login and use_model:
            self._log("WARN", "   🔑 Demo login is rejected — repairing the seed")
            seed = next((p for p in self.source_files() if "seed" in p.lower()
                         and p.endswith((".js", ".jsx"))), "lib/seed.js")
            report.missing = []
            for f in bad_login:
                f.path = f.path or seed

            report.findings.append(Finding("blocker", "BAD_CREDENTIALS",
                                           "rewrite the seed so the demo "
                                           "password hashes correctly",
                                           path=seed))
            self.repair(report, server_log=(dev_log() if dev_log else ""))

            if self.allow_reseed and mongo is not None:
                res = mongo.reset_project_db(self.project_dir, node_bin=node_bin)
                if res.get("ok"):
                    self._fire("on_mongo", {"state": "reset",
                                            "db": res.get("db", "")})

                    self._get_status(self.base_url + "/")
                    again = self.scan()
                    self.verify_credentials(again)
                    again.written = report.written
                    report = again
                    if again.credentials.get("ok"):
                        self._log("INFO", "   ✅ Demo login works after re-seed")
                else:
                    self._log("WARN", f"   ⚠ Could not reset the database: "
                                      f"{res.get('error')}")
            elif not self.allow_reseed:
                self._log("WARN", "   ⚠ The seed was corrected, but the existing "
                                  "demo rows block it from running. Reset is "
                                  "disabled for this project.")

        self._fire("on_phase", {"phase": -5, "title": "Verifying app",
                                "status": "done"})
        self._log("INFO", f"🔍 Verification — {report.summary()}")
        return report



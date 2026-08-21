import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import server
from agents.architect_boundaries import ArchitectBoundaryMixin


class TerminalNoiseTests(unittest.TestCase):
    def test_closed_dev_stream_is_not_a_runtime_fault(self):
        text = "\n".join([
            "[next] ⨯ Error: The destination stream closed early.",
            "[next] ⚠ Fast Refresh had to perform a full reload.",
        ])
        self.assertEqual(server.terminal_faults(text), [])

    def test_plain_object_boundary_error_is_actionable(self):
        text = "[next] Only plain objects can be passed to Client Components from Server Components."
        got = server.terminal_faults(text)
        self.assertEqual(len(got), 1)
        self.assertIn("Only plain objects", got[0])


class DynamicRuntimeConfirmationTests(unittest.TestCase):
    def test_dynamic_runtime_finding_reuses_real_link(self):
        finding = server.Finding(
            "blocker", "RUNTIME_EXCEPTION", "serialization failed",
            path="app/rooms/[id]/page.jsx")
        report = server.AnalyzerReport()
        report.runtime_examples = {
            "app/rooms/[id]/page.jsx": ["/rooms/6a8537b61706bb04834a5427"]
        }
        targets = server._runtime_confirmation_targets([finding], report, None)
        self.assertIn(("/rooms/6a8537b61706bb04834a5427",
                       "app/rooms/[id]/page.jsx"), targets)

    def test_plain_object_trace_maps_to_concrete_dynamic_page(self):
        trace = "\n".join([
            "[next] Only plain objects can be passed to Client Components from Server Components.",
            "[next] <... roomId={{buffer: ...}}>",
            "[next] GET /rooms/6a8537b61706bb04834a5427 200 in 9.9s",
        ])
        arch = SimpleNamespace(files={
            "app/rooms/[id]/page.jsx": "export default function Page(){}"
        })
        analyzer = SimpleNamespace(enumerate_routes=lambda: {
            "/rooms/[id]": {"kind": "page", "dynamic": True,
                            "file": "app/rooms/[id]/page.jsx"}
        })
        got = server._runtime_fault_findings(trace, arch, analyzer)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].path, "app/rooms/[id]/page.jsx")
        self.assertIn("/rooms/6a8537b61706bb04834a5427", got[0].extra)

    def test_runtime_stage_reprobes_dynamic_page_after_repair(self):
        bad = server.AnalyzerReport()
        bad.findings = [server.Finding(
            "blocker", "RUNTIME_EXCEPTION",
            "Only plain objects can be passed while serving /rooms/abc",
            path="app/rooms/[id]/page.jsx",
            extra=["app/rooms/[id]/page.jsx", "/rooms/abc"])]
        bad.runtime_examples = {"app/rooms/[id]/page.jsx": ["/rooms/abc"]}

        clean = server.AnalyzerReport()
        clean.routes = {
            "/rooms/[id]": {"kind": "page", "dynamic": True,
                            "file": "app/rooms/[id]/page.jsx"}
        }

        class Analyzer:
            base_url = "http://localhost:5173"
            def __init__(self):
                self.hit = []
            def run_runtime(self, **_kwargs):
                return bad
            def scan(self):
                return clean
            def _get_status(self, url):
                self.hit.append(url)
                return 200

        analyzer = Analyzer()
        arch = SimpleNamespace(files={"app/rooms/[id]/page.jsx": "export default function Page(){}"})
        with patch.object(server, "repair_findings", return_value=1), \
             patch.object(server, "dev_log_mark", return_value=1), \
             patch.object(server, "dev_log_since", return_value=""):
            _report, ok, writes = server.run_runtime_verification_stage(
                arch, Path("."), analyzer, db_ok=True, build_ok=True)
        self.assertTrue(ok)
        self.assertEqual(writes, 1)
        self.assertIn("http://localhost:5173/rooms/abc", analyzer.hit)


class RuntimeRepairEvidenceTests(unittest.TestCase):
    def test_verification_repair_receives_live_server_log(self):
        seen = {}
        class Analyzer:
            def repair(self, _report, server_log=""):
                seen["log"] = server_log
                return 0
        report = server.AnalyzerReport()
        report.findings = [server.Finding(
            "blocker", "RUNTIME_EXCEPTION", "plain object boundary",
            path="app/page.jsx")]
        arch = SimpleNamespace(files={})
        with tempfile.TemporaryDirectory() as td:
            server.repair_findings(
                arch, Path(td), Analyzer(), report, True,
                server_log="Only plain objects can be passed")
        self.assertIn("Only plain objects", seen.get("log", ""))


class BsonBoundaryStaticTests(unittest.TestCase):
    def test_objectid_prop_to_client_is_caught_before_runtime(self):
        files = {
            "app/rooms/[id]/page.jsx": """
                import { getCollection } from '@/lib/mongodb'
                import BookingActions from '@/components/BookingActions'
                export default async function Page(){
                  const rooms = await getCollection('rooms')
                  const room = await rooms.findOne({ slug: 'x' })
                  return <BookingActions roomId={room._id} />
                }
            """,
            "components/BookingActions.jsx": """'use client'\nexport default function BookingActions(){return null}""",
        }
        class Dummy(ArchitectBoundaryMixin):
            def __init__(self):
                self.files = files
            def _next_files(self):
                return list(self.files.items())
        got = Dummy().bson_props_to_client()
        self.assertEqual(len(got), 1)
        self.assertIn("room._id", got[0])
        self.assertIn("ObjectId", got[0])


class E2EUpstreamGateTests(unittest.TestCase):
    def test_source_runtime_fault_does_not_skip_e2e(self):
        runtime = server.AnalyzerReport()
        runtime.findings = [server.Finding(
            "blocker", "RUNTIME_EXCEPTION", "page boundary fault",
            path="app/rooms/[id]/page.jsx")]
        api = server.AnalyzerReport()
        self.assertEqual(server._e2e_hard_upstream_blockers(runtime, api), [])

    def test_stage_failure_still_blocks_e2e(self):
        runtime = server.AnalyzerReport()
        runtime.findings = [server.Finding(
            "blocker", "RUNTIME_STAGE_FAILED", "runtime verifier crashed")]
        api = server.AnalyzerReport()
        got = server._e2e_hard_upstream_blockers(runtime, api)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0], "runtime")


if __name__ == "__main__":
    unittest.main()

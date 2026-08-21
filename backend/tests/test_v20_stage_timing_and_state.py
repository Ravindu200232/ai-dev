"""v20 — stage state that outlived what it described, and time spent serially
that did not have to be.

Four measured faults, one per class: the clean room replayed every journey
against a database the replay before it had consumed; the warm-route set
claimed routes were compiled after the process that compiled them was killed;
the dynamic-DOM cache overwrote itself with nothing; and the picture sweep ran
one request at a time, after the tests, on the pipeline's own thread.
"""
import importlib
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from qa_agent.e2e import E2EAgent


@contextmanager
def patched(module, **names):
    """Swap names in the shared server runtime namespace, then put them back."""
    saved = {k: getattr(module, k, None) for k in names}
    for k, v in names.items():
        setattr(module, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                try:
                    delattr(module, k)
                except Exception:
                    pass
            else:
                setattr(module, k, v)


class CleanRoomReseedTests(unittest.TestCase):
    """Every replay starts from the state its scenario was proven against."""

    def test_each_replay_after_the_first_gets_its_fixtures_back(self):
        server = importlib.import_module("server")
        reseeds, ran = [], []

        journeys = [{"title": f"journey {i}"} for i in range(1, 4)]

        class Agent:
            def accepted_scenario(self, journey):
                return SimpleNamespace(spec_path=lambda: "tests/e2e/x.spec.js")

            def run(self, sc, journey=None):
                ran.append(journey["title"])
                return []

        with TemporaryDirectory() as td:
            with patched(server,
                         _e2e_clean_boot=lambda proj_dir, agent: True,
                         _reseed_for_journey=lambda a, p, n: reseeds.append(n),
                         _runtime_contract_failure=lambda *a, **k: []):
                results = server._e2e_clean_room_once(
                    Agent(), journeys, Path(td))

        self.assertEqual(["journey 1", "journey 2", "journey 3"], ran)
        # One reseed attempt per replay; the helper's own guard drops n == 1.
        self.assertEqual([1, 2, 3], reseeds)
        self.assertEqual({t: [] for t in ran}, results)

    def test_the_reseed_helper_still_skips_the_first_and_the_read_only(self):
        server = importlib.import_module("server")
        with TemporaryDirectory() as td:
            first = server._reseed_for_journey(
                SimpleNamespace(_mutation_ok={"/api/x"}), Path(td), 1)
            read_only = server._reseed_for_journey(
                SimpleNamespace(_mutation_ok=set()), Path(td), 2)

        self.assertFalse(first)
        self.assertFalse(read_only)

    def test_clean_boot_clears_the_mutation_ledger(self):
        source = (Path("server_modules/qa/e2e_final.py")
                  .read_text(encoding="utf-8"))
        boot = source.split("def _e2e_clean_boot")[1].split("\ndef ")[0]
        self.assertIn("_mutation_ok = set()", boot)


class WarmRouteLifetimeTests(unittest.TestCase):
    """A killed dev server has compiled nothing, whatever the set remembers."""

    def test_dropping_the_dom_caches_also_drops_the_warm_routes(self):
        with TemporaryDirectory() as td:
            agent = E2EAgent(SimpleNamespace(project_dir=Path(td), files={},
                                             plan={}), Path(td))
            agent._warmed_routes.add("/rooms")
            agent._runtime_route_cache[("guest", "/rooms")] = "DOM"
            agent.invalidate_runtime_evidence()

        self.assertEqual(set(), agent._warmed_routes)
        self.assertEqual({}, agent._runtime_route_cache)

    def test_every_dev_server_restart_in_the_e2e_path_forgets_them(self):
        stage = (Path("server_modules/qa/e2e_stage.py")
                 .read_text(encoding="utf-8"))
        starts = [i for i, ln in enumerate(stage.splitlines())
                  if "start_next(proj_dir)" in ln]
        self.assertTrue(starts)
        for i in starts:
            window = "\n".join(stage.splitlines()[i:i + 3])
            self.assertIn("_forget_warm(agent)", window)

    def test_the_helper_survives_an_agent_that_has_no_such_method(self):
        server = importlib.import_module("server")
        server._forget_warm(SimpleNamespace())          # must not raise


class DynamicEvidenceCacheTests(unittest.TestCase):
    """A populated record-page inventory is never replaced by an empty one."""

    def test_the_cache_write_is_gated_on_having_harvested(self):
        source = (Path("qa_agent/e2e_context.py").read_text(encoding="utf-8"))
        body = source.split("def runtime_evidence")[1].split("\n    def ")[0]
        write = "self._runtime_dynamic_cache[dyn_key] ="
        self.assertIn(write, body)
        guard = body.split(write)[0].rstrip().splitlines()[-1].strip()
        self.assertEqual("if need_dynamic:", guard)


class PictureSweepTests(unittest.TestCase):
    """Pictures are drawn together, announced once, and off the hot path."""

    FILES = {
        "app/page.jsx": (
            '<img src="/generated/lobby.png" alt="a hotel lobby" />\n'
            '<img src="/generated/spa.png" alt="a spa suite" />\n'
            '<img src="/generated/pool.png" alt="a rooftop pool" />\n'),
    }

    class FakeAgent:
        enabled = True

        def __init__(self):
            self.batches = []

        def available(self):
            return True

        def generate(self, *a, **k):        # pragma: no cover - must not run
            raise AssertionError("pictures must not be drawn one at a time")

        def generate_many(self, jobs, *, on_done=None, **k):
            self.batches.append(list(jobs))
            return {j["key"]: True for j in jobs}

    def _sweep(self):
        server = importlib.import_module("server")
        fake = self.FakeAgent()
        logged = []
        with TemporaryDirectory() as td:
            arch = SimpleNamespace(project_dir=Path(td), files=dict(self.FILES),
                                   plan={"description": "A boutique hotel"})
            with patched(server,
                         image_agent=lambda *a, **k: fake,
                         elog=lambda lvl, txt: logged.append(txt),
                         eprog=lambda *a, **k: None,
                         ephase=lambda *a, **k: None):
                made = server._fill_missing_images(arch, Path(td), "the pages")
        return fake, logged, made

    def test_all_the_missing_pictures_go_out_in_one_batch(self):
        fake, _, made = self._sweep()
        self.assertEqual(3, made)
        self.assertEqual(1, len(fake.batches))
        self.assertEqual({"lobby", "pool", "spa"},
                         {j["key"] for j in fake.batches[0]})

    def test_the_alt_text_and_the_app_idea_become_the_prompt(self):
        fake, _, _ = self._sweep()
        lobby = next(j for j in fake.batches[0] if j["key"] == "lobby")
        self.assertTrue(lobby["prompt"].startswith("a hotel lobby, for "))
        self.assertIn("A boutique hotel", lobby["prompt"])

    def test_a_picture_is_not_announced_twice(self):
        _, logged, _ = self._sweep()
        # ImageAgent.generate owns the per-picture line; this side must not
        # print a second one that reads as a second attempt.
        starts = [t for t in logged if "lobby.png —" in t]
        self.assertEqual([], starts)

    def test_the_sweep_runs_beside_the_build_not_after_the_tests(self):
        pipeline = (Path("server_modules/agent/agent_pipeline.py")
                    .read_text(encoding="utf-8"))
        spawn = pipeline.index('rest = threading.Thread(target=_draw_the_rest')
        unit = pipeline.index("run_qa_unit_stage(")
        self.assertLess(spawn, unit)
        self.assertIn('drawing["rest"] = rest', pipeline)


if __name__ == "__main__":
    unittest.main()

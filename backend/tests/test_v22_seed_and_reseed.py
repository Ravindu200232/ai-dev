"""v22 — the seed's own pictures, and a reseed that actually reseeds.

Two faults from one run, and the second one cost five of six journeys.

The seed asked for `seedImage('room-deluxe')` while the image stage had drawn
`room-1.png`, so every room on the site rendered a 404. And dropping the
database did not make the app seed again -- the generated `ensureSeeded()`
caches its promise for the life of the node process -- so every journey after
the first reseed signed in against an empty user collection and the E2E
debugger spent its rounds on a login page that was correct.
"""
import importlib
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from tests.test_v20_stage_timing_and_state import patched


SEED_JS = """
import { getCollection, seedImage } from '@/lib/mongodb'

async function doSeed() {
  const roomData = [
    { room_type: 'Deluxe Room', room_number: '101', price: 450,
      photos: await seedImage('room-deluxe'), slug: 'deluxe-room' },
    { room_type: 'Executive Suite', room_number: '201', price: 850,
      photos: await seedImage('room-suite'), slug: 'executive-suite' },
  ]
}
"""


class FakeAgent:
    enabled = True

    def __init__(self):
        self.jobs = []

    def available(self):
        return True

    def generate_many(self, jobs, *, on_done=None, **k):
        self.jobs.extend(jobs)
        return {j["key"]: True for j in jobs}


class SeedImageSweepTests(unittest.TestCase):
    """A picture the seed asks for by key is a picture that gets drawn."""

    def _sweep(self, files, plan=None):
        server = importlib.import_module("server")
        fake = FakeAgent()
        with TemporaryDirectory() as td:
            arch = SimpleNamespace(project_dir=Path(td), files=dict(files),
                                   plan=plan or {"description": "A boutique hotel"})
            with patched(server, image_agent=lambda *a, **k: fake,
                         elog=lambda *a, **k: None, eprog=lambda *a, **k: None,
                         ephase=lambda *a, **k: None):
                server._fill_missing_images(arch, Path(td), "the pages")
        return {j["key"]: j["prompt"] for j in fake.jobs}

    def test_every_seed_image_key_is_drawn(self):
        drawn = self._sweep({"lib/seed.js": SEED_JS})
        self.assertEqual({"room-deluxe", "room-suite"}, set(drawn))

    def test_the_row_the_call_sits_in_names_the_picture(self):
        drawn = self._sweep({"lib/seed.js": SEED_JS})
        self.assertTrue(drawn["room-deluxe"].startswith("Deluxe Room,"))
        # The row above must not lend its name to the row below.
        self.assertTrue(drawn["room-suite"].startswith("Executive Suite,"))

    def test_a_key_already_on_disk_is_not_redrawn(self):
        server = importlib.import_module("server")
        fake = FakeAgent()
        with TemporaryDirectory() as td:
            out = Path(td) / "public" / "generated"
            out.mkdir(parents=True)
            (out / "room-deluxe.png").write_bytes(b"x" * 2048)
            arch = SimpleNamespace(project_dir=Path(td),
                                   files={"lib/seed.js": SEED_JS}, plan={})
            with patched(server, image_agent=lambda *a, **k: fake,
                         elog=lambda *a, **k: None, eprog=lambda *a, **k: None,
                         ephase=lambda *a, **k: None):
                server._fill_missing_images(arch, Path(td), "the pages")
        self.assertEqual(["room-suite"], [j["key"] for j in fake.jobs])

    def test_literal_paths_still_work_alongside_seed_keys(self):
        drawn = self._sweep({
            "lib/seed.js": SEED_JS,
            "app/page.jsx": '<img src="/generated/hero.png" alt="a lobby" />'})
        self.assertEqual({"room-deluxe", "room-suite", "hero"}, set(drawn))


class ReseedRestartTests(unittest.TestCase):
    """The database coming back means a process that will put it back."""

    def test_the_reseed_restarts_the_server_before_it_waits(self):
        body = Path("server_modules/qa/e2e_stage.py").read_text(encoding="utf-8")
        reseed = body.split("def _reseed_for_journey")[1].split("\ndef ")[0]
        restart = reseed.index("_restart_for_reseed(proj_dir)")
        wait = reseed.index("_wait_for_seeded_accounts(agent)")
        announce = reseed.index("fixtures reseeded")
        self.assertLess(restart, wait)
        self.assertLess(wait, announce)

    def test_it_no_longer_only_polls_the_shell(self):
        body = Path("server_modules/qa/e2e_stage.py").read_text(encoding="utf-8")
        reseed = body.split("def _reseed_for_journey")[1].split("\ndef ")[0]
        self.assertNotIn("urllib.request.urlopen", reseed)

    def test_a_restart_that_never_comes_back_is_not_fatal(self):
        server = importlib.import_module("server")
        with patched(server, _stop_dev_proc=lambda: None,
                     start_next=lambda p: None, wait_for_next=lambda: False,
                     elog=lambda *a, **k: None):
            self.assertFalse(server._restart_for_reseed(Path(".")))

    def test_a_healthy_restart_warms_the_app_so_the_seed_runs(self):
        server = importlib.import_module("server")
        got = []
        with patched(server, _stop_dev_proc=lambda: None,
                     start_next=lambda p: got.append("start"),
                     wait_for_next=lambda: True,
                     requests=SimpleNamespace(
                         get=lambda url, **k: got.append(url)),
                     elog=lambda *a, **k: None):
            self.assertTrue(server._restart_for_reseed(Path(".")))
        self.assertEqual("start", got[0])
        self.assertTrue(got[1].endswith("/"))


class SeedGuardContractTests(unittest.TestCase):
    """The builder is told to write a seed that survives a wiped database."""

    PROMPT = Path("agents/architect_next_builder_prompt_b.py")

    def test_the_template_rechecks_instead_of_caching_forever(self):
        text = self.PROMPT.read_text(encoding="utf-8")
        block = text.split("export async function ensureSeeded()")[1][:600]
        self.assertIn("estimatedDocumentCount()", block)
        self.assertIn("seeding = null", block)
        self.assertNotIn("if (!seeding) seeding = doSeed()", text)

    def test_the_forbidden_count_is_still_forbidden(self):
        text = self.PROMPT.read_text(encoding="utf-8")
        self.assertIn("countDocuments() > 0", text)
        self.assertIn("NEVER", text)

    def test_the_reason_names_the_measured_failure(self):
        text = self.PROMPT.read_text(encoding="utf-8")
        self.assertIn("User not found", text)
        self.assertIn("sign-in/email 401", text)


SEED_WITH_PEOPLE = """
import { getCollection, seedImage } from '@/lib/mongodb'

async function doSeed() {
  const demoUsers = [
    { email: 'guest@demo.com', password: 'password123', name: 'Guest User', role: 'guest' },
    { email: 'admin@demo.com', password: 'password123', name: 'Admin User', role: 'admin' },
  ]
  const reviews = [
    { name: 'Jane Doe', stars: 5, body: 'Lovely stay' },
  ]
  const rooms = [
    { name: 'Deluxe Room', price: 450 },
  ]
}
"""


class SeededAccountPortraitTests(unittest.TestCase):
    """A demo login is not a thing the app sells, and gets no portrait."""

    def _sweep(self, files, plan=None):
        server = importlib.import_module("server")
        fake = FakeAgent()
        with TemporaryDirectory() as td:
            arch = SimpleNamespace(project_dir=Path(td), files=dict(files),
                                   plan=plan or {})
            with patched(server, image_agent=lambda *a, **k: fake,
                         elog=lambda *a, **k: None, eprog=lambda *a, **k: None,
                         ephase=lambda *a, **k: None):
                server._fill_missing_images(arch, Path(td), "the pages")
        return {j["key"] for j in fake.jobs}

    FILES = {
        "lib/seed.js": SEED_WITH_PEOPLE,
        "components/Avatar.jsx": "<img src={`/generated/${u.name}.png`} />",
    }

    def test_a_row_with_a_login_gets_no_picture(self):
        drawn = self._sweep(self.FILES)
        self.assertNotIn("Guest User", drawn)
        self.assertNotIn("Admin User", drawn)

    def test_a_row_that_is_not_a_login_still_does(self):
        drawn = self._sweep(self.FILES)
        self.assertIn("Deluxe Room", drawn)
        self.assertIn("Jane Doe", drawn)

    def test_a_demo_account_named_in_the_plan_is_excluded_too(self):
        drawn = self._sweep(
            {"lib/seed.js": "const rows = [{ name: 'John Smith' }]",
             "components/Avatar.jsx": "<img src={`/generated/${u.name}.png`} />"},
            plan={"demo_accounts": [{"email": "j@x.io", "password": "pw",
                                    "name": "John Smith"}]})
        self.assertEqual(set(), drawn)


SEED_LOOPED = """
import { getCollection, seedImage } from '@/lib/mongodb'

async function doSeed() {
  const demoUsers = [
    { email: 'doctor@demo.com', password: 'password123', name: 'Dr. Smith', role: 'doctor' },
  ]
  const doctors = [
    { name: 'Dr. Smith', dept: 'Cardiology', slug: 'dr-smith' },
    { name: 'Dr. Sarah Lee', dept: 'Pediatrics', slug: 'dr-lee' },
  ]
  for (const d of doctors) {
    const photo = await seedImage(`doctor-${d.slug}`)
  }
}
"""


class LoopedSeedImageTests(unittest.TestCase):
    """A seed that loops over rows writes its keys as a template."""

    def _sweep(self, files):
        server = importlib.import_module("server")
        fake = FakeAgent()
        with TemporaryDirectory() as td:
            arch = SimpleNamespace(project_dir=Path(td), files=dict(files),
                                   plan={"description": "A clinic"})
            with patched(server, image_agent=lambda *a, **k: fake,
                         elog=lambda *a, **k: None, eprog=lambda *a, **k: None,
                         ephase=lambda *a, **k: None):
                server._fill_missing_images(arch, Path(td), "the pages")
        return {j["key"]: j["prompt"] for j in fake.jobs}

    def test_a_template_key_is_expanded_from_the_seeded_rows(self):
        drawn = self._sweep({"lib/seed.js": SEED_LOOPED})
        self.assertEqual({"doctor-dr-smith", "doctor-dr-lee"}, set(drawn))

    def test_each_row_names_its_own_picture(self):
        drawn = self._sweep({"lib/seed.js": SEED_LOOPED})
        self.assertTrue(drawn["doctor-dr-lee"].startswith("Dr. Sarah Lee,"))
        self.assertTrue(drawn["doctor-dr-smith"].startswith("Dr. Smith,"))

    def test_a_row_that_is_also_a_login_still_gets_its_picture(self):
        # The account exclusion covers `/generated/${u.name}.png` avatars, not
        # a `seedImage` key: Dr. Smith is a doctor the app displays as well as
        # a demo login, and the doctors page would render a 404 without it.
        drawn = self._sweep({"lib/seed.js": SEED_LOOPED})
        self.assertIn("doctor-dr-smith", drawn)

    def test_a_template_over_a_field_nothing_seeds_draws_nothing(self):
        drawn = self._sweep({"lib/seed.js":
                             "await seedImage(`x-${d.missingField}`)"})
        self.assertEqual({}, drawn)


if __name__ == "__main__":
    unittest.main()

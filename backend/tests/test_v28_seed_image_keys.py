"""The doctors-page bug: template image keys that were never drawn."""
import base64
import re
import tempfile
import unittest
from pathlib import Path

from agents.architect_next_scaffold import (ArchitectNextScaffoldMixin,
                                            PLACEHOLDER_PNG_B64)
from agents.seed_keys import family_key, seed_arrays, slugify, template_keys
from qa_agent.debugger_rules import diagnose_broken_images
from qa_agent.e2e_common import CONCEPT_STOPWORDS, singular
from qa_agent.e2e_steps import E2EStepsMixin
from qa_agent.flows import FIELD_CSS, field_css

TPL_RE = re.compile(
    r"seedImage\s*\(\s*`([A-Za-z0-9._-]{0,40})\$\{([^}]{1,60})\}"
    r"([A-Za-z0-9._-]{0,40})`")

# The seed a real build writes: the slug is computed, never spelled out.
DOCTOR_SEED = """
import { getCollection, seedImage } from '@/lib/mongodb'
const slugify = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-')

const DOCTORS = [
  { name: 'Dr. Smith',  specialty: 'Cardiology' },
  { name: 'Dr. Lee',    specialty: 'Dermatology' },
  { name: 'Dr. Ross',   specialty: 'Neurology' },
  { name: 'Dr. Wilson', specialty: 'Paediatrics' },
]
const USERS = [{ email: 'admin@clinic.test', name: 'Admin', role: 'admin' }]

export async function seedDoctors() {
  const col = await getCollection('doctors')
  await col.insertMany(await Promise.all(DOCTORS.map(async (d) => ({
    ...d, slug: slugify(d.name), photo: await seedImage(`doctor-${d.slug}`),
  }))))
}
"""

EXPECTED = {"doctor-dr-smith", "doctor-dr-lee",
            "doctor-dr-ross", "doctor-dr-wilson"}


class Arch:
    def __init__(self, files):
        self.files = files
        self.plan = {}


def resolve(body, arch):
    out = {}
    for m in TPL_RE.finditer(body):
        out.update(template_keys(arch, body, m.start(), *m.groups()))
    return out


class SeedImageTemplateKeyTests(unittest.TestCase):
    def test_derived_slug_yields_every_real_image_key(self):
        keys = resolve(DOCTOR_SEED, Arch({"lib/seed.js": DOCTOR_SEED}))
        self.assertEqual(set(keys), EXPECTED)

    def test_each_key_keeps_the_row_label_for_the_drawing_prompt(self):
        keys = resolve(DOCTOR_SEED, Arch({"lib/seed.js": DOCTOR_SEED}))
        self.assertEqual(keys["doctor-dr-smith"], "Dr. Smith")
        self.assertEqual(keys["doctor-dr-wilson"], "Dr. Wilson")

    def test_account_rows_are_not_swept_into_the_loop(self):
        keys = resolve(DOCTOR_SEED, Arch({"lib/seed.js": DOCTOR_SEED}))
        self.assertFalse([k for k in keys if "admin" in k])
        self.assertIn("USERS", seed_arrays(Arch({"lib/seed.js": DOCTOR_SEED})))

    def test_literal_slugs_still_resolve(self):
        seed = DOCTOR_SEED.replace(
            "{ name: 'Dr. Smith',  specialty: 'Cardiology' },",
            "{ name: 'Dr. Smith', slug: 'dr-smith', specialty: 'Cardiology' },")
        self.assertEqual(set(resolve(seed, Arch({"lib/seed.js": seed}))),
                         EXPECTED)

    def test_it_reads_no_domain_words_at_all(self):
        seed = (DOCTOR_SEED.replace("DOCTORS", "VEHICLES")
                .replace("Dr. Smith", "Blue Van").replace("Dr. Lee", "Red Bus")
                .replace("Dr. Ross", "Grey Cab").replace("Dr. Wilson", "Tow Truck")
                .replace("doctor-", "vehicle-").replace("doctors", "vehicles"))
        self.assertEqual(
            set(resolve(seed, Arch({"lib/seed.js": seed})),),
            {"vehicle-blue-van", "vehicle-red-bus",
             "vehicle-grey-cab", "vehicle-tow-truck"})

    def test_slugify_matches_the_javascript_the_seed_uses(self):
        self.assertEqual(slugify("Dr. Smith"), "dr-smith")
        self.assertEqual(slugify("  Deluxe  Suite / 2  "), "deluxe-suite-2")

    def test_unresolvable_template_falls_back_to_one_shared_key(self):
        body = "rows.map(async (r) => ({ hero: await seedImage(`banner-${r.uuid}`) }))"
        self.assertEqual(resolve(body, Arch({"lib/seed.js": body})), {})
        self.assertEqual(family_key("banner-", ""), "banner")


class PlaceholderImageTests(unittest.TestCase):
    def test_scaffold_writes_a_real_placeholder_png(self):
        class Fake(ArchitectNextScaffoldMixin):
            def __init__(self, d):
                self.project_dir = Path(d)

        with tempfile.TemporaryDirectory() as d:
            fake = Fake(d)
            self.assertTrue(fake.write_placeholder_image())
            self.assertFalse(fake.write_placeholder_image())
            out = Path(d, "public/generated/placeholder.png")
            self.assertTrue(out.is_file())
            self.assertEqual(out.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertGreater(len(base64.b64decode(PLACEHOLDER_PNG_B64)), 100)


class BrokenImageDiagnosisTests(unittest.TestCase):
    def test_image_404s_are_reported_as_an_app_defect(self):
        arch = Arch({"lib/seed.js": DOCTOR_SEED})
        packet = ("HTTP 200 GET http://localhost:5173/doctors\n"
                  "HTTP 404 GET http://localhost:5173/generated/doctor-dr-smith.png\n"
                  "HTTP 404 GET http://localhost:5173/generated/doctor-dr-lee.png")
        decision = diagnose_broken_images(None, arch, [], packet)
        self.assertEqual(decision["verdict"], "APP_FIX")
        self.assertEqual(decision["evidence_rule"], "image-asset-404")
        self.assertIn("doctor-dr-smith", decision["root"])
        self.assertIn("lib/seed.js", decision["files"])

    def test_a_healthy_page_is_left_alone(self):
        arch = Arch({"lib/seed.js": DOCTOR_SEED})
        self.assertIsNone(diagnose_broken_images(
            None, arch, [], "HTTP 200 GET http://localhost:5173/doctors"))


class AppAgnosticE2ETests(unittest.TestCase):
    def test_no_hotel_vocabulary_is_hardcoded_any_more(self):
        self.assertNotIn("room", FIELD_CSS)
        self.assertFalse({"room", "booking", "doctor", "hotel", "patient"}
                         & CONCEPT_STOPWORDS)
        text = Path("qa_agent/e2e_locators.py").read_text(encoding="utf-8")
        self.assertNotIn('("room", ("room",))', text)

    def test_an_unknown_field_still_gets_a_working_selector(self):
        css = field_css("room")
        self.assertIn('input[name*="room" i]', css)
        self.assertIn('select[name*="room" i]', css)
        self.assertTrue(field_css("appointment"))

    def test_concept_words_normalise_any_domain_plural(self):
        self.assertEqual(singular("bookings"), "booking")
        self.assertEqual(singular("categories"), "category")
        self.assertEqual(singular("boxes"), "box")
        self.assertEqual(singular("status"), "status")
        self.assertEqual(E2EStepsMixin._concept_words("Doctors list page"),
                         {"doctor"})
        self.assertEqual(E2EStepsMixin._concept_words("Rooms management"),
                         {"room"})


if __name__ == "__main__":
    unittest.main()

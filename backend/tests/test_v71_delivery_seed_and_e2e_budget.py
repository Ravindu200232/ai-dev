"""A stalled delivery and a role that cannot sign in."""
import importlib
from pathlib import Path
from types import SimpleNamespace

from agents.builder.workflow.delivery import ArchitectDeliveryMixin
ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------- delivery
class _Builder(ArchitectDeliveryMixin):
    """Only what the closure path touches."""

    def __init__(self, missing, thread_writes=False):
        self.on_disk = set()
        self.missing = list(missing)
        self.thread_writes = thread_writes
        self.convo = [{"role": "system", "content": "builder"}]
        self.clean_room_prompts = []
        self.logs = []
        self.plan = {"phases": [{"files": [
            {"path": rel, "kind": "route", "purpose": "reviews API"}
            for rel in missing]}]}

    # -- plumbing the mixin expects -------------------------------------
    def _log(self, level, text):
        self.logs.append((level, text))

    def _on_disk(self, rel):
        return rel in self.on_disk

    def _planned_files(self):
        return list(self.plan["phases"][0]["files"])

    def _file_list_block(self, files):
        return "\n".join(f"  • {f['path']}" for f in files)

    def _contract_ledger(self, wanted=None):
        return "  • api: components/X.jsx —save→ /api/reviews [POST]"

    def _capability_ledger(self, wanted=None):
        return "CAP-009: a customer can leave a review"

    def _related_context_files(self, wanted):
        return {"components/ReviewForm.jsx"}

    def _import_whitelist_block(self):
        return "IMPORTABLE LOCAL MODULES — @/lib/mongodb"

    def _builder_sys(self):
        return "builder"

    def delivery_gaps(self):
        return [f"missing planned file: {rel}"
                for rel in self.missing if rel not in self.on_disk]

    def _run_write_loop(self, user_content, _tool_depth=0):
        if user_content.startswith("Write exactly ONE file"):
            self.clean_room_prompts.append(user_content)
            self.on_disk.update(self.missing)
            self.missing = []
            return 1
        return 0                                 # the shared thread has stalled


def test_gap_paths_reads_every_deterministic_gap_phrasing():
    builder = _Builder([])
    gaps = [
        "missing planned file: app/api/reviews/route.js",
        "CAP-009 missing capability file: app/api/reviews/route.js",
        "planned file is still scaffold placeholder: app/page.jsx",
        "CAP-009 has e2e=true but no workflow covers it",
    ]

    assert builder.gap_paths(gaps) == ["app/api/reviews/route.js",
                                       "app/page.jsx"]


def test_a_stalled_build_thread_falls_through_to_a_clean_room_per_file():
    builder = _Builder(["app/api/reviews/route.js"])

    remaining = builder.close_delivery_gaps(max_rounds=2)

    assert remaining == []
    assert builder._on_disk("app/api/reviews/route.js")
    assert len(builder.clean_room_prompts) == 1
    prompt = builder.clean_room_prompts[0]
    assert "app/api/reviews/route.js" in prompt
    assert "read_file tool" in prompt
    assert "components/ReviewForm.jsx" in prompt
    assert "CAP-009" in prompt


def test_the_clean_room_never_leaks_its_conversation_into_the_build_thread():
    builder = _Builder(["app/api/reviews/route.js"])
    before = list(builder.convo)

    builder.write_one_file_clean_room("app/api/reviews/route.js")

    assert builder.convo == before


def test_a_gap_the_clean_room_cannot_close_is_repaired_later_not_aborted():
    source = (ROOT / "agents" / "builder" / "workflow"
              / "delivery.py").read_text(encoding="utf-8")
    run_body = source.split("    def run(")[1].split("\n    def ")[0]

    # A nearly complete app is no longer thrown away at this seam.
    assert "Refusing a partial delivery" not in run_body
    assert "self.delivery_gaps_left = list(remaining)" in run_body
    assert "handing them to the" in run_body
    # The analyzer is the pass that closes it, and it already knows how.
    analyzer = (ROOT / "agents" / "gates"
                / "analyzer_workflows.py").read_text(encoding="utf-8")
    assert '"blocker", "MISSING_FILE"' in analyzer


# ------------------------------------------------------------- seeded auth
def _server():
    return importlib.import_module("server")


def test_every_role_is_probed_not_only_the_first(monkeypatch):
    server = _server()
    tried = []

    def probe(email, password, timeout=8.0):
        tried.append(email)
        return True, ""

    monkeypatch.setattr(server, "_probe_sign_in", probe)
    monkeypatch.setattr(server, "elog", lambda *a, **k: None)
    agent = SimpleNamespace(accounts=lambda: [
        {"email": "a@x.io", "password": "p", "role": "cashier"},
        {"email": "b@x.io", "password": "p", "role": "store_manager"},
        {"email": "a@x.io", "password": "p", "role": "cashier"},
    ])

    assert server._wait_for_seeded_accounts(agent, timeout=5) is True
    assert sorted(tried) == ["a@x.io", "b@x.io"]
    assert agent._seed_account_report["broken"] == {}


def test_one_account_signing_in_proves_the_seed_ran_and_stops_the_wait(monkeypatch):
    server = _server()
    calls = {"n": 0}
    said = []

    def probe(email, password, timeout=8.0):
        calls["n"] += 1
        if email == "cashier@x.io":
            return True, ""
        return False, "the account exists but the seeded password does not match it"

    monkeypatch.setattr(server, "_probe_sign_in", probe)
    monkeypatch.setattr(server, "elog", lambda lvl, txt: said.append(txt))
    agent = SimpleNamespace(accounts=lambda: [
        {"email": "cashier@x.io", "password": "p", "role": "cashier"},
        {"email": "manager@x.io", "password": "wrong", "role": "store_manager"},
    ])

    assert server._wait_for_seeded_accounts(agent, timeout=30) is False
    # No retry loop: the seed demonstrably ran, so waiting proves nothing.
    assert calls["n"] == 2
    assert "manager@x.io" in agent._seed_account_report["broken"]
    assert any("store_manager account manager@x.io cannot sign in" in t
               for t in said)


def test_a_role_that_cannot_sign_in_becomes_a_repairable_seed_finding():
    server = _server()
    agent = SimpleNamespace(_seed_account_report={
        "ready": ["cashier@x.io"],
        "broken": {"manager@x.io": "no account with that email exists yet"},
        "roles": {"cashier@x.io": "cashier", "manager@x.io": "store_manager"},
    })

    findings = server._seed_account_findings(agent)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "blocker"
    assert finding.code == "SEED_ACCOUNT"
    assert finding.path == "lib/seed.js"
    assert "store_manager <manager@x.io>" in finding.message
    assert "sign-up API" in finding.fix


def test_the_seed_finding_names_whatever_this_project_called_its_seed():
    server = _server()
    agent = SimpleNamespace(
        arch=SimpleNamespace(files={"lib/db/bootstrap-seed.js": "",
                                    "app/page.jsx": ""}),
        _seed_account_report={
            "ready": [], "broken": {"a@x.io": "no account with that email exists yet"},
            "roles": {"a@x.io": "admin"}})

    finding = server._seed_account_findings(agent)[0]

    # No stack-wide guess: the path comes from what the app actually wrote.
    assert finding.path == "lib/db/bootstrap-seed.js"
    assert "lib/db/bootstrap-seed.js" in finding.fix


def test_the_first_journey_proves_the_accounts_before_it_walks():
    prepare = (ROOT / "server_modules" / "qa"
               / "e2e_prepare.py").read_text(encoding="utf-8")
    body = prepare.split("def _prepare_app_before_journeys")[1].split("\ndef ")[0]
    assert "_ensure_every_role_can_sign_in(agent, arch, proj_dir, analyzer)" in body

    ensure = prepare.split("def _ensure_every_role_can_sign_in")[1].split("\ndef ")[0]
    # Repair the seed, empty the database, restart, then prove it again.
    assert (ensure.index("_repair_seeded_accounts")
            < ensure.index("reset_project_db")
            < ensure.index("_restart_for_reseed"))


def test_a_reseed_compiles_the_routes_while_it_waits_for_the_seed():
    stage = (ROOT / "server_modules" / "qa"
             / "e2e_stage.py").read_text(encoding="utf-8")
    reseed = stage.split("def _reseed_for_journey")[1].split("\ndef ")[0]
    assert (reseed.index("_restart_for_reseed(proj_dir)")
            < reseed.index("_warm_routes_async(agent)")
            < reseed.index("_wait_for_seeded_accounts(agent)")
            < reseed.index("fixtures reseeded"))

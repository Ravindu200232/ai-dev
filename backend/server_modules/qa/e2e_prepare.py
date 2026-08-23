# Read and repair the app that shipped

def _analyze_before_journeys(analyzer) -> int:
    """One static pass over the app before any journey is written."""
    try:
        scanned = analyzer.scan()
    except Exception as e:                                 # noqa: BLE001
        log.debug(f"pre-journey analysis scan: {e}")
        return 0

    findings = [f for f in (getattr(scanned, "findings", None) or [])
                if f.severity == "blocker" or f.code in REPAIRABLE_MAJOR]
    if not findings:
        elog("INFO", "   🔎 pre-journey analysis — nothing to fix first")
        return 0

    codes = ", ".join(sorted({f.code for f in findings}))
    elog("INFO", f"   🔎 pre-journey analysis found {len(findings)} "
                 f"issue(s) before any journey runs — {codes}")

    report = AnalyzerReport()
    report.findings = findings
    report.missing = []
    try:
        written = analyzer.repair(report) or 0
    except Exception as e:                                 # noqa: BLE001
        elog("WARN", f"   ⚠ pre-journey repair failed: {e}")
        log.exception("pre-journey repair")
        return 0

    if written:
        elog("INFO", f"   🔧 repaired {written} file(s) before the journeys")
    return written


def _prepare_app_before_journeys(agent, arch, proj_dir, analyzer):
    """Load the shipped app, repair it, load it again."""
    try:
        arch.load_existing()
    except Exception as e:
        log.debug(f"pre-E2E source refresh: {e}")

    paths = [p for p in arch.files
             if p.startswith(("app/", "components/", "lib/"))]
    baseline = FileSnapshot(proj_dir)
    baseline.capture(paths)

    if _analyze_before_journeys(analyzer):
        try:
            arch.load_existing()
        except Exception as e:
            log.debug(f"post-repair source refresh: {e}")
        try:
            agent.invalidate_runtime_evidence()
        except Exception as e:
            log.debug(f"post-repair E2E evidence reset: {e}")
    _ensure_every_role_can_sign_in(agent, arch, proj_dir, analyzer)
    return paths, baseline


def _ensure_every_role_can_sign_in(agent, arch, proj_dir, analyzer) -> bool:
    """Prove each role's demo identity before the first journey needs it.

    A journey that opens with a 401 costs a whole repair budget diagnosing the
    app, when the defect is that `lib/seed.js` never created that role's
    account. Establishing it here turns that into one seed repair.
    """
    if _wait_for_seeded_accounts(agent, timeout=30.0):
        return True
    if not _repair_seeded_accounts(agent, arch, analyzer):
        return False

    # The old, unusable identities are still in the database; only an empty
    # database makes the repaired seed run again.
    try:
        if getattr(MONGO, "available", False):
            MONGO.reset_project_db(proj_dir, node_bin=NODE_BIN)
    except Exception as e:                                     # noqa: BLE001
        log.debug(f"seed-repair database reset: {e}")
    if not _restart_for_reseed(proj_dir):
        return False
    _forget_warm(agent)
    _warm_routes_async(agent)
    try:
        agent.invalidate_runtime_evidence()
    except Exception as e:                                     # noqa: BLE001
        log.debug(f"post seed-repair E2E evidence reset: {e}")
    return _wait_for_seeded_accounts(agent, timeout=30.0)
def _forget_warm(agent):
    """Every route is cold again after the dev server is restarted."""
    try:
        agent.forget_warmed_routes()
    except Exception as e:
        log.debug(f"forget warmed routes: {e}")


SEED_PROBE_TIMEOUT = 8.0


def _seed_account_roster(agent) -> list:
    """Every distinct demo identity a journey may sign in as."""
    try:
        accounts = agent.accounts() or []
    except Exception as e:                                     # noqa: BLE001
        log.debug(f"seeded account probe: {e}")
        return []
    roster, seen = [], set()
    for account in accounts:
        if not isinstance(account, dict):
            continue
        email = str(account.get("email") or "").strip()
        password = str(account.get("password") or "")
        key = email.casefold()
        if not email or not password or key in seen:
            continue
        seen.add(key)
        roster.append({"role": str(account.get("role") or "").strip(),
                       "email": email, "password": password})
    return roster


def _credential_reason(detail: str) -> str:
    """Name what the auth endpoint actually objected to."""
    text = str(detail or "").lower()
    if "user not found" in text or "user_not_found" in text:
        return "no account with that email exists yet"
    if "invalid password" in text or "invalid_password" in text:
        return "the account exists but the seeded password does not match it"
    if "not verified" in text or "verification" in text:
        return "the account exists but sign-in is gated behind email verification"
    return "the app refused these credentials"


def _probe_sign_in(email: str, password: str,
                   timeout: float = SEED_PROBE_TIMEOUT) -> tuple:
    """One real POST to the app's own sign-in endpoint: (signed_in, why)."""
    import json as _json
    import urllib.error
    import urllib.request

    body = _json.dumps({"email": email, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{DEV_PORT}/api/auth/sign-in/email",
        data=body, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(512)
            return True, ""
    except urllib.error.HTTPError as e:
        try:
            detail = e.read(400).decode("utf-8", "replace")
        except Exception:                                      # noqa: BLE001
            detail = ""
        if e.code in (400, 401, 403):
            return False, _credential_reason(detail)
        return False, f"the endpoint answered HTTP {e.code}"
    except Exception as e:                                     # noqa: BLE001
        return False, f"the endpoint was unreachable ({type(e).__name__})"


def _probe_roster(roster: list) -> list:
    """Probe every identity at once — one slow account never serialises the rest."""
    if not roster:
        return []
    if len(roster) == 1:
        account = roster[0]
        ok, why = _probe_sign_in(account["email"], account["password"])
        return [(account, ok, why)]
    from concurrent.futures import ThreadPoolExecutor

    def one(account):
        ok, why = _probe_sign_in(account["email"], account["password"])
        return (account, ok, why)

    with ThreadPoolExecutor(max_workers=min(6, len(roster))) as pool:
        return list(pool.map(one, roster))


def _wait_for_seeded_accounts(agent, timeout: float = 40.0) -> bool:
    """Block until EVERY demo identity can sign in, or the budget runs out.

    Probing one account could only ever prove that the seed had started. A
    role whose account the seed never created, or created with a password the
    seed did not declare, used to surface much later as an unexplained 401 in
    the middle of a journey.
    """
    roster = _seed_account_roster(agent)
    if not roster:
        return False

    by_email = {a["email"]: a for a in roster}
    pending = dict(by_email)
    ready, broken = {}, {}
    deadline = time.time() + max(1.0, float(timeout or 0))
    waited, delay = False, 0.25

    while pending and time.time() < deadline:
        results = _probe_roster(list(pending.values()))
        for account, ok, _why in results:
            if ok:
                ready[account["email"]] = pending.pop(account["email"], account)
        if not pending:
            break
        if ready:
            # Something signed in, so the seed has finished running. Whatever
            # is still refused is a defect in the seed, not a slow start, and
            # waiting on it only burns the journey's budget.
            for account, ok, why in results:
                if not ok and account["email"] in pending:
                    broken[account["email"]] = why
                    pending.pop(account["email"], None)
            break
        waited = True
        time.sleep(delay)
        delay = min(2.0, delay * 1.6)

    for email in list(pending):
        broken.setdefault(email, "the app never accepted it before this "
                                 "journey had to start")

    report = {
        "ready": sorted(ready),
        "broken": {e: broken[e] for e in sorted(broken)},
        "roles": {e: by_email[e]["role"] for e in by_email},
    }
    try:
        agent._seed_account_report = report
    except Exception:                                          # noqa: BLE001
        pass

    if ready and waited and not broken:
        elog("INFO", "   ♻ the seeded accounts are back — "
                     f"{len(ready)} identity(ies) can sign in")
    for email, why in report["broken"].items():
        role = by_email[email]["role"] or "demo"
        elog("WARN", f"   ⚠ the seeded {role} account {email} cannot sign "
                     f"in — {why}"
                     + (f". {len(ready)} other account(s) can, so the seed did "
                        "run: this is lib/seed.js, not a slow start"
                        if ready else
                        ". No demo account signed in at all, so the seed has "
                        "not created the identities the journeys need"))
    return bool(ready) and not broken


def _seed_source_path(agent) -> str:
    """Whatever this project actually called its seed module."""
    files = getattr(getattr(agent, "arch", None), "files", None) or {}
    candidates = [rel for rel in files
                  if "seed" in str(rel).rsplit("/", 1)[-1].lower()
                  and str(rel).endswith((".js", ".jsx", ".mjs"))]
    for rel in sorted(candidates, key=lambda r: (r.count("/"), len(r))):
        return rel
    return "lib/seed.js"


def _seed_account_findings(agent) -> list:
    """Turn refused demo identities into repairable analyzer findings."""
    report = getattr(agent, "_seed_account_report", None) or {}
    broken = report.get("broken") or {}
    if not broken:
        return []
    roles = report.get("roles") or {}
    rows = ", ".join(f"{roles.get(email) or 'demo'} <{email}> ({why})"
                     for email, why in broken.items())
    seed = _seed_source_path(agent)
    return [Finding(
        severity="blocker", code="SEED_ACCOUNT", path=seed,
        message=("the seeded sign-in identity for "
                 f"{len(broken)} role(s) cannot sign in: {rows}"),
        fix=(f"In `{seed}`, create every demo identity through the auth "
             "library's own sign-up API rather than inserting a user row — "
             "only that path produces a credential the app's own sign-in will "
             "accept. One call per role, each with its own unique email and "
             "the exact password literal the seed declares, then set that "
             "row's role with an update against the user collection the auth "
             "library created. A direct insert, one identity reused across "
             "roles, or a password the seed hashes itself all produce an "
             "account that exists and can never sign in."))]


def _repair_seeded_accounts(agent, arch, analyzer) -> int:
    """Fix the seed when a role's identity cannot sign in, before journeys run."""
    findings = _seed_account_findings(agent)
    if not findings or analyzer is None:
        return 0
    report = AnalyzerReport()
    report.findings = findings
    report.missing = []
    try:
        written = analyzer.repair(report) or 0
    except Exception as e:                                     # noqa: BLE001
        elog("WARN", f"   ⚠ seed-account repair failed: {e}")
        log.exception("seed account repair")
        return 0
    if not written:
        elog("WARN", "   ⚠ the seed still cannot produce a signed-in "
                     "identity for every role — the journeys for those roles "
                     "will report what they find")
        return 0
    elog("INFO", f"   🔑 repaired {written} file(s) so every role has "
                 "an identity it can sign in with")
    try:
        arch.load_existing()
    except Exception as e:                                     # noqa: BLE001
        log.debug(f"post seed-repair source refresh: {e}")
    return written


def _restart_for_reseed(proj_dir) -> bool:
    """Restart `next dev` so the app will seed the empty database again."""
    try:
        _stop_dev_proc()
    except Exception as e:
        log.debug(f"reseed restart stop: {e}")
    try:
        start_next(proj_dir)
    except Exception as e:
        log.debug(f"reseed restart start: {e}")
        return False
    if not wait_for_next():
        elog("WARN", "   ⚠ the dev server did not come back after the "
                     "reseed — this journey will report what it finds")
        return False
    try:
        # The seed runs on the first cold request, not on boot.
        requests.get(f"http://localhost:{DEV_PORT}/", timeout=30)
    except Exception as e:
        log.debug(f"reseed warm request: {e}")
    return True


def _warm_routes_async(agent, limit: int = 8):
    """Compile the routes the stage is about to open, off the critical path."""
    try:
        routes = [u for u, m in sorted(agent.route_map().items())
                  if m.get("kind") == "page" and "[" not in u][:limit]
    except Exception as e:
        log.debug(f"async warm routes: {e}")
        return None
    if not routes:
        return None

    def run():
        from concurrent.futures import ThreadPoolExecutor
        def fetch(path):
            try:
                requests.get(f"http://localhost:{DEV_PORT}{path}", timeout=60)
                agent._warmed_routes.add(path)
            except Exception as e:                             # noqa: BLE001
                log.debug(f"async warm {path}: {e}")
        with ThreadPoolExecutor(max_workers=min(6, len(routes))) as pool:
            list(pool.map(fetch, routes))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t

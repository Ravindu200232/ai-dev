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
            log.debug(f"runtime evidence invalidate: {e}")

    return paths, baseline
def _forget_warm(agent):
    """Every route is cold again after the dev server is restarted."""
    try:
        agent.forget_warmed_routes()
    except Exception as e:
        log.debug(f"forget warmed routes: {e}")


def _wait_for_seeded_accounts(agent, timeout: float = 40.0) -> bool:
    """Block until a demo account can actually sign in, or the budget runs out."""
    try:
        accounts = agent.accounts() or []
    except Exception as e:
        log.debug(f"seeded account probe: {e}")
        return False
    account = next((a for a in accounts
                    if a.get("email") and a.get("password")), None)
    if not account:
        return False

    import json as _json
    import urllib.error
    import urllib.request

    body = _json.dumps({"email": account["email"],
                        "password": account["password"]}).encode("utf-8")
    deadline = time.time() + timeout
    waited = False
    while time.time() < deadline:
        req = urllib.request.Request(
            f"http://127.0.0.1:{DEV_PORT}/api/auth/sign-in/email",
            data=body, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                r.read(512)
                if waited:
                    elog("INFO", "   ♻ the seeded accounts are back")
                return True
        except urllib.error.HTTPError as e:
            if e.code != 401:
                return True                # Seeded; this is a different answer
        except Exception:
            pass
        waited = True
        time.sleep(1.0)
    elog("INFO", "   ♻ the demo accounts did not come back before this "
                 "journey started — it will report what it finds")
    return False


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

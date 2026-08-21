# Chat-driven bug fixes for an existing project.
def bind_host() -> str:
    """The address the servers listen on."""
    return "0.0.0.0" if load_settings().get("lan_access") else "127.0.0.1"


def db_ok() -> bool:
    """Is MongoDB usable right now?"""
    try:
        return bool(MONGO.available)
    except Exception:
        return False


ROUTER_SYSTEM = """\
You are reading one message a user typed about an app they are looking at, and
deciding which tool should handle it. You are NOT fixing anything.

Answer with ONE line and nothing else:

INTENT :: bug :: <one sentence naming what is broken>
INTENT :: feature :: <one sentence naming what to add>
INTENT :: page :: <one sentence naming what should change on this page>
INTENT :: ask :: <the question they are asking>

How to choose:

  • bug — something is broken, erroring, blank, missing, wrong, or not doing
    what it should. "the login does nothing", "this page is white", "prices
    show as NaN", "it crashes when I click save".
  • feature — they want something the app does not do yet. "add reviews",
    "let staff export a CSV", "I need a search box", "make an admin page".
  • page — a change to how the page they are ON looks or reads: layout,
    spacing, wording, colour, order, adding or removing a section. "make this
    two columns", "the heading should say Built With", "move the form up".
  • ask — a question about the app rather than a request to change it. "where
    is the seed data", "what are the demo logins", "how does auth work".

When it could be two, prefer in this order: bug, then page, then feature. A
report that something looks wrong is a bug before it is a restyle, and a
restyle of the current page is cheaper and safer than a feature.
"""

INTENT_RE = re.compile(r"^\s*INTENT\s*::\s*(bug|feature|page|ask)"
                       r"\s*(?:::\s*(.*))?$", re.I | re.M)


def classify_intent(arch, text: str, route: str = "") -> tuple:
    """`(intent, restated)` for one message the user typed."""
    user = (f"The user is looking at {route or 'the app'}.\n\n"
            f"They typed:\n{text}\n\nAnswer with the INTENT line.")
    buf = []
    try:
        arch._stream([{"role": "system", "content": ROUTER_SYSTEM},
                      {"role": "user", "content": user}],
                     buf.append, temperature=0.0, timeout=60)
    except Exception as e:
        log.warning(f"intent router failed: {e}")
        return "feature", text
    m = INTENT_RE.search("".join(buf))
    if not m:
        return "feature", text
    return m.group(1).lower(), (m.group(2) or text).strip()


def run_chat(proj_name: str, text: str, model: str, route: str = "",
             think: bool = None, qa_model: str = "", console: str = ""):
    """One input for everything: read what the user wants, then do it."""
    set_tester_emit(emit)
    proj_dir = PROD_DIR / proj_name
    if not proj_dir.is_dir():
        return eerr(f"Project not found: {proj_name}")
    if not ensure_model(model):
        return eerr(f"Cannot load model: {model}")

    arch = ArchitectAgent(ollama, model, proj_dir, _agent_callbacks(proj_dir),
                          stack=detect_stack(proj_dir), think=think)
    intent, restated = classify_intent(arch, text, route)
    elog("INFO", f"💬 {intent} — {restated[:80]}")
    emit({"type": "chat_intent", "intent": intent, "summary": restated})

    if intent == "ask":
        try:
            run_question(arch, proj_dir, text)
            edone(f"http://localhost:{DEV_PORT}", proj_name)
        finally:
            stop_model(model)
        return
    if intent == "bug":
        return run_bug_report(proj_name, text, model, route, think, qa_model,
                              console=console)
    if intent == "page" and route:
        return run_page_update(proj_name, text, model, route, think)

    return run_feature(proj_name, text, model, think, qa_model, route=route)


def _reproduce_complaint(proj_dir: Path, route: str, complaint: str, analyzer):
    """Open the app as a browser and watch the reported thing fail."""
    from agents.reproduce import Reproduction, reproduce

    if not _dev_alive():
        return Reproduction(route=route or "/", why_not="the dev server is not running")

    login, endpoint = None, ""
    try:
        endpoint = analyzer.find_login_endpoint() or ""
        accounts = (analyzer.arch.plan or {}).get("demo_accounts") or []
        if accounts and endpoint:
            first = accounts[0]
            login = (first.get("email"), first.get("password"))
    except Exception as e:                                      # noqa: BLE001
        log.debug(f"demo login for reproduce: {e}")

    seen = reproduce(route or "/", complaint, port=DEV_PORT,
                     login=login, login_endpoint=endpoint)
    if not seen.ran:
        elog("INFO", f"   🔎 Could not open it in a browser — {seen.why_not}")
        return seen

    found = (len(seen.console) + len(seen.page_errors) + len(seen.network))
    elog("INFO", f"   🔎 Opened {seen.route} in a browser"
                 + (" (signed in)" if seen.signed_in else "")
                 + (f" — {found} thing(s) went wrong" if found
                    else " — the browser reported nothing"))
    if seen.filled:
        elog("INFO", f"   ⌨ Filled {len(seen.filled)} required field(s) first "
                     f"— {', '.join(seen.filled[:4])}")
    if seen.clicked:
        elog("INFO", f"   🖱 Clicked '{seen.clicked}' — "
                     + ("the page changed" if seen.changed else "nothing happened"))
    return seen


def _report_symptom(before, after) -> bool:
    """Say whether the thing they reported still happens."""
    if not (before.ran and after.ran):
        elog("INFO", "   ↻ Could not re-check it in a browser — the change is "
                     "in, but nobody has confirmed the symptom is gone")
        return False

    was, now = before.signature(), after.signature()
    gone, left, fresh = was - now, was & now, now - was

    if left:
        elog("WARN", "   ✗ The app builds, but what you reported is STILL "
                     "happening:")
        for line in sorted(left)[:3]:
            elog("WARN", f"      {line[:150]}")
    elif was:
        elog("INFO", f"   ✓ The symptom is gone — {len(gone)} fault(s) that "
                     f"happened before this change do not happen now")
    elif before.clicked and not before.changed and after.changed:
        elog("INFO", f"   ✓ '{before.clicked}' does something now")
    else:
        elog("INFO", "   ↻ Re-checked in a browser; it was quiet before and "
                     "after, so this one is for you to confirm")

    if fresh:
        elog("WARN", f"   ⚠ {len(fresh)} new fault(s) that were not there "
                     f"before this change:")
        for line in sorted(fresh)[:3]:
            elog("WARN", f"      {line[:150]}")
    return bool(was) and not left


def _normalise_fault_line(line: str) -> str:
    text = " ".join(str(line or "").replace("\\", "/").split())
    text = re.sub(r"\b[0-9a-fA-F]{24}\b", "<id>", text)
    text = re.sub(r"\b\d+\b", "#", text)
    return text[:220]


def _evidence_fault_signature(text: str) -> set:
    """Stable serious-fault signatures from pasted console/terminal evidence."""
    out = set()
    for line in str(text or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        serious = any(sig in raw for sig in _TERMINAL_SIGNALS)
        http = re.search(r"\bHTTP\s+([45]\d\d)\s+([A-Z]+)\s+(\S+)", raw)
        if not serious and not http:
            continue
        if http:
            out.add(_normalise_fault_line(
                f"HTTP {http.group(1)} {http.group(2)} {http.group(3)}"))
        else:
            out.add(_normalise_fault_line(raw))
    return out


def _observation_fault_signature(seen, trace: str = "") -> set:
    sig = {_normalise_fault_line(x) for x in (seen.signature() if seen else set()) if x}
    if seen is not None and getattr(seen, "clicked", "") and not getattr(seen, "changed", False):
        sig.add(_normalise_fault_line("CONTROL_NO_CHANGE " + str(seen.clicked)))
    sig.update(_evidence_fault_signature(trace))
    return {x for x in sig if x}


def _route_for_page_source(rel: str) -> str:
    rel = str(rel or "").replace("\\", "/")
    if not re.fullmatch(r"app/(?:.+/)?page\.(?:js|jsx|ts|tsx)", rel):
        return ""
    parts = rel.split("/")[1:-1]
    segs = [x for x in parts if not (x.startswith("(") and x.endswith(")"))]
    route = "/" + "/".join(segs) if segs else "/"
    return route


def _infer_issue_route(route: str, complaint: str, console: str, trace: str,
                       arch, analyzer=None) -> str:
    """Best concrete page route from the user's live evidence, not a guess at '/."""
    explicit = str(route or "").split("?", 1)[0].strip()
    if explicit and not explicit.startswith("/"):
        explicit = ""
    blob = "\n".join([str(console or ""), str(trace or "")]).replace("\\", "/")
    files = getattr(arch, "files", {}) or {}

    for rel in files:
        if rel in blob and re.fullmatch(r"app/(?:.+/)?page\.(?:js|jsx|ts|tsx)", rel):
            candidate = _route_for_page_source(rel)
            if candidate and "[" not in candidate:
                return candidate

    gets = re.findall(r"\bGET\s+(/[^\s?]*)[^\n]*?\b(?:200|4\d\d|5\d\d)\b", blob)
    for candidate in reversed(gets):
        if candidate.startswith(("/api/", "/_next/")):
            continue
        if "[" not in candidate:
            return candidate.rstrip("/") or "/"

    if explicit and explicit != "/":
        return explicit.rstrip("/") or "/"

    try:
        routes = analyzer.enumerate_routes() if analyzer is not None else {}
    except Exception:
        routes = {}
    words = {w for w in re.findall(r"[a-z0-9]+", str(complaint or "").lower()) if len(w) >= 4}
    scored = []
    for candidate, meta in (routes or {}).items():
        if meta.get("kind") != "page" or meta.get("dynamic") or candidate.startswith("/api/"):
            continue
        route_words = set(re.findall(r"[a-z0-9]+", candidate.lower()))
        score = len(words & route_words)
        if score:
            scored.append((-score, len(candidate), candidate))
    if scored:
        return sorted(scored)[0][2]
    return explicit or "/"


def _api_url_for_source(rel: str) -> str:
    rel = str(rel or "").replace("\\", "/")
    m = re.fullmatch(r"app/api/(.+)/route\.(?:js|jsx|ts|tsx)", rel)
    return "/api/" + m.group(1) if m else ""


def _routes_affected_by_paths(arch, paths) -> list[str]:
    """Concrete page routes that render or call the changed source set."""
    files = getattr(arch, "files", {}) or {}
    out = set()
    for rel in paths or []:
        rel = str(rel or "").replace("\\", "/")
        try:
            out.update(routes_rendering(files, rel))
        except Exception:
            pass
        direct = _route_for_page_source(rel)
        if direct:
            out.add(direct)
        api = _api_url_for_source(rel)
        if api:
            prefix = api.split("[", 1)[0].rstrip("/")
            for owner, body in files.items():
                if not owner.endswith((".js", ".jsx", ".ts", ".tsx")):
                    continue
                if prefix and prefix in str(body or ""):
                    try:
                        out.update(routes_rendering(files, owner))
                    except Exception:
                        pass
    return sorted(r for r in out
                  if r.startswith("/") and not r.startswith("/api/") and "[" not in r)


def _feature_focus_scope(arch, paths, *, declared_routes=None, route_hint: str = ""):
    """Return only pages/APIs directly owned by the feature change."""
    files = getattr(arch, "files", {}) or {}
    direct_pages, caller_pages, shared_pages, apis = set(), set(), set(), set()

    for route in declared_routes or []:
        route = str(route or "").strip()
        if not route.startswith("/") or "[" in route:
            continue
        (apis if route.startswith("/api/") else direct_pages).add(route.rstrip("/") or "/")

    for rel0 in paths or []:
        rel = str(rel0 or "").replace("\\", "/")
        direct = _route_for_page_source(rel)
        if direct and "[" not in direct:
            direct_pages.add(direct)

        api = _api_url_for_source(rel)
        if api:
            if "[" not in api:
                apis.add(api.rstrip("/") or "/")
            prefix = api.split("[", 1)[0].rstrip("/")
            for owner, body in files.items():
                if not owner.endswith((".js", ".jsx", ".ts", ".tsx")):
                    continue
                if prefix and prefix in str(body or ""):
                    try:
                        caller_pages.update(routes_rendering(files, owner))
                    except Exception:
                        pass
            continue

        if rel.startswith(("components/", "lib/", "hooks/", "utils/")) or rel.startswith("app/layout"):
            try:
                shared_pages.update(routes_rendering(files, rel))
            except Exception:
                pass

    pages = {r for r in direct_pages | caller_pages
             if r.startswith("/") and not r.startswith("/api/") and "[" not in r}

    if not pages and shared_pages:
        candidates = [r for r in shared_pages
                      if r.startswith("/") and not r.startswith("/api/") and "[" not in r]
        picked = []
        hint = (route_hint or "").rstrip("/") or "/"
        if hint in candidates:
            picked.append(hint)
        if "/" in candidates and "/" not in picked:
            picked.append("/")
        for r in sorted(candidates):
            if r not in picked:
                picked.append(r)
            if len(picked) >= 2:
                break
        pages.update(picked)

    if not pages and route_hint and route_hint.startswith("/") and "[" not in route_hint:
        pages.add(route_hint.rstrip("/") or "/")

    return sorted(pages), sorted(apis)


def _reproduce_state(proj_dir: Path, route: str, complaint: str, analyzer):
    mark = dev_log_mark()
    seen = _reproduce_complaint(proj_dir, route, complaint, analyzer)
    trace = _filter_db_noise(dev_log_since(mark, limit=180), True)
    return seen, trace, _observation_fault_signature(seen, trace)


def _stabilize_bug_repair(arch, proj_dir: Path, proj_name: str, analyzer, *,
                          route: str, complaint: str, model: str,
                          baseline_signature: set, max_rounds: int = 4) -> tuple:
    """Keep repairing the SAME affected route until downstream faults stop."""
    total = []
    last_sig = None
    last_seen = None
    for rnd in range(1, max_rounds + 1):
        elog("INFO", f"   🩺 Bug stabilization {rnd}/{max_rounds} on {route}")
        seen, trace, sig = _reproduce_state(proj_dir, route, complaint, analyzer)
        last_seen = seen

        # A clean rerun settles a runtime/network complaint.
        semantic_ok = bool(getattr(seen, "clicked", "") and getattr(seen, "changed", False))
        if not sig and (baseline_signature or semantic_ok):
            elog("INFO", "   ✅ Affected route stayed clean after the repair")
            return True, total, seen
        if baseline_signature:
            old_left = baseline_signature & sig
            fresh = sig - baseline_signature
            if not old_left and not fresh:
                elog("INFO", "   ✅ Original symptom is gone and no downstream fault appeared")
                return True, total, seen
        if not baseline_signature and not sig and getattr(seen, "ran", False):
            return bool(total), total, seen

        if sig == last_sig:
            elog("WARN", "   ↔ the same affected-route fault survived the last repair")
            break
        last_sig = set(sig)

        evidence = (f"The user reports: {complaint}\nAffected page: {route}\n\n"
                    + (seen.as_prompt() if seen is not None else "")
                    + ("\n\nThe Next dev server printed:\n" + trace if trace else ""))
        focus = _exact_runtime_focus(arch, evidence)
        fixed = _repair_runtime(arch, proj_dir, None, analyzer, evidence, trace,
                                rnd + 1, model=model, focus_paths=focus or None)
        if not fixed:
            elog("WARN", "   ↔ no evidence-backed follow-up repair was possible")
            break
        total.extend(p for p in fixed if p not in total)

        check = verify_after_edit(arch, proj_dir, proj_name, stack="next",
                                  build_rounds=1, probe=False, analyzer=analyzer)
        if (not check.get("build_ok", True) or check.get("syntax_broken")
                or check.get("broken_imports")):
            elog("WARN", "   ↔ follow-up repair did not return to a green compile/import state")
            break
    return False, total, last_seen

def run_bug_report(proj_name: str, complaint: str, model: str, route: str = "",
                   think: bool = None, qa_model: str = "", console: str = ""):
    """Repair one user-visible bug as a converging transaction."""
    set_tester_emit(emit)
    tx = None
    try:
        proj_dir, arch, stack = _open_for_edit(proj_name, model, think)
        if arch is None:
            return
        analyzer = AnalyzerAgent(arch, proj_dir,
                                 base_url=f"http://localhost:{DEV_PORT}",
                                 callbacks=_analyzer_callbacks())
        elog("INFO", f"🐛 {complaint[:80]}")
        eprog("Reproducing…", 20)

        if not _dev_alive():
            elog("INFO", "   ▶ Starting the dev server to reproduce it")
            start_dev_server(proj_dir, stack)
            wait_for_dev(stack)

        effective_route = _infer_issue_route(route, complaint, console, "",
                                             arch, analyzer)
        if effective_route != (route or "/"):
            elog("INFO", f"   🎯 Repair route resolved from evidence: {effective_route}")

        mark = dev_log_mark()
        seen = _reproduce_complaint(proj_dir, effective_route, complaint, analyzer)
        trace = _filter_db_noise(dev_log_since(mark, limit=180), True)
        faults = terminal_faults(trace)
        baseline_sig = _observation_fault_signature(seen, trace)
        baseline_sig.update(_evidence_fault_signature(console))

        report = (f"The user reports: {complaint}\n\n"
                  f"They were on {effective_route}.\n\n")
        if console:
            report += ("Their own browser had already logged this on the page "
                       "they were looking at — this is primary evidence:\n"
                       f"{console.strip()}\n\n")
            elog("INFO", "   📋 the browser's console came with the report")
        report += seen.as_prompt()
        if faults:
            report += ("\n\nThe dev server printed this at the same time:\n"
                       + "\n".join(faults[:6]))
            elog("INFO", f"   📋 {len(faults)} matching server error(s)")

        tx = _capture_feature_transaction(arch, proj_dir)
        before = dict(getattr(arch, "files", {}) or {})

        eprog("Repairing…", 45)
        focus = _exact_runtime_focus(arch, report + "\n" + trace)
        fixed = _repair_runtime(arch, proj_dir, None, analyzer, report, trace, 1,
                                model=model, focus_paths=focus or None)
        if not fixed:
            eerr("Nothing was changed — source evidence did not prove a safe repair")
            return
        elog("INFO", f"   ✅ {len(fixed)} file(s) changed")

        eprog("Checking…", 65)
        _fill_missing_images(arch, proj_dir)
        check = verify_after_edit(arch, proj_dir, proj_name, stack=stack,
                                  build_rounds=1, probe=False, analyzer=analyzer)
        hard_red = (not check.get("build_ok", True)
                    or bool(check.get("syntax_broken"))
                    or bool(check.get("broken_imports")))
        if hard_red:
            reverted = _restore_feature_transaction(arch, proj_dir, tx)
            _stop_dev_proc(); start_dev_server(proj_dir, stack); wait_for_dev(stack)
            elog("WARN", f"   ↩ Bug repair rolled back — compile/import verification stayed red ({len(reverted)} file(s) restored)")
            eerr("The attempted bug repair introduced a compile/import regression and was rolled back")
            return

        eprog("Watching the repaired flow…", 78)
        stable, more, after = _stabilize_bug_repair(
            arch, proj_dir, proj_name, analyzer, route=effective_route,
            complaint=complaint, model=model, baseline_signature=baseline_sig)
        all_fixed = list(dict.fromkeys(list(fixed) + list(more)))
        if not stable:
            reverted = _restore_feature_transaction(arch, proj_dir, tx)
            try:
                arch.save_convo()
            except Exception:
                pass
            _stop_dev_proc(); start_dev_server(proj_dir, stack); wait_for_dev(stack)
            elog("WARN", f"   ↩ Bug repair rolled back after non-converging live verification ({len(reverted)} file(s) restored)")
            eerr("The repair did not stabilize the affected route, so the original app state was restored")
            return

        touched = [p for p in all_fixed if p in before]
        undo_id = _snapshot(proj_name, touched, before) if touched else ""
        if undo_id:
            emit({"type": "undo_point", "id": undo_id, "files": touched})
        arch.save_convo()

        _report_symptom(seen, after or _reproduce_complaint(
            proj_dir, effective_route, complaint, analyzer))
        eprog("Done!", 100)
        edone(f"http://localhost:{DEV_PORT}", proj_name,
              preview=effective_route)
    except Exception as e:
        if tx is not None:
            try:
                _restore_feature_transaction(arch, proj_dir, tx)
            except Exception:
                pass
        eerr(f"Bug fix error: {e}")
        log.exception("run_bug_report")
    finally:
        stop_model(model)

def run_question(arch, proj_dir: Path, question: str):
    """Answer a question about the app without touching it."""
    try:
        analyzer = AnalyzerAgent(arch, proj_dir,
                                 callbacks=_analyzer_callbacks())
        agent = FeaturesAgent(arch, proj_dir, callbacks=_analyzer_callbacks(),
                              analyzer=analyzer, model=model)
        convo = [
            {"role": "system", "content":
                "You are answering a question about a Next.js app you can see "
                "in full. Answer in two or three sentences, naming the exact "
                "files and values involved. Do not write code unless a two "
                "line snippet is the clearest answer. Do not suggest changes "
                "unless asked."},
            {"role": "user", "content":
                f"## The project\n{agent.full_source()}\n\n"
                f"## The question\n{question}"},
        ]
        buf = []
        arch._stream(convo, buf.append, temperature=0.2, timeout=120)
        answer = "".join(buf).strip() or "I could not work that out."
        echat(answer)
        elog("INFO", "   💬 answered")
    except Exception as e:
        eerr(f"Could not answer: {e}")
        log.exception("run_question")


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def verify_after_edit(arch, proj_dir: Path, proj_name: str, *,
                      stack: str = "next", build_rounds: int = 2,
                      probe: bool = True, analyzer=None) -> dict:
    """Everything that has to be true after a tool edits a project."""
    out = {"build_ok": True, "routes_failed": [], "broken_imports": 0,
           "syntax_broken": []}
    if stack != "next":

        start_dev_server(proj_dir, stack)
        wait_for_dev(stack)
        return out

    analyzer = analyzer or AnalyzerAgent(
        arch, proj_dir, base_url=f"http://localhost:{DEV_PORT}",
        callbacks=_analyzer_callbacks())

    compiling = bool(build_rounds) and not _truthy("AGENTFORGE_SKIP_BUILD_CHECK")

    if compiling:

        _stop_dev_proc()

    ensure_node_deps(proj_dir)

    problems, why_not = check_syntax(proj_dir, arch.files)
    if why_not:

        elog("INFO", f"   ⚠ Syntax not checked — {why_not}")
    elif problems:
        elog("WARN", f"🧩 {len(problems)} file(s) do not parse — repairing")
        ephase({"phase": -6, "title": "Fixing broken syntax", "status": "active"})
        report = AnalyzerReport()
        report.findings = [
            Finding(severity="blocker", code="SYNTAX_ERROR", path=p["path"],
                    message=msg)
            for p, msg in zip(problems, syntax_messages(problems))
        ]
        for line in syntax_messages(problems):
            elog("WARN", f"   ✗ {line}")
        try:
            analyzer.repair(report)
        except Exception as e:
            elog("WARN", f"   ⚠ Syntax repair failed: {e}")
            log.exception("verify_after_edit: syntax repair")
        still, _ = check_syntax(proj_dir, arch.files)
        elog("INFO" if not still else "WARN",
             "   ✅ Every file parses" if not still
             else f"   ⚠ {len(still)} still unparseable")
        out["syntax_broken"] = [p["path"] for p in still]
        ephase({"phase": -6, "title": "Fixing broken syntax", "status": "done"})

    # A link to a route nobody built is a 404 the person finds by clicking.
    # It is cheap to see from the source, it needs no dev server, and an edit
    # that adds a nav item is exactly where it appears — an "Admin" button
    # was added to a navbar and /admin did not exist for two more turns.
    try:
        dead = [u for u in (analyzer.scan().dead_links or [])]
    except Exception as e:                                      # noqa: BLE001
        log.debug(f"dead link scan: {e}")
        dead = []
    out["dead_links"] = dead
    if dead:
        shown = ", ".join(dead[:5]) + ("…" if len(dead) > 5 else "")
        elog("WARN", f"   🔗 {len(dead)} link(s) point at a page that does not "
                     f"exist: {shown} — clicking one gives a 404")

    broken = check_named_imports(arch.files)
    out["broken_imports"] = len(broken)
    if broken:
        elog("WARN", f"🔗 {len(broken)} import(s) name something the target "
                     f"module does not export")
        ephase({"phase": -7, "title": "Fixing broken imports", "status": "active"})
        report = AnalyzerReport()
        report.findings = analyzer.broken_imports()
        try:
            analyzer.repair(report)
        except Exception as e:
            elog("WARN", f"   ⚠ Import repair failed: {e}")
            log.exception("verify_after_edit: import repair")
        still = check_named_imports(arch.files)
        out["broken_imports"] = len(still)
        elog("INFO" if not still else "WARN",
             "   ✅ Every import resolves" if not still
             else f"   ⚠ {len(still)} still unresolved")
        ephase({"phase": -7, "title": "Fixing broken imports", "status": "done"})

    if compiling:
        out["build_ok"] = run_build_fix_loop(arch, proj_dir, MONGO.available,
                                             max_rounds=build_rounds)
        if not out["build_ok"]:
            estep("build", "error")
        start_dev_server(proj_dir, stack)
    elif not _dev_alive():

        start_dev_server(proj_dir, stack)

    if not wait_for_dev(stack):
        elog("WARN", "   ⚠ Dev server did not come up — skipping the route probe")
        return out

    if probe:
        report = analyzer.scan()
        mark = dev_log_mark()
        analyzer.probe_routes(report)

        others = [f for f in report.findings if f.code != "ROUTE_ERROR"]
        blockers = [f for f in others if f.severity == "blocker"]
        if blockers:
            elog("WARN", f"   ⚠ {len(blockers)} blocker(s) the edit leaves behind:")
            for f in blockers[:5]:
                elog("WARN", f"      {f.line()[:150]}")
        elif others:
            elog("INFO", f"   · {len(others)} smaller finding(s) — see the "
                         f"Testing tab")

        failed = [f for f in report.findings if f.code == "ROUTE_ERROR"]
        if failed:
            elog("WARN", f"   ❌ {len(failed)} route(s) failing — repairing")
            ephase({"phase": -8, "title": "Fixing failing routes", "status": "active"})
            report.findings = failed
            report.missing = []

            trace = _filter_db_noise(dev_log_since(mark), True)
            try:
                analyzer.repair(report, server_log=trace)
            except Exception as e:
                elog("WARN", f"   ⚠ Route repair failed: {e}")
                log.exception("verify_after_edit: route repair")
            ephase({"phase": -8, "title": "Fixing failing routes", "status": "done"})

            _stop_dev_proc()
            ensure_node_deps(proj_dir)
            start_dev_server(proj_dir, stack)
            if wait_for_dev(stack):
                again = analyzer.scan()
                analyzer.probe_routes(again)
                failed = [f for f in again.findings if f.code == "ROUTE_ERROR"]
        out["routes_failed"] = [f.message for f in failed]

    return out


_WATCHED_PKGS = ("agents", "qa_agent")


def _own_sources():
    for pkg in _WATCHED_PKGS:
        for p in (BASE_DIR / pkg).glob("*.py"):
            if p.is_file():
                yield f"{pkg}/{p.name}", p


_AGENT_MTIMES = {rel: p.stat().st_mtime for rel, p in _own_sources()}


def warn_if_agents_stale():
    """Say so when AgentForge's own code has changed since this process started."""
    stale = []
    for rel, p in _own_sources():
        try:
            if p.stat().st_mtime > _AGENT_MTIMES.get(rel, 0) + 1:
                stale.append(rel)
        except OSError:
            continue
    if stale:
        elog("WARN", f"⚠ {', '.join(sorted(stale))} changed since this server "
                     f"started — restart AgentForge for it to take effect")
    return stale


RUN_INTENT = ".agentforge/intent.json"


def save_run_intent(proj_dir: Path, **kw) -> None:
    """Write down what this run was asked to do, at the moment it was asked."""
    try:
        out = proj_dir / RUN_INTENT
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.is_file():
            return
        out.write_text(json.dumps(
            {k: v for k, v in kw.items() if v not in (None, "")},
            indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        log.debug(f"intent for {proj_dir.name}: {e}")


def load_run_intent(proj_dir: Path) -> dict:
    try:
        return json.loads((proj_dir / RUN_INTENT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

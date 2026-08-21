# Pencil edits and page-level update helpers.
def _vision_model(preferred: str) -> str:
    """The model to send an image to."""
    try:
        if ollama.supports_vision(preferred):
            return preferred
    except Exception:
        pass
    saved = str(load_settings().get("vision_model", "")).strip()
    if saved:
        return saved
    try:
        cat = ollama.catalog()
        pool = (cat.get("cloud") or []) + (cat.get("local") or [])
        for entry in pool:
            if entry.get("vision"):
                return entry["id"]
    except Exception as e:
        log.warning(f"could not find a vision model: {e}")
    return ""


def _pencil_write_round(arch, path, before, instruction, element, shot,
                        vis_model, payload, attempts=2, line=0):
    vp = payload.get("viewport") or {}
    text = (f"Route: {element.get('route') or payload.get('route') or '/'}   "
            f"Viewport: {vp.get('w', '?')}×{vp.get('h', '?')} "
            f"({vp.get('mode', 'desktop')})\n")
    if shot and shot.ok():
        c = shot.crop
        text += (f"The red freehand annotation marks the region to redesign.\n"
                 f"Region in the page: x={c.get('x')} y={c.get('y')} "
                 f"{c.get('width')}×{c.get('height')}\n")
        if shot.logged_in:
            text += "The capture is of the signed-in view.\n"
    else:
        text += ("No screenshot is available. Redesign the element described "
                 "below.\n")
    if element:
        text += f"\nElement under the drawing:\n{describe(element)}\n"
        where = _where_in_file(before, element, line)
        if where:
            text += f"\nWhere that is in the source:\n{where}\n"
    text += (f"\n## What the user asked for\n{instruction}\n\n"
             f"## The complete current source of {path}\n{before}")
    near = _neighbours(arch, path, before)
    if near:
        text += (f"\n\n## The files this one is joined to — what it renders, "
                 f"and what renders it\n{near}")

    msg = {"role": "user", "content": text}
    if shot and shot.ok():

        msg["images"] = [shot.png_b64]

    convo = [{"role": "system", "content": PENCIL_SYSTEM + "\n\n" + TOOL_HELP}, msg]

    for attempt in range(1, attempts + 1):
        got = {"writes": {}}

        def capture_write(out_path, content):
            key = str(out_path or "").replace("\\", "/").lstrip("./")
            if key:
                got["writes"][key] = content

        parser = FileStreamParser(
            on_text=lambda t: None,
            on_file_start=lambda p: None,
            on_file_token=lambda t: None,
            on_file_end=capture_write)

        raw = []

        def feed(token):
            raw.append(token)
            parser.feed(token)

        try:
            reply = ""
            seen_observations = set()
            while True:
                turn_raw = []

                def feed_turn(token):
                    turn_raw.append(token)
                    feed(token)

                arch._stream(convo, feed_turn, temperature=0.4,
                             model=vis_model, timeout=arch.EDIT_TIMEOUT)
                reply = "".join(turn_raw)
                convo.append({"role": "assistant", "content": reply})
                observations, used = WorkspaceTools(arch).serve(reply)
                if used and not got["writes"]:
                    sig = observations.strip()
                    used_chars = sum(len(str(m.get("content", ""))) for m in convo)
                    try:
                        budget_chars = int(arch._budget_chars())
                    except Exception:
                        budget_chars = 0
                    if sig and sig not in seen_observations and (
                            not budget_chars or used_chars < budget_chars * 0.82):
                        seen_observations.add(sig)
                        elog("INFO", f"   🧰 pencil editor inspected {used} workspace tool(s)")
                        convo.append({"role": "user", "content":
                                      "Tool observations:\n\n" + observations +
                                      "\n\nContinue the SAME visual edit. Follow the source/import/caller evidence. "
                                      "If another file must change, do not hide that fact; emit the required write so the controller can escalate safely."})
                        continue
                    if sig in seen_observations:
                        elog("WARN", "   ↔ pencil editor repeated the same inspection — deciding with current evidence")
                break
        except Exception as e:
            eerr(f"The model failed: {e}")
            return False, ""
        parser.close()
        writes = got["writes"]
        body = writes.get(path, "")
        external = [rel for rel in writes if rel != path]
        if external:
            names = ",".join(external[:8])
            elog("INFO", "   ↗ pencil editor emitted dependency write(s) — "
                         f"escalating transaction: {names}")
            return False, "__AGENTFORGE_ESCALATE__:" + names
        if writes and not body:
            only = ",".join(list(writes)[:8])
            elog("WARN", f"   ⚠ pencil editor wrote {only}, not selected owner {path} — escalating")
            return False, "__AGENTFORGE_ESCALATE__:" + only
        need = re.search(r"^\s*NEED\s+(\S+)\s*$", reply, re.M)
        if need and not body:
            needed = need.group(1).strip('`')
            elog("INFO", f"   ↗ pencil editor discovered dependency {needed} — escalating automatically")
            return False, "__AGENTFORGE_ESCALATE__:" + needed
        if not body:
            head = " ".join(reply.split())[:300] or "(empty response)"
            if _DECLINED_RE.search(reply):
                elog("INFO", f"   ✅ Nothing to change — {head[:160]}")
                eerr(f"That region was not found in {path}, so nothing was "
                     f"changed. {vis_model} said: {head[:200]}")
                return False, ""
            elog("WARN", f"   ⚠ no <write_file> block — {vis_model} said: {head}")

            if attempt < attempts:
                convo.append({"role": "assistant", "content": reply[:2000]})
                convo.append({"role": "user", "content":
                    "That was not a file. Output the COMPLETE file inside "
                    f"one <write_file path=\"{path}\">…</write_file> block, "
                    "starting immediately with '<write_file'. No markdown "
                    "fences, no description of the image, no explanation."})
                continue
            eerr(f"{vis_model} returned no file after {attempts} attempts. "
                 f"It said: {head[:220]}")
            return False, ""

        if body.strip():
            return True, body
        if attempt == attempts:
            eerr("The model returned an empty file — nothing was written")
            return False, ""
    return False, ""


def run_pencil_edit(proj_name: str, instruction: str, payload: dict,
                    model: str, think=None):
    """Redesign the region the user drew over."""
    set_tester_emit(emit)
    element = payload.get("element") or {}
    try:
        proj_dir, arch, stack = _open_for_edit(proj_name, model, think)
        if arch is None:
            return
        analyzer = AnalyzerAgent(arch, proj_dir,
                                 base_url=f"http://localhost:{DEV_PORT}",
                                 callbacks=_analyzer_callbacks())
        resolver = ElementResolver(arch, analyzer)

        route = payload.get("route") or element.get("route") or "/"
        eprog("Finding the code…", 12)
        res = resolver.resolve({**element, "route": route})
        if not res.path:
            eerr(f"Could not find the code for that region — {res.reason}")
            return
        elog("INFO", f"   📍 {res.path}:{res.line or '?'}")
        shared = _shared_routes(arch, res.path)
        emit({"type": "element_picked", "file": res.path, "line": res.line,
              "score": res.score, "candidates": res.candidates[:6],
              "used_model": res.used_model, "shared_routes": shared[:12]})
        _log_reach(res.path, shared, route)

        before = arch.files.get(res.path, "")
        if not before:
            eerr(f"{res.path} is empty or unreadable")
            return
        before_project = dict(arch.files)

        broaden, change_request, impact = _visual_change_preflight(
            arch, analyzer, proj_dir, instruction, element, res.path, route, model)
        if broaden:
            count = len(getattr(impact, "files", []) or [])
            elog("INFO", f"   ↗ pencil change spans {count} source file(s) — switching to full agentic change")
            return run_feature(proj_name, change_request, model, think)

        vis_model = _vision_model(model)
        shot = None
        if vis_model:
            eprog("Capturing the region…", 30)
            ephase({"phase": -12, "title": "Capturing the region",
                    "status": "active"})
            creds = analyzer.demo_credentials()
            shot = capture_region(
                route, viewport=payload.get("viewport") or {},
                scroll=payload.get("scroll") or {},
                strokes=payload.get("strokes") or [], port=DEV_PORT,
                login=(creds[0] if creds else None),
                login_endpoint=analyzer.find_login_endpoint())
            ephase({"phase": -12, "title": "Capturing the region",
                    "status": "done"})
            if not shot.ok():
                elog("WARN", f"   ⚠ Screenshot failed ({shot.error}) — using the "
                             f"element description instead")
                shot = None
            elif vis_model != model:
                elog("INFO", f"   👁 {model} has no vision — using {vis_model} "
                             f"for this one call")
        else:
            elog("WARN", "   ⚠ No vision-capable model is available — the "
                         "drawing is used only to locate the region")

        eprog("Redesigning…", 50)
        ephase({"phase": -13, "title": "Redesigning", "status": "active"})

        mark = dev_log_mark()
        ok, written = _pencil_write_round(arch, res.path, before, instruction,
                                          element, shot, vis_model or model,
                                          payload, line=res.line)
        ephase({"phase": -13, "title": "Redesigning", "status": "done",
                "written": 1 if ok else 0})
        if not ok:
            if isinstance(written, str) and written.startswith("__AGENTFORGE_ESCALATE__:"):
                needed = written.split(":", 1)[1]
                change_request = (
                    f"On route {route or '/'} the user drew over a region rendered by {res.path}. "
                    f"The visual editor discovered that {needed} is also required.\n\n"
                    f"Requested change:\n{instruction}\n\n"
                    "Implement the complete dependency-aware change across every necessary file, preserving unrelated behavior."
                )
                return run_feature(proj_name, change_request, model, think)
            return

        # The same guard the page editor uses. A pencil edit is a smaller
        # change than a page rewrite, so a reply that removes most of the
        # file is more suspect here, not less.
        why = guard_scope(before, written, designing=True)
        if why:
            elog("WARN", f"   ⛔ Rejected {res.path}: {why[:120]}")
            eerr(f"That edit was rejected — {why[:160]}")
            return
        if not arch.write_file(res.path, written):
            eerr(f"Could not write {res.path}")
            return
        estream_start(res.path)
        estream_end(res.path, written)

        impact = _converge_visual_semantics(
            arch, analyzer, proj_dir, instruction, impact, res.path,
            route, element, model)
        touched = list(dict.fromkeys(getattr(impact, "written", []) or [res.path]))
        undo_id = _snapshot(proj_name, touched, before_project)
        if undo_id:
            emit({"type": "undo_point", "id": undo_id, "files": touched})

        eprog("Verifying…", 78)
        _fill_missing_images(arch, proj_dir)
        verify_after_edit(arch, proj_dir, proj_name, stack=stack,
                          build_rounds=0, probe=False, analyzer=analyzer)
        _autofix_from_terminal(arch, res.path, payload, mark,
                               proj_dir=proj_dir, analyzer=analyzer, model=model)
        eprog("Done!", 100)
        edone(f"http://localhost:{DEV_PORT}", proj_name,
              preview=_route_of(payload))
    except Exception as e:
        eerr(f"Pencil edit error: {e}")
        log.exception("run_pencil_edit")
    finally:
        stop_model(model)


PAGE_UPDATE_SYSTEM = """\
You are rewriting ONE page of a Next.js 16 App Router app, in place.

You are given the complete current source of that page, the components it
renders, and what the user wants changed. Everything you need is here.

DO EVERYTHING THEY ASKED FOR. Rewrite the copy, change the animations,
restructure the layout, redesign the page end to end — if that is the request,
that is the job, and no change is too large.

THAT INCLUDES REMOVING THINGS. "Take the sidebar out", "drop the stats band",
"get rid of the hero" — do exactly that, and delete the markup properly rather
than hiding it behind a class. Removal is a normal request, not a special case.

WHAT MUST NOT HAPPEN is a part of the page disappearing that they never
mentioned. A request to tighten the spacing leaves every section still on the
page. A request to restyle the cards leaves the FAQ below them alone. One
question before you answer: is anything gone that they did not ask you to
remove? If so, put it back.

Do not invent content either. No placeholder addresses, phone numbers, social
links, testimonials or statistics that the app has no data for: an empty column
is better than a fabricated one.

WHAT IS NEVER YOURS TO CHANGE:
  • API routes — same paths, same methods, same request and response shapes.
  • Entities — the collections and the fields on them keep their names.
  • Functions — every one keeps its name, its parameters and what it does.
  • Exports — other files import them.
  • Props — a component called with `product={p}` is still called that way.
  • 'use client' stays exactly where it is, or stays absent. If something you
    add needs a hook or a handler and this file is a Server Component, put that
    piece in its own small 'use client' component and render it here.

IF YOU NEED A PACKAGE THAT IS NOT INSTALLED, ask for it BEFORE the file:

<run_command>npm install embla-carousel-react</run_command>

One package per command, real npm names, `npm install` only. Already there, so
never ask for: react, react-dom, next, mongodb, tailwindcss, lucide-react,
framer-motion, better-auth.

Output the COMPLETE file in exactly one <write_file path="…"> block. No
markdown fences, no explanation.
"""


def _page_file_for(arch, analyzer, route: str) -> str:
    """The page file a route renders, or '' when the route is unknown."""
    route = (route or "/").split("?")[0].rstrip("/") or "/"
    try:
        for url, meta in (analyzer.enumerate_routes() or {}).items():
            if meta.get("kind") == "page" and (url.rstrip("/") or "/") == route:
                return meta.get("file", "")
    except Exception as e:
        log.debug(f"page file for {route}: {e}")

    stem = "app" + ("" if route == "/" else route) + "/page"
    for ext in (".jsx", ".js"):
        if (stem + ext) in arch.files:
            return stem + ext
    return ""



_RENDER_IMPORT_RE = re.compile(r"""from\s+['\"](@/[^'\"]+|\.{1,2}/[^'\"]+)['\"]""")


def _rendered_by(arch, roots) -> set:
    """Existing source the given files import, one level down.

    A page's Navbar, its cards and its form live here. They are what a page
    edit is usually actually about, and refusing them while allowing a brand
    new component pushed the model into writing a second Navbar beside the
    one that was wrong.
    """
    files = getattr(arch, "files", None) or {}
    out = set()
    for root in roots:
        body = str(files.get(root) or "")
        for spec in _RENDER_IMPORT_RE.findall(body):
            rel = spec[2:] if spec.startswith("@/") else None
            if rel is None:
                base = "/".join(str(root).split("/")[:-1])
                rel = str(Path(base) / spec).replace("\\", "/")
                while "/./" in rel:
                    rel = rel.replace("/./", "/")
                while "/../" in rel:
                    rel = re.sub(r"[^/]+/\.\./", "", rel, count=1)
            for ext in ("", ".jsx", ".js"):
                if rel + ext in files:
                    out.add(rel + ext)
                    break
    return out

def run_page_update(proj_name: str, instruction: str, model: str, route: str,
                    think: bool = None):
    """Rewrite the page the user is looking at, and nothing else."""
    set_tester_emit(emit)
    try:
        proj_dir, arch, stack = _open_for_edit(proj_name, model, think)
        if arch is None:
            return
        analyzer = AnalyzerAgent(arch, proj_dir,
                                 base_url=f"http://localhost:{DEV_PORT}",
                                 callbacks=_analyzer_callbacks())
        path = _page_file_for(arch, analyzer, route)
        if not path or path not in arch.files:
            elog("INFO", f"   ↪ {route or '/'} is not one page — planning it "
                         f"as a change instead")
            return run_feature(proj_name, instruction, model, think)

        before = arch.files[path]
        elog("INFO", f"📄 Page update — {route or '/'} → {path}")
        eprog("Rewriting the page…", 35)
        ephase({"phase": -20, "title": f"Rewriting {route or '/'}",
                "status": "active"})

        near = _neighbours(arch, path, before)
        chain = _layout_chain(arch, path)

        chrome = "\n\n".join(f"--- {p} ({_reach_label(arch, p, route)}) ---\n{b}"
                             for p, b in chain)
        pmap = _project_map(arch)
        user = ((f"{pmap}\n\n" if pmap else "")
                + f"## The page\nRoute: {route or '/'}\nFile: {path}\n\n"
                f"## What the user wants\n{instruction}\n\n"
                f"DO NOT CHANGE: api routes, entities, functions. The routes "
                f"stay at the same paths with the same methods and the same "
                f"request and response shapes. The data entities keep their "
                f"fields and their names. Every function keeps its name, its "
                f"parameters and what it does.\n\n"
                f"Everything else on this page is yours: the layout, the copy, "
                f"the animation, the whole design if that is what they asked "
                f"for — including removing a section if they asked for that. "
                f"Only what they did not mention stays exactly as it is.\n\n"
                f"## The COMPLETE current source of {path}\n{before}"
                + (f"\n\n## The components it renders, so you can match "
                   f"them\n{near}" if near else "")
                + (f"\n\n## The layout wrapping this route, and what it renders"
                   f"\nThe navbar, header, footer and page shell live HERE, not "
                   f"in the page — Next composes the layout around it, so the "
                   f"page's own source will not mention them. If what was asked "
                   f"for is one of those, rewrite the file below that actually "
                   f"contains it and leave the page alone.\n\n"
                   f"A layout wraps EVERY route beneath it. Removing a navbar "
                   f"from the root layout removes it from the whole site, which "
                   f"is almost never what one page's request meant. Each file "
                   f"below says how many routes it is on — read that before "
                   f"you touch it.\n\n"
                   f"### Taking chrome off THIS route only\n"
                   f"A nested layout does NOT do it. `app/…/layout.jsx` renders "
                   f"INSIDE the root layout, so anything the root renders is "
                   f"still there — this was the advice here before and it "
                   f"cannot work. What works:\n"
                   f"  1. Move the markup into a small component under "
                   f"`components/` with `'use client'` on line 1.\n"
                   f"  2. In it, `const pathname = usePathname()` from "
                   f"`next/navigation`, and `return null` for the routes it "
                   f"should not appear on.\n"
                   f"  3. Render that component from the layout in place of "
                   f"the markup you moved.\n"
                   f"Do NOT put `'use client'` on the root layout — it exports "
                   f"`metadata` and owns `<html>`/`<body>`, and both stop "
                   f"working in a client component. A nested layout is still "
                   f"the right answer for ADDING chrome to one route.\n\n"
                   f"{chrome}"
                   if chrome else ""))
        arch._workspace_tool_cache = {}
        convo = [{"role": "system", "content": PAGE_UPDATE_SYSTEM + "\n\n" + TOOL_HELP},
                 {"role": "user", "content": user}]

        mark = dev_log_mark()

        # The page, its layouts, and everything they render. The old rule
        # allowed a NEW component but refused an edit to the existing one the
        # page already imports, so a fix to the real Navbar was dropped and
        # the run reported that the model had returned nothing.
        writable = {path} | {p for p, _ in chain}
        writable.add("/".join(path.split("/")[:-1]) + "/layout.jsx")
        writable |= _rendered_by(arch, writable)
        got, raw, refused = {}, [], []

        def took(pth, content):

            key = (pth or "").strip().lstrip("./").replace("\\", "/")

            editable = key.startswith(("app/", "components/", "lib/")) and \
                key.endswith((".jsx", ".js"))
            fresh = editable and key not in arch.files
            if key not in writable and not fresh:
                # Not silently dropped: an edit outside the page's own tree is
                # usually the model following the bug somewhere real, and the
                # parse/build checks downstream are what decide whether it was
                # right. Keep it, and say where it went.
                if editable:
                    elog("INFO", f"   ↔ {key} is outside this page's tree but "
                                 f"is app source — taking it; it is reverted "
                                 f"if it does not parse or build")
                    got[key] = content
                    return
                refused.append(key)
                elog("WARN", f"   ⛔ ignored a write to {key} — a page edit "
                             f"changes app/, components/ or lib/ source, "
                             f"nothing else")
                return
            if fresh:
                elog("INFO", f"   ➕ {key} — new component for this route's "
                             f"chrome")
            got[key] = content

        parser = FileStreamParser(
            on_text=lambda t: None, on_file_start=lambda pth: None,
            on_file_token=lambda t: None,
            on_file_end=took)

        def feed(tok):
            raw.append(tok)
            parser.feed(tok)

        t0 = time.time()
        try:
            for tool_turn in range(3):
                turn_raw = []
                def feed_turn(tok):
                    turn_raw.append(tok)
                    feed(tok)
                arch._stream(convo, feed_turn, temperature=0.3, timeout=arch.EDIT_TIMEOUT)
                reply = "".join(turn_raw)
                convo.append({"role": "assistant", "content": reply})
                observations, used = WorkspaceTools(arch).serve(reply)
                if used and not got and tool_turn < 2:
                    elog("INFO", f"   🧰 page editor inspected {used} workspace tool(s)")
                    convo.append({"role": "user", "content":
                                  "Tool observations:\n\n" + observations +
                                  "\n\nContinue the same page edit and write the minimum complete file set."})
                    continue
                break
        except Exception as e:
            eerr(f"The model failed: {e}")
            return
        parser.close()
        elog("INFO", f"   ⏱ model {time.time() - t0:.1f}s")

        for out in arch.run_requested_commands("".join(raw)):
            elog("INFO", f"   📦 {out.splitlines()[0][:110]}")

        if not got:
            head = " ".join("".join(raw).split())[:300] or "(empty response)"
            if refused:
                # It DID write something. Saying "returned no file" sent
                # people looking at the model when the rule was the problem.
                elog("WARN", f"   ⚠ every write was outside what a page edit "
                             f"may change: {', '.join(refused[:4])}")
                eerr(f"Nothing was changed — the edit went to "
                     f"{', '.join(refused[:3])}, which a page edit cannot touch.")
                return
            elog("WARN", f"   ⚠ no <write_file> block — model said: {head}")
            if _DECLINED_RE.search("".join(raw)):
                eerr(f"Nothing was changed — the model said: {head[:220]}")
                return
            eerr(f"The model returned no file. It said: {head[:220]}")
            return

        olds, keep = {}, {}
        for key, content in got.items():
            was = arch.files.get(key, "")
            why = guard_scope(was, content, designing=True) if was else ""
            if why:
                elog("WARN", f"   ⛔ Rejected {key}: {why[:120]}")
                continue
            olds[key] = was
            keep[key] = content
        if not keep:
            eerr("The rewrite was rejected — nothing was written")
            return

        undo_id = _snapshot(proj_name, list(keep), olds)
        for key, content in keep.items():
            arch.write_file(key, content)
            estream_start(key)
            estream_end(key, content)
            if key != path:
                elog("INFO", f"   📐 {key} — the chrome lives here, not in "
                             f"{path}")
        if undo_id:
            emit({"type": "undo_point", "id": undo_id, "files": list(keep)})
        ephase({"phase": -20, "title": f"Rewriting {route or '/'}",
                "status": "done", "written": len(keep)})
        arch.save_convo()

        eprog("Checking the page…", 75)
        t1 = time.time()
        _fill_missing_images(arch, proj_dir)
        verify_after_edit(arch, proj_dir, proj_name, stack=stack,
                          build_rounds=0, probe=False, analyzer=analyzer)
        _autofix_from_terminal(arch, path, {"route": route}, mark,
                               proj_dir=proj_dir, analyzer=analyzer, model=model)
        elog("INFO", f"   ⏱ verify {time.time() - t1:.1f}s")

        eprog("Done!", 100)
        edone(f"http://localhost:{DEV_PORT}", proj_name,
              preview=_route_of({"route": route}))
    except Exception as e:
        eerr(f"Page update error: {e}")
        log.exception("run_page_update")
    finally:
        stop_model(model)


def run_agent_update(proj_name: str, instruction: str, model: str,
                     think: bool = None):
    """Agentic edit of an existing project — same write_file loop."""
    set_tester_emit(emit)
    try:
        proj_dir = PROD_DIR / proj_name
        if not proj_dir.exists():
            eerr(f"Project not found: {proj_name}")
            return
        if not ensure_model(model):
            eerr(f"Cannot load model: {model}")
            return

        stack = detect_stack(proj_dir)
        elog("INFO", f"✏️  Agent update ({stack}) — {instruction[:70]}")
        eprog("Reading project…", 10)

        if stack == "next":
            MONGO.ensure_running()

        arch = ArchitectAgent(ollama, model, proj_dir, _agent_callbacks(proj_dir),
                              stack=stack,
                              mongo_uri=MONGO.uri_for(proj_name) if stack == "next" else "",
                              db_name=db_name_for(proj_name) if stack == "next" else "",
                              think=think)
        arch.load_existing()
        stack = arch.stack

        eprog("Applying changes…", 35)
        n = arch.update(instruction)
        if not n:
            eerr("Agent made no changes")
            return
        elog("INFO", f"   ✅ {n} file(s) updated")

        arch.save_convo()

        eprog("Verifying…", 80)
        _fill_missing_images(arch, proj_dir)
        res = verify_after_edit(arch, proj_dir, proj_name, stack=stack)
        if res["routes_failed"]:
            elog("WARN", f"   ⚠ {len(res['routes_failed'])} route(s) still "
                         f"failing: {'; '.join(res['routes_failed'][:3])}")

        eprog("Done!", 100)
        edone(f"http://localhost:{DEV_PORT}", proj_name)
    except Exception as e:
        eerr(f"Agent update error: {e}")
        log.exception("Agent update error")
    finally:
        stop_model(model)

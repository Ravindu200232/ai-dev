# Source graph and safe edit-scope helpers.

LOCAL_IMPORT_RE = re.compile(r"""from\s+['"]@/(components/[\w./-]+)['"]""")


_DECLINED_RE = re.compile(
    r"\b(?:"
    r"does\s+not\s+exist|is\s+not\s+(?:present|there|in\s+the)|not\s+found|"
    r"could\s+not\s+(?:find|locate)|cannot\s+(?:find|locate)|can't\s+(?:find|locate)|"
    r"unable\s+to\s+(?:find|locate)|"
    r"no\s+(?:such|matching|element|section|tools?)\b|"
    r"nothing\s+(?:to\s+change|matching|matches)|"
    r"there\s+is\s+no\b|there\s+are\s+no\b|"
    r"i\s+(?:did\s+not|didn't|do\s+not|don't)\s+(?:find|see|change)|"
    r"left\s+(?:it\s+)?unchanged|changed\s+nothing|no\s+changes?\s+(?:were\s+)?made"
    r")",
    re.I)


def _where_in_file(before: str, element: dict, line: int = 0) -> str:
    """The exact source line the clicked element is written on, quoted back."""
    lines = before.splitlines()
    if not lines:
        return ""

    def quote(n: int) -> str:
        return (f"It is written on line {n + 1}:\n"
                f"{lines[n]}\n\n"
                f"That is the element they pointed at. Change what THAT tag "
                f"renders — not the tag above it, not the one nested inside "
                f"it, and not a sibling that happens to look similar.")

    if 0 < line <= len(lines):
        return quote(line - 1)

    classes = str(element.get("className") or "").strip()
    if classes:
        for probe in (classes, " ".join(classes.split()[:4])):
            if not probe:
                continue
            for n, text in enumerate(lines):
                if probe in text:
                    return quote(n)

    text = str(element.get("text") or "").strip()[:40]
    if len(text) >= 4:
        for n, row in enumerate(lines):
            if text in row:
                return quote(n)
    return ""


def _section_span(element: dict) -> str:
    """The two elements that bound the selected section: its first and its last."""
    sec = element.get("section") or {}
    first, last = sec.get("start"), sec.get("end")
    if not isinstance(first, dict) or not isinstance(last, dict):
        return ""
    return (f"  1. {describe(first).splitlines()[0]}\n"
            f"  2. {describe(last).splitlines()[0]}")


_MAP_IMPORT_RE = re.compile(r"""from\s+['"]@/(components/[^'"]+)['"]""")
_MAP_METHOD_RE = re.compile(
    r"export\s+(?:async\s+)?(?:function|const)\s+(GET|POST|PUT|PATCH|DELETE)")
_MAP_COLLECTION_RE = re.compile(r"""getCollection\s*\(\s*['"]([a-zA-Z0-9_]+)['"]""")


def _project_map(arch) -> str:
    """Where everything lives, in about a page of text."""
    pages, apis, comps, libs, colls = [], [], set(), [], set()
    for rel, body in sorted(arch.files.items()):
        if not rel.endswith((".js", ".jsx")):
            continue
        colls.update(_MAP_COLLECTION_RE.findall(body))
        if rel.startswith("app/") and rel.rsplit("/", 1)[-1].startswith("page."):
            seg = rel[len("app/"):].rsplit("/", 1)[0]
            route = "/" if seg.startswith("page.") else "/" + seg
            kind = ("client"
                    if body.lstrip()[:40].lstrip("\"'").startswith("use client")
                    else "server")
            uses = [m.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                    for m in dict.fromkeys(_MAP_IMPORT_RE.findall(body))]
            pages.append((route, rel, kind, uses))
        elif rel.startswith("app/") and rel.rsplit("/", 1)[-1].startswith("layout."):
            seg = rel[len("app/"):].rsplit("/", 1)[0]
            uses = [m.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                    for m in dict.fromkeys(_MAP_IMPORT_RE.findall(body))]
            pages.append(("/" if seg.startswith("layout.") else "/" + seg,
                          rel, "layout", uses))
        elif rel.startswith("app/api/") and rel.rsplit("/", 1)[-1].startswith("route."):
            ms = sorted(set(_MAP_METHOD_RE.findall(body)))
            apis.append(("/" + rel[len("app/"):].rsplit("/", 1)[0],
                         rel, ", ".join(ms) or "—"))
        elif rel.startswith("components/"):
            comps.add(rel)
        elif rel.startswith("lib/"):
            libs.append(rel)
    if not pages and not apis:
        return ""

    out = ["## The whole project, so you do not have to guess where things are",
           "", "### Routes"]
    for route, rel, kind, uses in sorted(pages, key=lambda p: p[0]):
        line = f"- `{route}` → {rel} ({kind})"
        if uses:
            line += " — renders " + ", ".join(uses[:6])
        out.append(line)
    out += ["", "### API", *[f"- `{r}` → {f} [{m}]" for r, f, m in sorted(apis)]]
    unused = sorted(c for c in comps
                    if not any(c.rsplit("/", 1)[-1].rsplit(".", 1)[0] in u
                               for _, _, _, us in pages for u in us))
    out += ["", "### Components",
            *[f"- {c}" for c in sorted(comps)]]
    if unused:
        out.append(f"  (rendered by no route: {', '.join(unused)})")
    if libs:
        out += ["", "### lib", *[f"- {l}" for l in sorted(libs)]]
    if colls:
        out += ["", "### MongoDB collections",
                "- " + ", ".join(sorted(colls))]
    out += ["", "A layout wraps every route beneath it — the navbar, header "
                "and footer are in a layout, never in the page."]
    return "\n".join(out)


def _shared_routes(arch, rel: str) -> list:
    """Every route `rel` appears on."""
    try:
        return routes_rendering(arch.files, rel)
    except Exception as e:
        log.debug(f"shared routes {rel}: {e}")
        return []


def _log_reach(rel: str, shared: list, route: str = "") -> None:
    """Say how far this edit reaches."""
    if len(shared) <= 1:
        return
    where = route or "this page"
    elog("WARN", f"   \U0001f310 {rel} is on {len(shared)} routes, so this changes "
                 f"all of them: {', '.join(shared[:6])}"
                 + (f" (+{len(shared) - 6} more)" if len(shared) > 6 else ""))
    elog("INFO", f"      Add \u201c\u2014 on {where} only\u201d next time to keep the "
                 f"others as they are.")


def _scope_verdict(rel: str, shared: list, instruction: str,
                   route: str = "", ask: bool = True) -> str:
    """`""` go ahead · `"scoped"` do it for this route only · `"asked"` stop."""
    if len(shared) <= 1:
        return ""
    where = route or "this page"
    if looks_like_global(instruction):
        elog("WARN", f"   🌐 {rel} is rendered on {len(shared)} routes — "
                     f"changing all of them, as asked")
        return ""
    if looks_like_page_only(instruction):
        elog("INFO", f"   📐 {rel} is on {len(shared)} routes — changing "
                     f"{where} only, as asked")
        return "scoped"
    if not ask:
        # Said, not asked.
        elog("WARN", f"   🌐 {rel} is on {len(shared)} routes, so this changes "
                     f"all of them: {', '.join(shared[:6])}"
                     + (f" (+{len(shared) - 6} more)" if len(shared) > 6 else ""))
        elog("INFO", f"      Add “— on {where} only” next time to keep the "
                     f"others as they are.")
        return ""
    elog("WARN", f"   🛑 Not done yet — that lives in {rel}, which is rendered "
                 f"on {len(shared)} routes, not just {where}:")
    elog("WARN", f"      {', '.join(shared[:8])}"
                 + (f" (+{len(shared) - 8} more)" if len(shared) > 8 else ""))
    elog("INFO", f"      Say “{instruction.strip()[:60]} — everywhere” to "
                 f"change all of them,")
    elog("INFO", f"      or “{instruction.strip()[:60]} — on {where} only” to "
                 f"keep the others as they are.")
    emit({"type": "ask", "kind": "scope", "file": rel, "routes": shared[:12],
          "route": route,
          "options": [f"{instruction.strip()[:60]} — on {where} only",
                      f"{instruction.strip()[:60]} — everywhere"]})
    eprog("Waiting for you", 0)

    edone("", "", preview=route or "/")
    return "asked"


def _reach_label(arch, rel: str, route: str = "") -> str:
    """How many routes a file is on, said plainly enough to act on."""
    try:
        reach = routes_rendering(arch.files, rel)
    except Exception as e:
        log.debug(f"reach label {rel}: {e}")
        return "wraps this route — you MAY rewrite this one"
    if len(reach) > 1:
        return (f"rendered on ALL {len(reach)} routes — editing this changes "
                f"every one of them, not just {route or 'this page'}")
    if len(reach) == 1:
        return f"{reach[0]} only — safe to edit for this route"
    return "wraps this route — you MAY rewrite this one"


def _layout_chain(arch, path: str, cap: int = 3) -> list:
    """The layouts that wrap this page, outermost last, with what they render."""
    out, seen = [], set()
    if not path.startswith("app/"):
        return out
    parts = path[len("app/"):].split("/")[:-1]

    for i in range(len(parts), -1, -1):
        stem = "/".join(["app"] + parts[:i] + ["layout"])
        for ext in (".jsx", ".js"):
            body = arch.files.get(stem + ext)
            if body and stem + ext not in seen:
                seen.add(stem + ext)
                out.append((stem + ext, body))
            if len(out) >= cap:
                return out

    for _, body in list(out):
        for spec in dict.fromkeys(LOCAL_IMPORT_RE.findall(body)):
            for ext in (".jsx", ".js", ""):
                sub = arch.files.get(f"{spec}{ext}")
                if sub and f"{spec}{ext}" not in seen:
                    seen.add(f"{spec}{ext}")
                    out.append((f"{spec}{ext}", sub))
                    break
            if len(out) >= cap + 3:
                return out
    return out


CONNECTED_BUDGET = 60_000  # Characters of related source, at most


def connected_files(arch, path: str, cap: int = 6) -> list:
    """The files this one is joined to: what it imports, and what imports it."""
    from agents import exports as _ex

    files = getattr(arch, "files", None) or {}
    body = files.get(path, "")
    out, seen = [], {path}

    for stmt in _ex.parse_imports(body):
        target = _ex.resolve_local(path, stmt.spec, files)
        if target and target not in seen:
            seen.add(target)
            out.append((target, "imported by"))

    for other, src in files.items():
        if other in seen or not other.endswith((".js", ".jsx")):
            continue
        if other.startswith(("tests/", "test/", "__tests__/")):
            continue
        if not isinstance(src, str) or len(src) > 200_000:
            continue
        for stmt in _ex.parse_imports(src):
            if _ex.resolve_local(other, stmt.spec, files) == path:
                seen.add(other)
                out.append((other, "imports it"))
                break
        if len(out) >= cap * 2:
            break
    return out[:cap]


def _neighbours(arch, path: str, before: str, cap: int = 6) -> str:
    """The connected files, in full, as prompt text."""
    files = getattr(arch, "files", None) or {}
    blocks, spent = [], 0
    for rel, how in connected_files(arch, path, cap):
        body = files.get(rel) or ""
        if not body:
            continue
        if spent + len(body) > CONNECTED_BUDGET:
            blocks.append(f"--- {rel} ({how} {path}) — too long to quote here; "
                          f"it exists and you may not break its exports ---")
            continue
        spent += len(body)
        blocks.append(f"--- {rel} ({how} {path}) ---\n{body}")
    return "\n\n".join(blocks)


MAX_ELEMENT_AUTOFIX = 2


def _autofix_from_terminal(arch, path, element, mark, rounds=MAX_ELEMENT_AUTOFIX,
                           proj_dir: Path = None, analyzer=None, model: str = None):
    """Diagnose a broken visual edit from runtime evidence, then repair it."""
    import urllib.error
    import urllib.request

    route = _route_of(element)
    if proj_dir is None:
        rounds = 0

    for rnd in range(0, max(0, rounds) + 1):
        status = None
        try:
            with urllib.request.urlopen(
                    f"http://localhost:{DEV_PORT}{route}", timeout=60) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        except Exception as e:
            log.debug(f"autofix probe {route}: {e}")

        faults = terminal_faults(_filter_db_noise(dev_log_since(mark), True))
        if not faults and (status is None or status < 400):
            if rnd:
                elog("INFO", f"   ✅ {route} is clean after the evidence repair")
            return True
        if rnd >= rounds or proj_dir is None:
            break

        detail = "\n".join(faults[:6]) or f"{route} returned HTTP {status}"
        exact = _exact_runtime_focus(arch, detail)
        focus = exact or [path]
        elog("WARN", f"   🐞 {route} broke — evidence repair ({rnd + 1}/{rounds}): "
                     f"{detail.splitlines()[0][:100]}")
        ephase({"phase": -20, "title": f"Diagnosing the edit (round {rnd + 1})",
                "status": "active"})
        try:
            helper = analyzer or AnalyzerAgent(
                arch, proj_dir, base_url=f"http://localhost:{DEV_PORT}",
                callbacks=_analyzer_callbacks())
            written = _repair_runtime(
                arch, proj_dir, None, helper, detail, detail, rnd + 1, model,
                focus_paths=focus, strict_scope=True)
            ephase({"phase": -20, "title": f"Diagnosing the edit (round {rnd + 1})",
                    "status": "done", "written": len(written or [])})
            if not written:
                elog("WARN", "   ⚠ evidence planner could not prove a safe visual-edit repair")
                break
            elog("INFO", f"   🐞 evidence repair rewrote {len(written)} file(s): "
                         + ", ".join(written[:8]))
            mark = dev_log_mark()
        except Exception as e:
            ephase({"phase": -20, "title": f"Diagnosing the edit (round {rnd + 1})",
                    "status": "done"})
            elog("WARN", f"   ⚠ evidence repair failed: {e}")
            log.exception("autofix: runtime repair")
            break

    elog("WARN", f"   ⚠ {route} is still reporting errors — leaving the fault explicit")
    return False


PICTURES_RULE = (
    "PICTURES ARE FREE — ASK FOR ONE AND IT IS DRAWN. When the request wants "
    "an image, a photo, a picture, an icon, a logo or a background, write an "
    "ordinary tag pointing into /generated/ and describe the picture in the "
    "alt text:\n"
    "    <img src=\"/generated/sourdough-loaf.png\" "
    "alt=\"a rustic sourdough loaf on a wooden board, warm morning light\" "
    "className=\"...\" />\n"
    "The file does not exist yet and that is fine — the alt text is the "
    "prompt, and every picture referenced this way is generated and written "
    "to disk the moment your edit lands. Use a short kebab-case filename and "
    "write the alt text the way you would describe the shot to a "
    "photographer: subject, setting, style, light.\n"
    "Never use a stock photo URL, never link to an external host, and never "
    "leave a placeholder box where a picture was asked for.\n"
    "PUT IT WHERE IT CAN BE SEEN. A picture that was asked for is the point of "
    "the change, so it goes in the flow of the section at full strength — no "
    "`opacity-20`, no `mix-blend-multiply`, and no gradient laid over it. This "
    "is the shape that keeps coming back and it renders as nothing at all:\n"
    "    <div className=\"absolute inset-0 z-0 opacity-20\">\n"
    "      <img … className=\"… mix-blend-multiply\" />\n"
    "      <div className=\"absolute inset-0 bg-gradient-to-b from-white "
    "to-white\" />\n"
    "    </div>\n"
    "Twenty per cent opacity under a white gradient on a white section is an "
    "invisible picture, and the person who asked for it sees no change. Only "
    "make one a faint background when the request actually said watermark, "
    "texture, or subtle background — and even then keep it above `opacity-60`. "
    "Otherwise give it real size: a hero band, a card image, a figure beside "
    "the text, something with width and height that a reader would notice.\n\n")


def _edit_rules(adding: bool) -> str:
    """The fence around a picker edit, in the two shapes it comes in."""
    shared = ("DO NOT CHANGE: api routes, entities, functions. The routes stay "
              "at the same paths with the same methods and the same request "
              "and response shapes. The data entities keep their fields and "
              "their names. Every function keeps its name, its parameters and "
              "what it does.\n\n"
              + PICTURES_RULE)
    if adding:
        return shared + (
            "ADD THE REQUESTED SECTION WHERE THEY POINTED. Preserve existing "
            "sections unless the new section genuinely needs a local import or "
            "small wiring change. Do not perform unrelated cleanup or redesign. "
            "The new section may be as large or complex as the request needs.\n\n")
    return shared + (
        "CHANGE THE SELECTED REGION COMPLETELY AS REQUESTED. Preserve unrelated "
        "sections and public contracts, but imports/helpers used by this region "
        "may change when necessary. Do not reject a correct edit because it "
        "changes many lines; verification decides correctness.\n\n")


def _element_write_round(arch, path, before, instruction, element, anchor,
                         removing, adding=False, retexting=False, attempts=2,
                         line=0):
    """Ask for the rewrite; reject anything that overran and retry once."""
    near = _neighbours(arch, path, before)
    span = _section_span(element)
    where = _where_in_file(before, element, line)
    user = (f"## The element the user clicked\n{describe(element)}\n\n"
            + (f"## Where it is\n{where}\n\n" if where else "")
            + (f"## The section they selected — it runs from the first of "
               f"these to the second\n{span}\n\n" if span else "")
            + f"Route: {element.get('route', '/')}\n\n"
            + f"## What they want\n{instruction}\n\n"
            + _edit_rules(adding)
            + f"## The complete current source of {path}\n{before}"
            + (f"\n\n## The files this one is joined to — what it "
               f"renders, and what renders it\n{near}" if near else ""))
    arch._workspace_tool_cache = {}
    convo = [{"role": "system", "content": ELEMENT_EDIT_SYSTEM + "\n\n" + TOOL_HELP},
             {"role": "user", "content": user}]

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
        buf = []

        def feed(tok):
            buf.append(tok)
            parser.feed(tok)

        try:
            reply = ""
            seen_observations = set()
            while True:
                turn_buf = []
                def feed_turn(tok):
                    turn_buf.append(tok)
                    feed(tok)
                arch._stream(convo, feed_turn, temperature=0.3,
                             timeout=arch.EDIT_TIMEOUT)
                reply = "".join(turn_buf)
                convo.append({"role": "assistant", "content": reply})
                observations, used = WorkspaceTools(arch).serve(reply)
                if used and not got["writes"]:
                    sig = observations.strip()
                    used_chars = sum(len(str(m.get("content", ""))) for m in convo)
                    try:
                        budget_chars = int(arch._budget_chars())
                    except Exception:
                        budget_chars = 0
                    if sig and sig not in seen_observations and (not budget_chars or used_chars < budget_chars * 0.82):
                        seen_observations.add(sig)
                        elog("INFO", f"   🧰 section editor inspected {used} workspace tool(s)")
                        convo.append({"role": "user", "content":
                                      "Tool observations:\n\n" + observations +
                                      "\n\nContinue the same selected-element edit. Follow dependencies as far as needed; do not repeat a tool call."})
                        continue
                    if sig in seen_observations:
                        elog("WARN", "   ↔ section editor repeated the same inspection — deciding with current evidence")
                break
        except Exception as e:
            eerr(f"The model failed: {e}")
            return False, ""
        parser.close()

        full_reply = "".join(buf)
        for out in arch.run_requested_commands(full_reply):
            elog("INFO", f"   📦 {out.splitlines()[0][:110]}")

        need = re.search(r"^\s*NEED\s+(\S+)\s*$", reply, re.M)
        if need and not got["writes"]:
            needed = need.group(1).strip('`')
            elog("INFO", f"   ↗ focused editor discovered dependency {needed} — escalating automatically")
            return False, "__AGENTFORGE_ESCALATE__:" + needed

        writes = got["writes"]
        body = writes.get(path, "")
        external = [rel for rel in writes if rel != path]
        if external:
            names = ",".join(external[:8])
            elog("INFO", "   ↗ focused editor emitted dependency write(s) — "
                         f"escalating transaction: {names}")
            return False, "__AGENTFORGE_ESCALATE__:" + names
        if writes and not body:
            only = ",".join(list(writes)[:8])
            elog("WARN", f"   ⚠ focused editor wrote {only}, not selected owner {path} — escalating")
            return False, "__AGENTFORGE_ESCALATE__:" + only
        if not body:

            head = " ".join(reply.split())[:300] or "(empty response)"

            if _DECLINED_RE.search(reply):
                elog("INFO", f"   ✅ Nothing to change — {head[:160]}")
                eerr("That element is not on this page — nothing was changed")
                return False, ""
            elog("WARN", f"   ⚠ no <write_file> block — model said: {head}")
            if attempt < attempts:
                convo.append({"role": "user", "content":
                    "That was not a file. Output the COMPLETE file inside "
                    f"one <write_file path=\"{path}\">…</write_file> block, "
                    "starting immediately with '<write_file'. No markdown "
                    "fences, no explanation, no summary."})
                continue

            # What it said, not only that it said nothing usable.
            eerr(f"The model returned no file after {attempts} attempts. "
                 f"It said: {head[:220]}")
            return False, ""

        why = None
        if not body.strip():
            why = "the rewrite is empty"
        else:
            if len(body) < 0.5 * len(before):
                elog("INFO", f"   ✂ the file goes from {len(before)} to "
                             f"{len(body)} characters — writing it as asked")
            return True, body

        elog("WARN", f"   ⛔ Rejected: {why[:110]}")
        if attempt == attempts:
            eerr("The edit changed far more than the element — nothing was "
                 "written")
            return False, ""
        convo.append({"role": "assistant", "content": reply[:2000]})
        convo.append({"role": "user", "content":
            f"That rewrite was rejected: {why}\n\nTry again. Output the "
            f"COMPLETE file, byte-identical to the original except for the "
            f"element described above."})
    return False, ""

# Builder project file operations and WebSocket actions.
SRC_ROOTS = ("app", "components", "lib")
SKIP_DIRS = {"node_modules", ".next", ".git", "dist", "out", ".vite", ".turbo",
             ".agentforge"}
SRC_EXT = {".js", ".jsx", ".css"}



def _upload_map(raw) -> dict:
    """`{key: path}` from the browser, with anything unusable dropped."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, path in list(raw.items())[:40]:
        name = str(key or "").strip()
        where = str(path or "").strip()
        if name and where and len(name) <= 60:
            out[name] = where
    return out
def _iter_source(proj_dir: Path):
    """Every source file in a project, whatever layout it uses."""
    for root in SRC_ROOTS:
        base = proj_dir / root
        if not base.is_dir():
            continue
        for fp in base.rglob("*"):
            if not fp.is_file() or fp.suffix not in SRC_EXT:
                continue
            if any(s in fp.parts for s in SKIP_DIRS):
                continue
            yield fp


def _deploy_marker(project_dir: Path) -> dict | None:
    """Whether this project was ever deployed, from disk alone."""
    agentforge = project_dir / ".agentforge"
    if (agentforge / "deploy-deleted.json").is_file():
        return {"state": "deleted", "target": ""}
    link = agentforge / "deploy" / "link.json"
    if not link.is_file():
        return None
    try:
        target = str(json.loads(link.read_text(encoding="utf-8")).get("target") or "")
    except Exception:
        target = ""
    return {"state": "deployed", "target": target}


def discard_srs(srs_id: str) -> dict:
    """Remove one staged specification."""
    sid = str(srs_id or "").strip().replace("\\", "/")
    if not sid or "/" in sid or sid in (".", "..") or sid.startswith("."):
        return {"error": f"{srs_id!r} is not a specification id"}

    root = PROD_DIR / ".srs"
    try:
        resolved = (root / sid).resolve()
        resolved.relative_to(root.resolve())
    except (ValueError, OSError):
        return {"error": f"{sid} is outside the specification store"}
    if not resolved.is_dir():
        return {"error": f"no such specification: {sid}"}

    shutil.rmtree(resolved, ignore_errors=True)
    if resolved.exists():
        return {"error": f"{sid} could not be removed"}
    elog("INFO", f"   🗑 discarded the specification {sid}")
    return {"ok": True, "srs_id": sid}


def delete_project(proj_name: str) -> dict:
    """Remove a project from disk, and its database with it."""
    name = str(proj_name or "").strip().replace("\\", "/")
    if not name or "/" in name or name in (".", "..") or name.startswith("."):
        return {"error": f"{proj_name!r} is not a project name"}

    proj_dir = PROD_DIR / name
    try:
        resolved = proj_dir.resolve()
        resolved.relative_to(PROD_DIR.resolve())
    except (ValueError, OSError):
        return {"error": f"{name} is outside production-ready"}
    if not resolved.is_dir():
        return {"error": f"no such project: {name}"}

    if active_vite.get("dir") == str(resolved):
        elog("INFO", f"   ⏹ Stopping the dev server before deleting {name}")
        _stop_dev_proc()
        _kill_port(DEV_PORT)
        active_vite["dir"] = None

    trash = PROD_DIR / f".trash-{name}-{int(time.time())}"
    try:
        resolved.rename(trash)
    except OSError as e:
        return {"error": f"could not delete {name}: {e}"}

    DEPLOY_RUNS.pop(name, None)

    def _finish():
        dropped, why = "", ""
        try:
            r = MONGO.reset_project_db(trash, node_bin=NODE_BIN)
            dropped = r.get("db", "") if r.get("ok") else ""
            why = "" if dropped else str(r.get("error", "") or "")
        except Exception as e:                                   # noqa: BLE001
            why = f"{type(e).__name__}: {e}"
        elog("INFO", f"   🗑 Deleted {name}"
                     + (f" and its database {dropped}" if dropped else
                        f" — its database was left ({why or 'no reason given'})"))
        try:
            shutil.rmtree(trash, ignore_errors=True)
        except Exception as e:                                   # noqa: BLE001
            log.debug(f"emptying {trash.name}: {e}")
        for old in PROD_DIR.glob(".trash-*"):
            if old != trash:
                shutil.rmtree(old, ignore_errors=True)

    threading.Thread(target=_finish, daemon=True).start()
    return {"ok": True, "project": name}


def list_projects() -> list:
    """Return all projects in production-ready/ with metadata."""
    projects = []
    if not PROD_DIR.exists():
        return projects
    for d in sorted(PROD_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):

        if not d.is_dir() or d.name.startswith("."):
            continue
        pkg = d / "package.json"
        title = d.name
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text())
                title = data.get("name", d.name)
            except: pass
        projects.append({
            "name": d.name,
            "title": title,
            "mtime": int(d.stat().st_mtime),
            "file_count": sum(1 for _ in _iter_source(d)),
            "stack": detect_stack(d),

            "unfinished": _unfinished_count(d),

            "deployed": _deploy_marker(d),
        })
    return projects


def _unfinished_count(proj_dir: Path) -> int:
    """Planned files with nothing on disk."""
    try:
        fp = proj_dir / ".agentforge" / "plan.json"
        if not fp.is_file():
            return 0
        plan = json.loads(fp.read_text(encoding="utf-8"))
        planned = [f.get("path", "") for ph in (plan.get("phases") or [])
                   for f in (ph.get("files") or []) if f.get("path")]
        missing = 0
        for rel in planned:
            rel = rel.lstrip("./")
            stem = re.sub(r"\.jsx?$", "", rel)
            if any((proj_dir / c).is_file()
                   for c in (rel, stem + ".js", stem + ".jsx")):
                continue
            missing += 1
        return missing
    except Exception as e:
        log.debug(f"unfinished count for {proj_dir.name}: {e}")
        return 0


FILE_PRIORITY = [
    "app/page.js", "app/page.jsx", "app/layout.js", "lib/mongodb.js",
    "app/globals.css", "next.config.mjs", "jsconfig.json",
    "package.json", "tailwind.config.js", "plan.md",
]
MAX_LISTED_FILES = 120
MAX_FILE_BYTES = 256_000


def save_project_file(proj_name: str, rel: str, content: str) -> dict:
    """Write one file a person edited in the code pane. `{"error": …}` if not."""
    proj_dir = PROD_DIR / _safe_stem(proj_name, "")
    if not proj_dir.is_dir():
        return {"error": f"no such project: {proj_name}"}

    rel = str(rel or "").replace("\\", "/").strip().lstrip("/")
    if not rel:
        return {"error": "no path given"}
    try:
        target = (proj_dir / rel).resolve()
        target.relative_to(proj_dir.resolve())
    except (ValueError, OSError):
        return {"error": f"{rel} is outside the project"}

    if target.suffix not in SRC_EXT | {".json", ".md", ".mjs", ".cjs", ".txt"}:
        return {"error": f"{target.suffix or 'that kind of file'} is not editable here"}
    if len(content) > MAX_FILE_BYTES:
        return {"error": f"{len(content):,} characters is past the "
                         f"{MAX_FILE_BYTES:,} limit"}

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
    except OSError as e:
        return {"error": f"could not write {rel}: {e}"}

    size = (f"{len(content)/1024:.1f}KB" if len(content) >= 1024
            else f"{len(content)}B")
    elog("INFO", f"   💾 {rel} saved by hand ({size})")

    efile(rel, size, content)
    return {"ok": True, "path": rel, "size": size}


def get_project_files(proj_name: str) -> dict:
    """Read project sources into a path-to-content map."""
    proj_dir = PROD_DIR / proj_name
    if not proj_dir.exists():
        return {}

    def add(files: dict, rel: str, fp: Path):
        try:
            if fp.stat().st_size > MAX_FILE_BYTES:
                return
            content = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return
        sz = (f"{len(content)/1024:.1f}KB" if len(content) >= 1024
              else f"{len(content)}B")
        files[rel] = {"content": content, "size": sz}

    files = {}
    for rel in FILE_PRIORITY:
        fp = proj_dir / rel
        if fp.exists() and rel not in files:
            add(files, rel, fp)

    for fp in sorted(_iter_source(proj_dir)):
        if len(files) >= MAX_LISTED_FILES:
            break
        rel = str(fp.relative_to(proj_dir)).replace("\\", "/")
        if rel not in files:
            add(files, rel, fp)

    for sub in ("tests/unit", "tests/e2e"):
        base = proj_dir / sub
        if not base.is_dir():
            continue
        for fp in sorted(base.rglob("*")):
            if len(files) >= MAX_LISTED_FILES:
                break
            if fp.is_file() and fp.suffix in SRC_EXT:
                rel = str(fp.relative_to(proj_dir)).replace("\\", "/")
                if rel not in files:
                    add(files, rel, fp)
    return files


async def ws_handler(websocket, path=None):
    clients.add(websocket)
    log.info(f"WS connected ({len(clients)})")
    try:
        await websocket.send(json.dumps({
            "type": "log", "level": "INFO",
            "text": "✅ AgentForge connected — enter a prompt and click Build"
        }))
        async for raw in websocket:
            try:
                msg = json.loads(raw)
                if msg.get("type") == "agent_build":
                    p  = msg.get("prompt", "").strip()
                    bm = (msg.get("builder_model") or msg.get("model")
                          or default_builder_model())
                    pm = msg.get("planner_model") or default_planner_model()
                    dm = msg.get("design_model") or default_design_model()
                    th = _think_flag(msg)
                    qm = (msg.get("qa_model") or "").strip()
                    if p:
                        threading.Thread(
                            target=run_agent_pipeline,
                            args=(p, bm, th, qm, "",
                                  str(msg.get("logo", "")).strip(),
                                  str(msg.get("srs_id", "")).strip(),
                                  str(msg.get("theme", "")).strip(),
                                  str(msg.get("theme_html", "")),
                                  str(msg.get("theme_page", "")).strip(),
                                  str(msg.get("theme_dir", "")).strip(),
                                  _upload_map(msg.get("uploads"))),
                            kwargs={"planner_model": pm,
                                    "design_model": dm},
                            daemon=True).start()
                elif msg.get("type") == "forge_build":
                    p = msg.get("prompt", "").strip()
                    bm = (msg.get("builder_model") or msg.get("model")
                          or default_builder_model())
                    qm = (msg.get("qa_model") or "").strip()
                    kinds = tuple(msg.get("kinds") or ("unit", "e2e"))
                    if p:
                        threading.Thread(
                            target=run_forge_pipeline,
                            args=(p, bm, qm,
                                  str(msg.get("project", "")).strip(), kinds),
                            daemon=True).start()
                elif msg.get("type") == "plan_decision":
                    if not forge_decide(msg.get("project", ""),
                                        str(msg.get("verdict") or "").strip(),
                                        str(msg.get("note") or "")):
                        elog("WARN", "   🧭 no run is waiting for a plan "
                                     "decision right now")
                elif msg.get("type") == "agent_resume":
                    proj = msg.get("project", "").strip()
                    bm = (msg.get("builder_model") or msg.get("model")
                          or default_builder_model())
                    pm = msg.get("planner_model") or default_planner_model()
                    dm = msg.get("design_model") or default_design_model()
                    if proj:
                        threading.Thread(
                            target=run_agent_pipeline,
                            args=("", bm, _think_flag(msg),
                                  (msg.get("qa_model") or "").strip(), proj),
                            kwargs={"planner_model": pm,
                                    "design_model": dm},
                            daemon=True).start()
                elif msg.get("type") == "chat":
                    proj = msg.get("project", "").strip()
                    p    = msg.get("prompt", "").strip()
                    am   = msg.get("model") or default_agent_model()
                    if proj and p:
                        threading.Thread(
                            target=run_chat,
                            args=(proj, p, am, (msg.get("route") or "").strip(),
                                  _think_flag(msg),
                                  (msg.get("qa_model") or "").strip(),
                                  _browser_console(msg)),
                            daemon=True).start()
                elif msg.get("type") == "agent_update":
                    proj = msg.get("project", "").strip()
                    p    = msg.get("prompt", "").strip()
                    am   = msg.get("model") or default_agent_model()
                    rt   = (msg.get("route") or "").strip()
                    if proj and p:

                        threading.Thread(
                            target=run_chat,
                            args=(proj, p, am, rt, _think_flag(msg),
                                  (msg.get("qa_model") or "").strip(),
                                  _browser_console(msg)),
                            daemon=True).start()
                elif msg.get("type") == "pencil_edit":
                    proj = msg.get("project", "").strip()
                    p    = msg.get("prompt", "").strip()
                    am   = msg.get("model") or default_agent_model()
                    if proj and p:
                        threading.Thread(
                            target=run_pencil_edit,
                            args=(proj, p, msg, am, _think_flag(msg)),
                            daemon=True).start()
                elif msg.get("type") == "element_edit":
                    proj = msg.get("project", "").strip()
                    p    = msg.get("prompt", "").strip()
                    am   = msg.get("model") or default_agent_model()
                    el   = msg.get("element") or {}
                    if proj and p:
                        threading.Thread(
                            target=run_element_edit,
                            args=(proj, p, el, am, _think_flag(msg)),
                            daemon=True).start()
                elif msg.get("type") == "image_edit":
                    proj = msg.get("project", "").strip()
                    p    = msg.get("prompt", "").strip()
                    am   = msg.get("model") or default_agent_model()
                    if proj and p:
                        threading.Thread(
                            target=run_image_edit,
                            args=(proj, p, msg.get("element") or {}, am,
                                  _think_flag(msg)),
                            daemon=True).start()
                elif msg.get("type") == "feature":
                    proj = msg.get("project", "").strip()
                    p    = msg.get("prompt", "").strip()
                    am   = msg.get("model") or default_agent_model()
                    if proj and p:
                        threading.Thread(
                            target=run_feature,
                            args=(proj, p, am, _think_flag(msg),
                                  (msg.get("qa_model") or "").strip()),
                            daemon=True).start()
            except json.JSONDecodeError: pass
    except websockets.exceptions.ConnectionClosed: pass
    finally:
        clients.discard(websocket)
        log.info(f"WS disconnected ({len(clients)})")

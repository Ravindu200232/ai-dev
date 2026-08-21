# Design generation and approved asset staging.
THEME_TIMEOUT_S = 900


def _draw_design(direction, brief, app_name, model, settings, out, lock,
                 why=None):
    """One design of the whole application, in one call.

    This is the heaviest moment in the product: five calls to the build model
    at the same instant. A busy daemon answers some of them with 503, and a
    dropped design used to leave no trace at all — five of them left the
    picker spinning with nothing to show and no reason for it.
    """
    k = direction["index"]
    raw = ""
    try:
        r = with_retry(
            lambda: ollama.chat(
                model,
                [{"role": "system", "content": themekit.SYSTEM},
                 {"role": "user", "content": themekit.user_prompt(
                     brief, app_name, direction, k, themekit.COUNT)}],
                options={"temperature": 0.9},
                timeout=THEME_TIMEOUT_S),
            what=f"the design model (direction {k})")
        raw = ((r.get("message") or {}).get("content") or "").strip()
    except Exception as e:                                      # noqa: BLE001
        log.debug(f"design {k}: {e}")
        busy = is_transient(e)
        elog("WARN", f"   ⚠ design {k} — nothing came back "
                     f"({str(e)[:70]})"
                     + ("; the model stayed busy" if busy else ""))
        if why is not None:
            with lock:
                why.append({"index": k, "busy": busy, "reason": str(e)[:200]})
        return
    html = themekit.finish(raw)
    if not html:
        elog("WARN", f"   ⚠ design {k} — the response was not HTML; skipping "
                     f"this design")
        if why is not None:
            with lock:
                why.append({"index": k, "busy": False,
                            "reason": "the model answered with something that "
                                      "was not an HTML document"})
        return
    name, blurb = themekit.meta_of(html)
    pages = themekit.pages_of(html)
    if len(name) > 36:
        blurb = blurb or name
        name = " ".join(name.split()[:3]).strip(" ,.:;—-")
    name = name or f"Design {k}"
    elog("INFO", f"   🎨 design {k} — {name}: {len(pages)} page(s) drawn"
                 + (f" ({', '.join(pages[:6])}{'…' if len(pages) > 6 else ''})"
                    if pages else ""))
    html, _ = photos.fill(html, settings, on_log=lambda l, m: elog(l, m))
    with lock:
        out.append({"id": f"design-{k}", "index": k, "name": name,
                    "blurb": blurb, "html": html, "pages": len(pages),
                    "page_names": pages, "direction": direction})


def _strip_fence(text: str) -> str:
    """Drop a html fence a model wrapped its answer in."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
    if t.endswith("```"):
        t = t.rsplit("```", 1)[0]
    return t.strip()


DESIGNS_STAGE = "designs"  # PROD_DIR/.designs/<key>/ until a build adopts
DESIGNS_KEEP_DAYS = 3


def _designs_dir() -> Path:
    d = PROD_DIR / f".{DESIGNS_STAGE}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def stage_designs(designs: list, *, source: str, srs_id: str, idea: str,
                  model: str) -> str:
    """Write the five demos to disk under the projects folder and return the key."""
    key = "des_" + uuid.uuid4().hex[:12]
    try:
        dest = _designs_dir() / key
        dest.mkdir(parents=True, exist_ok=True)
        index = {"key": key, "source": source, "srs_id": srs_id, "idea": idea[:500],
                 "model": model, "drawn_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "designs": []}
        for d in designs:
            fn = f"{d['id']}.html"
            (dest / fn).write_text(d.get("html") or "", encoding="utf-8")
            index["designs"].append({k: d.get(k) for k in
                                     ("id", "index", "name", "blurb", "pages",
                                      "page_names", "direction")} | {"file": fn})
        (dest / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False),
                                         encoding="utf-8")
        _prune_designs()
        return key
    except Exception as e:
        log.debug(f"stage designs: {e}")
        return ""


def _prune_designs(keep_days: int = DESIGNS_KEEP_DAYS) -> None:
    """Drop staged design sets nobody built from."""
    try:
        cutoff = time.time() - keep_days * 86400
        for d in _designs_dir().iterdir():
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


def staged_design_html(key: str, design_id: str) -> str:
    """The HTML of one staged design, or ""."""
    try:
        if not key or not design_id or "/" in key or "\\" in key:
            return ""
        f = _designs_dir() / key / f"{design_id}.html"
        return f.read_text(encoding="utf-8") if f.is_file() else ""
    except Exception:
        return ""


def adopt_designs(key: str, proj_dir: Path, chosen: str = "") -> bool:
    """Move a staged design set into the project it is being built for."""
    if not key or "/" in key or "\\" in key:
        return False
    src = _designs_dir() / key
    if not src.is_dir():
        return False
    dest = proj_dir / ".agentforge" / "designs"
    try:
        dest.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if f.is_file():
                shutil.copy2(f, dest / f.name)
        try:
            index = json.loads((dest / "index.json").read_text("utf-8"))
        except Exception:
            index = {"key": key, "designs": []}
        index["chosen"] = chosen or ""
        index["adopted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        (dest / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False),
                                         encoding="utf-8")
        if chosen:
            src_html = dest / f"{chosen}.html"
            if src_html.is_file():
                shutil.copy2(src_html, dest / "chosen.html")
        shutil.rmtree(src, ignore_errors=True)
        return True
    except Exception as e:
        log.debug(f"adopt designs: {e}")
        return False

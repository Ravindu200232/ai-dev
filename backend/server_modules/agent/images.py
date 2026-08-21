# Fooocus generation, uploads and image completion.
BROWSER_CONSOLE_MAX = 6000


def _browser_console(msg: dict) -> str:
    """What the preview's console had logged, as the client sent it."""
    text = str(msg.get("console") or "").strip()
    if len(text) <= BROWSER_CONSOLE_MAX:
        return text
    return "…\n" + text[-BROWSER_CONSOLE_MAX:]


def _think_flag(msg: dict):
    """The UI's thinking switch, as Ollama's tri-state."""
    v = msg.get("think")
    return None if v is None else bool(v)


def _find_fooocus_config() -> str:
    """Where Fooocus keeps its config, looked for rather than assumed."""
    inside = ("Fooocus/config.txt", "config.txt", "fooocus_config.json")
    for root in _FOOOCUS_ROOTS:
        try:
            if not root.exists():
                continue
            for folder in sorted(root.glob("Fooocus*")):
                for rel in inside:
                    candidate = folder / rel
                    if candidate.is_file():
                        return str(candidate)
                nested = folder / folder.name
                for rel in inside:
                    candidate = nested / rel
                    if candidate.is_file():
                        return str(candidate)
        except OSError:
            continue
    return ""


_FOOOCUS_ROOTS = (
    [Path(f"{d}:/") for d in "CDEFG"]
    + [Path.home() / p for p in
       ("", "Downloads", "Documents", "Desktop", "Documents/GitHub",
        "OneDrive/Documents", "OneDrive/Documents/GitHub", "OneDrive/Desktop")]
)


_FOOOCUS_LAUNCHERS = ("run_4gb.bat", "run.bat", "run_anime.bat",
                      "run_realistic.bat", "run.sh")


def _fooocus_folders() -> list:
    """Every directory that might hold a Fooocus launcher, nearest first."""
    settings = load_settings()
    out = []
    config = str(settings.get("image_config", FOOOCUS_CONFIG)).strip()
    if config:
        here = Path(config).parent
        out += [here, *list(here.parents)[:3]]
    for root in _FOOOCUS_ROOTS:
        try:
            if root.exists():
                for folder in sorted(root.glob("Fooocus*")):
                    out += [folder, folder / folder.name]
        except OSError:
            continue
    return out


def _fooocus_launcher() -> str:
    """The script that starts Fooocus on this machine, or ""."""
    explicit = str(load_settings().get("image_launcher", "")).strip()
    if explicit and Path(explicit).is_file():
        return explicit

    folders = _fooocus_folders()
    for name in _FOOOCUS_LAUNCHERS:
        for folder in folders:
            candidate = folder / name
            try:
                if candidate.is_file():
                    return str(candidate)
            except OSError:
                continue
    return ""


def start_fooocus() -> str:
    """Launch the local Fooocus."""
    script = _fooocus_launcher()
    if not script:
        return ("no Fooocus install was found — start it yourself, or set "
                "image_launcher in Settings to its run script")
    folder = Path(script).parent
    try:
        if os.name == "nt":
            subprocess.Popen(["cmd", "/c", "start", "", Path(script).name],
                             cwd=str(folder), shell=False,
                             creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            subprocess.Popen(["/bin/sh", str(script)], cwd=str(folder),
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             start_new_session=True)
    except OSError as e:
        return f"could not start {Path(script).name}: {e}"
    elog("INFO", f"   🎨 Starting Fooocus — {script}")
    return ""


FOOOCUS_CONFIG = _find_fooocus_config()


def image_agent(callbacks: dict = None) -> ImageAgent:
    """The configured Fooocus, whether or not it is switched on."""
    s = load_settings()
    return ImageAgent(host=str(s.get("image_host", "")).strip(),
                      config_path=str(s.get("image_config", FOOOCUS_CONFIG)),
                      callbacks=callbacks or _analyzer_callbacks(),
                      enabled=bool(s.get("image_enabled", False)))


def _image_settings() -> dict:
    s = load_settings()
    return {
        "image_enabled": bool(s.get("image_enabled", False)),
        "image_host": str(s.get("image_host", "")),
        "image_config": str(s.get("image_config", FOOOCUS_CONFIG)),
        "image_launcher": str(s.get("image_launcher", "")),
        "lan_access": bool(s.get("lan_access", False)),
    }


UPLOAD_IMAGE_MAX = 7_500_000
UPLOAD_IMAGE_SIDE = 2048


def _safe_stem(raw: str, fallback: str = "upload") -> str:
    """A name from the browser, reduced to something that cannot leave the folder."""
    stem = Path(str(raw or "").replace("\\", "/")).name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.")
    return (Path(stem).stem or fallback)[:60]


def save_uploaded_image(raw_b64: str, out: Path) -> str:
    """Write a browser upload to `out` as a real PNG."""
    raw = str(raw_b64 or "")
    if raw.lstrip().startswith("data:") and "," in raw[:64]:
        raw = raw.split(",", 1)[1]  # A browser data: URL
    if not raw.strip():
        return "no image was sent"
    try:
        blob = base64.b64decode(raw, validate=False)
    except Exception as e:                                      # noqa: BLE001
        return f"that is not valid base64 ({e})"
    if not blob:
        return "the image was empty"
    if len(blob) > UPLOAD_IMAGE_MAX:
        return f"the image is larger than {UPLOAD_IMAGE_MAX // 1_000_000} MB"

    try:
        from PIL import Image
    except Exception:                                           # noqa: BLE001
        # No Pillow: only a file that is already a PNG.
        if blob[:8] != b"\x89PNG\r\n\x1a\n":
            return "Pillow is not installed, so only PNG files can be uploaded"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(blob)
        return ""

    try:
        img = Image.open(io.BytesIO(blob))
        img.load()
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        img.thumbnail((UPLOAD_IMAGE_SIDE, UPLOAD_IMAGE_SIDE))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
    except Exception as e:                                      # noqa: BLE001
        return f"that file could not be read as an image ({e})"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(buf.getvalue())
    return ""


IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
AUDIO_EXT = (".wav", ".mp3", ".m4a", ".ogg", ".webm", ".flac")
ATTACH_TEXT_CAP = 6000


def read_attachment(filename: str, data_b64: str, proj_dir: Path = None) -> dict:
    """Turn one attached file into something an editing prompt can carry."""
    name = str(filename or "upload")
    lower = name.lower()
    out = {"kind": "file", "text": "", "url": "", "note": ""}

    try:
        if lower.endswith(IMAGE_EXT):
            out["kind"] = "image"
            stem = _safe_stem(name, "attached")
            where = ((proj_dir / "public" / "generated") if proj_dir
                     else (LOGS_DIR / "images")) / f"{stem}.png"
            why = save_uploaded_image(data_b64, where)
            if why:
                out["note"] = why
                return out
            out["url"] = f"/generated/{stem}.png"

            from srs_agent.app.extraction import read_image
            res = asyncio.run(read_image(base64.b64decode(_strip_data_url(data_b64)), name))
            out["text"] = (res.get("text") or "").strip()
            out["note"] = res.get("warning") or res.get("error") or ""
            return out

        raw = base64.b64decode(_strip_data_url(data_b64))

        if lower.endswith(".pdf"):
            out["kind"] = "pdf"
            from srs_agent.app.extraction import read_pdf
            res = asyncio.run(read_pdf(raw, name))
        elif lower.endswith(AUDIO_EXT):
            out["kind"] = "audio"
            from srs_agent.app.extraction import transcribe_audio
            res = transcribe_audio(raw, name)
        else:
            out["kind"] = "text"
            res = {"text": raw.decode("utf-8", "ignore")}

        out["text"] = (res.get("text") or "").strip()
        out["note"] = res.get("warning") or res.get("error") or ""
    except Exception as e:                                      # noqa: BLE001
        log.warning(f"attachment {name}: {e}")
        out["note"] = f"{name} could not be read ({e})"
    return out


def _strip_data_url(raw: str) -> str:
    raw = str(raw or "")
    if raw.lstrip().startswith("data:") and "," in raw[:64]:
        return raw.split(",", 1)[1]
    return raw


INLINE_BUDGET = 6_000_000
PREVIEW_SIDE = 900


def preview_uri(out: Path) -> str:
    """A data: URI for `out`, shrinking it rather than giving up when it is big."""
    try:
        raw = out.read_bytes()
    except OSError as e:
        log.debug(f"inline {out}: {e}")
        return ""

    if len(raw) <= INLINE_BUDGET:
        return "data:image/png;base64," + base64.b64encode(raw).decode()

    try:
        from PIL import Image

        img = Image.open(io.BytesIO(raw))
        img.load()
        img.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
    except Exception as e:                                      # noqa: BLE001
        log.debug(f"preview {out}: {e}")
        return ""
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _image_wishes(proj_dir: Path) -> list:
    """The kinds of picture the customer asked for, from the adopted interview."""
    try:
        doc = json.loads((proj_dir / ".agentforge" / "srs" / "interview.json")
                         .read_text(encoding="utf-8"))
    except Exception:
        return []
    for answer in doc.get("answers") or []:
        if answer.get("question_id") != "image_kinds":
            continue
        value = answer.get("value")
        items = value if isinstance(value, list) else [value]
        return [str(v).replace("_", " ").strip() for v in items if str(v)]
    return []


def _image_brief_line(proj_dir: Path) -> str:
    """What the planner is told about pictures — which until now was nothing."""
    agent = image_agent()
    if not agent.enabled or not agent.available():
        return ("\n\nIMAGE GENERATION IS OFF for this build. Omit the "
                "`## Images` heading, leave `\"images\"` empty in the JSON, and "
                "do not write an <img> pointing at `/generated/…` — nothing "
                "will draw it and every one of them would 404. Use Tailwind "
                "gradients, inline SVG or emoji where a picture would go.\n")

    line = ("\n\nIMAGE GENERATION IS ON for this build. Every picture you list "
            "under `## Images` is drawn by a local image model into "
            "`public/generated/<key>.png` before the app first runs, so the "
            "tags you write for them point at real files. This app is not one "
            "of the ones that should omit the heading.")
    wishes = _image_wishes(proj_dir)
    if wishes:
        line += (" The customer was asked which artwork they wanted and "
                 "answered: " + ", ".join(wishes) + ". Cover every one of "
                 "them, with a key per seeded record wherever the answer is a "
                 "photograph of a thing the app stores, so the seed can point "
                 "each row at its own picture.")
    else:
        line += (" List every picture the app is better for having — a photo "
                 "per seeded record that would carry one, a login backdrop, a "
                 "hero where the app has a public page.")
    return line + "\n"


def run_image_stage(arch, proj_dir: Path) -> int:
    """Generate the pictures the plan asked for, into the project's public folder."""
    plan_images = (arch.plan or {}).get("images") or []
    if not plan_images:
        return 0
    agent = image_agent()
    if not agent.enabled:
        elog("INFO", f"   🖼 {len(plan_images)} image(s) planned — image "
                     f"generation is off, so the app ships with the tags in "
                     f"place and the files missing")
        return 0
    if not agent.available():
        elog("WARN", "   ⚠ No Fooocus is answering — the planned images are "
                     "skipped. Start it, or set its address in Settings.")
        return 0

    ephase({"phase": -21, "title": f"Generating {len(plan_images)} image(s)",
            "status": "active"})
    out_dir = proj_dir / "public" / "generated"
    jobs = [{"key": im["key"], "prompt": im["prompt"],
             "aspect": im.get("aspect", "landscape"),
             "path": out_dir / f"{im['key']}.png"} for im in plan_images]

    def progress(done, total, key, made):
        eprog(f"Image {done}/{total}…", 78)

    results = agent.generate_many(jobs, on_done=progress)
    made = sum(1 for v in results.values() if v)
    ephase({"phase": -21, "title": f"Generating {len(plan_images)} image(s)",
            "status": "done", "written": made})
    elog("INFO" if made else "WARN",
         f"   🎨 {made}/{len(plan_images)} image(s) generated")
    return made


_GEN_IMG_RE = re.compile(r"/generated/([A-Za-z0-9._-]+)\.(?:png|jpg|jpeg|webp)")


_GEN_IMG_TPL_RE = re.compile(
    r"/generated/\$\{([^}]{1,80})\}\.(?:png|jpg|jpeg|webp)")


# `seedImage` itself strips anything outside this set
_GEN_IMG_KEY_RE = re.compile(r"[A-Za-z0-9._-]{1,60}")

_SEED_LABEL_RE = re.compile(
    r"\b(?:name|title|label|room_type|product_name|type)\s*:\s*"
    r"['\"]([^'\"]{1,80})['\"]")

# `photos: await seedImage('item-deluxe')` -- the seed's own
_SEED_IMAGE_RE = re.compile(r"seedImage\s*\(\s*['\"]([A-Za-z0-9._-]{1,60})['\"]")

# `photo: await seedImage(`item-${row.slug}`)` -- the same
_SEED_IMAGE_TPL_RE = re.compile(
    r"seedImage\s*\(\s*`([A-Za-z0-9._-]{0,40})\$\{([^}]{1,60})\}"
    r"([A-Za-z0-9._-]{0,40})`")


def _seeded_values(arch, field: str) -> dict:
    """`{value: label}` for every literal `field: '…'` in the project's seed."""
    field_re = re.compile(r"\b" + re.escape(field) + r"\s*:\s*['\"]([^'\"]{1,80})['\"]")
    out = {}
    for rel, body in arch.files.items():
        if "seed" not in rel.lower() or not rel.endswith(".js"):
            continue
        for m in field_re.finditer(body):
            value = m.group(1).strip()
            if not value or "/" in value:
                continue
            open_at = body.rfind("{", 0, m.start())
            close_at = body.find("}", m.end())
            span = body[(open_at + 1 if open_at != -1 else 0):
                        (close_at if close_at != -1 else len(body))]
            label = _SEED_LABEL_RE.search(span)
            out.setdefault(value, label.group(1).strip() if label else "")
    return out


_ACCOUNT_FIELD_RE = re.compile(
    r"\b(?:email|password|passwordHash|role|emailVerified)\s*:", re.I)


def _account_values(arch, field: str) -> set:
    """Values of `field` that belong to a seeded ACCOUNT row."""
    field_re = re.compile(r"\b" + re.escape(field) + r"\s*:\s*['\"]([^'\"]{1,80})['\"]")
    out = set()
    for rel, body in (getattr(arch, "files", None) or {}).items():
        if "seed" not in str(rel).lower() or not str(rel).endswith(".js"):
            continue
        for m in field_re.finditer(body):
            open_at = body.rfind("{", 0, m.start())
            close_at = body.find("}", m.end())
            span = body[(open_at + 1 if open_at != -1 else 0):
                        (close_at if close_at != -1 else len(body))]
            if _ACCOUNT_FIELD_RE.search(span):
                out.add(m.group(1).strip())
    for account in ((getattr(arch, "plan", None) or {}).get("demo_accounts") or []):
        name = str((account or {}).get("name") or "").strip()
        if name:
            out.add(name)
    return out


def _template_wants(arch, body: str, at: int, prefix: str, expr: str,
                    suffix: str, rel: str, *, strict: bool = True) -> dict:
    """Every key one `${…}` image template really stands for.

    The rows are read from the seed the build wrote, so a key derived at
    runtime (`slug: slugify(d.name)`) resolves the same way it will there.
    When even that finds nothing, one shared picture is drawn under the
    template's own stem so the page shows something instead of a broken img.
    """
    shown = f"{prefix}${{{expr.strip()}}}{suffix}"
    try:
        found = template_keys(arch, body, at, prefix, expr, suffix,
                              strict=strict)
    except Exception as e:                                      # noqa: BLE001
        log.debug(f"template key resolution failed for {shown}: {e}")
        found = {}
    if found:
        elog("INFO", f"   🖼 {rel} wants {len(found)} picture(s) from "
                     f"`{shown}` — {', '.join(sorted(found)[:4])}"
                     + (" …" if len(found) > 4 else ""))
        return {k: (v or k) for k, v in found.items()}

    stem = family_key(prefix, suffix)
    if stem and _GEN_IMG_KEY_RE.fullmatch(stem):
        elog("WARN", f"   🖼 {rel} builds picture names from `{shown}` and the "
                     f"seed does not spell them out — drawing one shared "
                     f"`{stem}` so the page still renders")
        return {stem: stem.replace("-", " ").replace("_", " ")}
    elog("WARN", f"   🖼 {rel} builds picture names from `{shown}` and neither "
                 f"the rows nor a shared name could be worked out — those "
                 f"images will 404")
    return {}


_IMG_TAG_RE = re.compile(
    r"<(?:Image|img)\b[^>]*?>", re.S | re.I)
_ALT_RE = re.compile(r"""\balt\s*=\s*["'{]\s*([^"'}]{3,120})""")

IMAGE_STYLE = ("photographic, natural light, shallow depth of field, "
               "no text, no watermark, no people looking at the camera")


def _fill_missing_images(arch, proj_dir: Path, why: str = "an edit", *,
                         explicit_request: bool = False) -> int:
    """Draw any picture the app now asks for and does not have."""
    wanted = {}
    for rel, body in arch.files.items():
        if not rel.endswith((".jsx", ".js", ".css")):
            continue
        for tag in _IMG_TAG_RE.findall(body):
            names = _GEN_IMG_RE.findall(tag)
            if not names:
                continue
            alt = _ALT_RE.search(tag)
            for name in names:
                wanted.setdefault(name, (alt.group(1).strip() if alt else "",
                                         rel))

        for name in _GEN_IMG_RE.findall(body):
            wanted.setdefault(name, ("", rel))

        # `seedImage('<key>')` is how the builder is told to attach
        for m in _SEED_IMAGE_TPL_RE.finditer(body):
            prefix, expr, suffix = m.groups()
            for key, label in _template_wants(arch, body, m.start(), prefix,
                                              expr, suffix, rel).items():
                wanted.setdefault(key, (label, rel))

        for m in _SEED_IMAGE_RE.finditer(body):
            key = m.group(1)
            open_at = body.rfind("{", 0, m.start())
            close_at = body.find("}", m.end())
            span = body[(open_at + 1 if open_at != -1 else 0):
                        (close_at if close_at != -1 else len(body))]
            label = _SEED_LABEL_RE.search(span)
            wanted.setdefault(key, (label.group(1).strip() if label else "",
                                    rel))

        for m in _GEN_IMG_TPL_RE.finditer(body):
            expr = m.group(1)
            field = expr.strip().split(".")[-1].strip()
            found = _template_wants(arch, body, m.start(), "", expr, "",
                                    rel, strict=False)
            accounts = _account_values(arch, field) if field.isidentifier() else set()
            skipped = [k for k in found if k in accounts]
            for key, label in found.items():
                if key not in accounts:
                    wanted.setdefault(key, (label, rel))
            if skipped:
                elog("INFO", f"   🖼 {len(skipped)} seeded account(s) "
                             f"are not given a generated portrait: "
                             + ", ".join(sorted(skipped)[:4]))

    out_dir = proj_dir / "public" / "generated"
    missing = {n: v for n, v in wanted.items()
               if not (out_dir / f"{n}.png").is_file()}
    if not missing:
        return 0

    agent = image_agent()
    if explicit_request:
        agent.enabled = True
        if not agent.available():
            why_start = start_fooocus()
            if not why_start:
                agent._checked = None
                for _ in range(20):
                    if agent.available():
                        break
                    time.sleep(1)
            else:
                elog("WARN", f"   ⚠ Fooocus could not be started: {why_start}")
    if not agent.enabled or not agent.available():
        elog("WARN", f"   🖼 {len(missing)} picture(s) {why} added are not "
                     f"drawn — image generation is off or no Fooocus is "
                     f"answering: {', '.join(sorted(missing))}")
        return 0

    idea = (arch.plan or {}).get("description") or (arch.plan or {}).get("title") or ""
    ephase({"phase": -21, "title": f"Drawing {len(missing)} picture(s)",
            "status": "active"})

    # One request at a time was costing a build minutes it did
    jobs = []
    for name, (alt, _rel) in sorted(missing.items()):
        subject = alt or name.replace("-", " ").replace("_", " ")
        prompt = (f"{subject}, for {idea[:80]}, {IMAGE_STYLE}" if idea
                  else f"{subject}, {IMAGE_STYLE}")
        jobs.append({"key": name, "prompt": prompt, "aspect": "landscape",
                     "path": out_dir / f"{name}.png"})

    def progress(done, total, key, drawn):
        eprog(f"Picture {done}/{total}…", 78)

    # No announcement here. `ImageAgent.generate` already logs
    try:
        results = agent.generate_many(jobs, on_done=progress)
    except Exception as e:
        elog("WARN", f"   ⚠ picture generation failed: {e}")
        log.debug("fill images", exc_info=True)
        results = {}

    made = sum(1 for ok in results.values() if ok)
    for name in sorted(n for n, ok in results.items() if not ok):
        elog("WARN", f"   ⚠ {name}.png could not be drawn")
    ephase({"phase": -21, "title": f"Drawing {len(missing)} picture(s)",
            "status": "done", "written": made})
    elog("INFO" if made else "WARN",
         f"   🖼 {made}/{len(missing)} picture(s) drawn for {why}")
    return made


def check_seed_duplicates(proj_dir: Path) -> list:
    """Rows the seed wrote more than once, counted in the live database."""
    try:
        from pymongo import MongoClient
    except ImportError:
        return []
    name = proj_dir.name
    try:
        db = MongoClient(MONGO.uri_for(name),
                         serverSelectionTimeoutMS=5000)[db_name_for(name)]
        collections = [c for c in db.list_collection_names()
                       if c not in ("user", "session", "account",
                                    "verification", "jwks")]
    except Exception as e:
        log.debug(f"seed duplicate check: {e}")
        return []

    out = []
    for coll in collections:
        try:

            sample = db[coll].find_one()
            if not sample:
                continue
            keys = [k for k in sample
                    if k not in ("_id", "createdAt", "updatedAt", "date")]
            if not keys:
                continue
            dupes = list(db[coll].aggregate([
                {"$group": {"_id": {k: f"${k}" for k in keys},
                            "n": {"$sum": 1}}},
                {"$match": {"n": {"$gt": 1}}},
                {"$sort": {"n": -1}},
                {"$limit": 3},
            ], maxTimeMS=8000))
        except Exception as e:
            log.debug(f"seed duplicates in {coll}: {e}")
            continue
        if dupes:
            worst = dupes[0]["n"]
            total = db[coll].count_documents({})
            out.append(f"{coll}: {total} row(s), and the seed's data is "
                       f"repeated up to {worst} times — every restart writes "
                       f"it again")
    return out


_UPLOAD_KEY_RE = re.compile(r"[A-Za-z0-9._-]{1,60}")


def adopt_uploaded_images(uploads, proj_dir: Path) -> int:
    """Put the person's own pictures where a generated one would go.

    `uploads` is `{key: path}`. Every key that lands here is a key the
    drawing pass will find already present and leave alone, so choosing to
    upload really does replace generating rather than racing it.
    """
    rows = dict(uploads or {})
    if not rows:
        return 0
    out_dir = Path(proj_dir) / "public" / "generated"
    taken, refused = [], []
    for key, src in rows.items():
        name = str(key or "").strip()
        if not _UPLOAD_KEY_RE.fullmatch(name):
            refused.append(f"{key!r} is not a usable picture name")
            continue
        try:
            path = Path(str(src or ""))
            if not path.is_file():
                refused.append(f"{name}: that file is gone")
                continue
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{name}.png").write_bytes(path.read_bytes())
            taken.append(name)
        except OSError as e:
            refused.append(f"{name}: {e}")
    if taken:
        elog("INFO", f"   🖼 Using {len(taken)} picture(s) you uploaded — "
                     + ", ".join(sorted(taken)[:5])
                     + (" …" if len(taken) > 5 else "")
                     + ". These will not be drawn.")
    for why in refused[:4]:
        elog("WARN", f"   ⚠ Could not use an uploaded picture — {why}")
    return len(taken)

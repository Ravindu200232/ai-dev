"""Generated images, from a Fooocus running on this machine or another one."""
import base64
import hashlib
import json
import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

log = logging.getLogger("images")


class _NoQueue(Exception):
    """This Gradio has no WebSocket queue — try the newer HTTP protocol."""


DEFAULT_HOSTS = ("http://127.0.0.1:7865", "http://127.0.0.1:7860")


PROMPT_LABEL = None
NEGATIVE_LABEL = "Negative Prompt"
ASPECT_LABEL = "Aspect Ratios"
COUNT_LABEL = "Image Number"
SEED_LABEL = "Seed"


SAFE_RE = re.compile(r"[^a-z0-9]+")

GENERATE_TIMEOUT = 600


class ImageAgent:
    """One Fooocus, wherever it is running."""

    def __init__(self, host: str = "", config_path: str = "",
                 callbacks: dict = None, enabled: bool = True):
        self.host = (host or "").rstrip("/")
        self.config_path = config_path or ""
        self.cb = callbacks or {}
        self.enabled = enabled
        self._payload = None
        self._fn_index = None
        self._gallery_index = None
        self._checked = None
        self._hosts = None
        self._lock = threading.Lock()
        # Sha256 -> the file already written with those exact bytes.
        self._seen_bytes = {}

    def hosts(self) -> list:
        """Every Fooocus that answers, in the order they were configured."""
        if self._hosts is not None:
            return self._hosts
        raw = [h.strip().rstrip("/") for h in
               re.split(r"[,\s;]+", self.host or "") if h.strip()]
        candidates = raw or list(DEFAULT_HOSTS)
        alive = []
        for url in candidates:
            try:
                r = requests.get(f"{url}/config", timeout=4)
                if r.status_code == 200 and "components" in r.text[:400]:
                    alive.append(url)
            except requests.RequestException:
                continue
            # An unconfigured run only needs the first default that answers.
            if alive and not raw:
                break
        self._hosts = alive
        if alive and self._checked is None:
            self._checked = alive[0]
        if len(alive) > 1:
            self._log("INFO", f"   🎨 {len(alive)} Fooocus hosts answering — "
                              f"images will be generated across all of them")
        return alive

    @staticmethod
    def seed_for(key: str) -> int:
        """A stable, distinct seed for a named image."""
        digest = hashlib.sha256(str(key or "").encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big") % (2 ** 31 - 2) + 1

    def _fire(self, name, *a):
        fn = self.cb.get(name)
        if fn:
            try:
                fn(*a)
            except Exception as e:
                log.warning(f"callback {name} failed: {e}")

    def _log(self, lvl, txt):
        self._fire("on_log", lvl, txt)
        log.info(txt)

    def base_url(self) -> str:
        """The Fooocus that answers, or '' when none does."""
        if self._checked is not None:
            return self._checked
        alive = self.hosts()
        self._checked = alive[0] if alive else ""
        return self._checked

    def available(self) -> bool:
        return bool(self.enabled and self.base_url())

    def _load_template(self) -> bool:
        """Read Fooocus's UI description and keep the defaults for every input."""
        if self._payload is not None:
            return True
        cfg = None
        url = self.base_url()
        if url:
            try:
                cfg = requests.get(f"{url}/config", timeout=10).json()
            except Exception as e:
                log.debug(f"live config: {e}")
        if cfg is None and self.config_path:
            try:
                cfg = json.loads(Path(self.config_path).read_text(encoding="utf-8"))
            except Exception as e:
                log.debug(f"config file: {e}")
        if not cfg:
            return False

        comps = {c["id"]: c for c in cfg.get("components", [])}
        deps = cfg.get("dependencies", [])

        idx = max(range(len(deps)),
                  key=lambda i: len(deps[i].get("inputs") or []),
                  default=None)
        if idx is None or len(deps[idx].get("inputs") or []) < 50:
            return False

        values, labels = [], {}
        for pos, cid in enumerate(deps[idx]["inputs"]):
            props = (comps.get(cid) or {}).get("props") or {}
            values.append(props.get("value"))
            label = props.get("label")
            if label and label not in labels:
                labels[label] = pos

        self._payload, self._fn_index, self._labels = values, idx, labels

        gallery = {cid for cid, c in comps.items() if c.get("type") == "gallery"}
        self._gallery_index = next(
            (i for i, d in enumerate(deps)
             if d.get("trigger_after") == idx
             and any(o in gallery for o in (d.get("outputs") or []))),
            None)

        chain, seen, at = [], set(), idx
        while at is not None and at not in seen:
            seen.add(at)
            prev = deps[at].get("trigger_after")
            if prev is None or not isinstance(prev, int) or prev >= len(deps):
                break
            chain.append(prev)
            at = prev

        self._prelude = []
        for i in reversed(chain):
            d = deps[i]
            self._prelude.append((i, [
                ((comps.get(cid) or {}).get("props") or {}).get("value")
                for cid in (d.get("inputs") or [])
            ]))
        return True

    def _slot(self, label: str, fallback: int) -> int:
        return getattr(self, "_labels", {}).get(label, fallback)

    ASPECTS = {
        "banner":   "1664*576",
        "wide":     "1344*768",
        "landscape": "1152*896",
        "square":   "1024*1024",
        "portrait": "896*1152",
        "poster":   "832*1216",
    }

    def generate(self, prompt: str, out_path: Path, *, aspect: str = "landscape",
                 seed: int = 0, force: bool = False, host: str = "",
                 unique: bool = True, _attempt: int = 0) -> bool:
        """Make one image and write it to `out_path`."""
        out_path = Path(out_path)
        if not force and out_path.is_file() and out_path.stat().st_size > 1024:
            return True
        url = host or self.base_url()
        if not url:
            return False
        if not self._load_template():
            self._log("WARN", "   ⚠ could not read the Fooocus UI description")
            return False

        args = list(self._payload)
        args[2] = prompt
        i = self._slot(ASPECT_LABEL, 6)

        want = self.ASPECTS.get(aspect, self.ASPECTS["landscape"])
        cur = args[i]
        if isinstance(cur, str):
            args[i] = re.sub(r"^\d+[*×]\d+", want.replace("*", "×"), cur) \
                if re.match(r"^\d+[*×]\d+", cur) else cur
        args[self._slot(COUNT_LABEL, 7)] = 1
        use_seed = seed if seed else random.randint(1, 2 ** 31 - 1)
        if _attempt:
            # A retry must not ask for the picture that was just rejected.
            use_seed = (use_seed + _attempt * 7919) % (2 ** 31 - 2) + 1
        args[self._slot(SEED_LABEL, 9)] = str(use_seed)

        self._log("INFO", f"   🎨 {out_path.name} — {prompt[:60]}")
        t0 = time.time()
        data = self._predict(url, args)
        if data is None:
            return False

        raw = self._first_image(data)
        if not raw:
            self._log("WARN", "   ⚠ Fooocus returned no image")
            return False

        if unique:
            digest = hashlib.sha256(raw).hexdigest()
            with self._lock:
                twin = self._seen_bytes.get(digest)
                if twin and twin != str(out_path):
                    duplicate = True
                else:
                    self._seen_bytes[digest] = str(out_path)
                    duplicate = False
            if duplicate:
                if _attempt < 2:
                    self._log("INFO", f"   ♻ {out_path.name} came back "
                                      f"identical to {Path(twin).name} — "
                                      f"drawing it again with another seed")
                    return self.generate(prompt, out_path, aspect=aspect,
                                         seed=use_seed, force=True, host=url,
                                         unique=unique, _attempt=_attempt + 1)
                self._log("WARN", f"   ⚠ {out_path.name} keeps matching "
                                  f"{Path(twin).name} — keeping it rather "
                                  f"than spending more GPU on it")
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(raw)
        except OSError as e:
            self._log("WARN", f"   ⚠ could not write {out_path.name}: {e}")
            return False
        self._log("INFO", f"   ✅ {out_path.name} in {time.time() - t0:.0f}s")
        return True

    def generate_many(self, jobs, *, per_host: int = 2, force: bool = False,
                      on_done=None) -> dict:
        """Draw a whole set of pictures at once. `{key: bool}`."""
        jobs = [dict(j) for j in (jobs or []) if j.get("prompt") and j.get("path")]
        if not jobs:
            return {}
        hosts = self.hosts()
        if not hosts:
            return {j["key"]: False for j in jobs}

        results, todo = {}, []
        for job in jobs:
            path = Path(job["path"])
            if not force and path.is_file() and path.stat().st_size > 1024:
                results[job["key"]] = True          # Already on disk
                continue
            todo.append(job)

        # One generation per distinct prompt
        primary, twins = {}, []
        for job in todo:
            sig = (job["prompt"].strip(), job.get("aspect", "landscape"))
            if sig in primary:
                twins.append((job, primary[sig]))
            else:
                primary[sig] = job
        if twins:
            self._log("INFO", f"   ♻ {len(twins)} image(s) repeat a prompt "
                              f"already being drawn — they will share it "
                              f"instead of running again")

        queue = list(primary.values())
        workers = max(1, min(len(queue), len(hosts) * max(1, per_host)))
        done = 0
        total = len(queue)

        def run(index_job):
            index, job = index_job
            return job, self.generate(
                job["prompt"], Path(job["path"]),
                aspect=job.get("aspect", "landscape"),
                seed=self.seed_for(job["key"]),
                force=force,
                host=hosts[index % len(hosts)])

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for job, made in pool.map(run, enumerate(queue)):
                results[job["key"]] = bool(made)
                done += 1
                if on_done:
                    try:
                        on_done(done, total, job["key"], bool(made))
                    except Exception as e:
                        log.debug(f"image progress callback: {e}")

        for job, source in twins:
            src = Path(source["path"])
            if results.get(source["key"]) and src.is_file():
                try:
                    dst = Path(job["path"])
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(src.read_bytes())
                    results[job["key"]] = True
                    continue
                except OSError as e:
                    log.debug(f"twin copy {job['key']}: {e}")
            results[job["key"]] = False
        return results

    def _predict(self, url: str, args: list):
        """Run the generate handler and return its final output list."""
        ws_url = url.replace("http://", "ws://").replace("https://", "wss://")
        session = "agentforge-" + str(int(time.time() * 1000))
        try:

            for fn, defaults in getattr(self, "_prelude", []):
                self._predict_ws(ws_url, session, list(defaults), fn)

            task = self._predict_ws(ws_url, session, args, self._fn_index)
            if task is None:
                return None
            if self._gallery_index is None:
                return task
            return self._predict_ws(ws_url, session, list(task),
                                    self._gallery_index)
        except _NoQueue:
            pass
        except Exception as e:
            self._log("WARN", f"   ⚠ Fooocus queue failed: {e}")
            return None

        payload = {"fn_index": self._fn_index, "data": args,
                   "session_hash": session}
        try:
            join = requests.post(f"{url}/queue/join", json=payload, timeout=30)
            if not join.ok:
                self._log("WARN", f"   ⚠ Fooocus {join.status_code}: "
                                  f"{join.text[:140]}")
                return None
            stream = requests.get(f"{url}/queue/data",
                                  params={"session_hash": session},
                                  stream=True, timeout=GENERATE_TIMEOUT)
            for line in stream.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                ev = json.loads(line[5:].strip())
                if ev.get("msg") == "process_completed":
                    out = ev.get("output") or {}
                    return None if out.get("error") else (out.get("data") or [])
        except Exception as e:
            self._log("WARN", f"   ⚠ Fooocus did not answer: {e}")
        return None

    def _predict_ws(self, ws_url: str, session: str, args: list,
                    fn_index: int):
        """The Gradio 3.x queue handshake."""
        import asyncio

        try:
            import websockets
        except ImportError:
            raise _NoQueue("websockets is not installed")

        async def go():
            try:
                conn = await asyncio.wait_for(
                    websockets.connect(f"{ws_url}/queue/join",
                                       max_size=None, open_timeout=20),
                    timeout=25)
            except Exception as e:
                raise _NoQueue(str(e))
            async with conn as ws:
                deadline = time.time() + GENERATE_TIMEOUT
                last = -1
                while time.time() < deadline:
                    raw = await asyncio.wait_for(ws.recv(),
                                                 timeout=GENERATE_TIMEOUT)
                    ev = json.loads(raw)
                    msg = ev.get("msg")
                    if msg == "send_hash":
                        await ws.send(json.dumps({"session_hash": session,
                                                  "fn_index": fn_index}))
                    elif msg == "send_data":
                        await ws.send(json.dumps({
                            "fn_index": fn_index, "data": args,
                            "session_hash": session}))
                    elif msg == "process_completed":
                        out = ev.get("output") or {}
                        if out.get("error"):
                            self._log("WARN", f"   ⚠ Fooocus: "
                                              f"{str(out['error'])[:140]}")
                            return None
                        return out.get("data") or []
                    elif msg in ("process_generating", "progress"):
                        pd = (ev.get("progress_data") or [{}])[0]
                        pct = int(pd.get("index") or 0)
                        if pct and pct != last and pct % 10 == 0:
                            last = pct
                            self._fire("on_progress", f"Image {pct}%", 78)
                self._log("WARN", "   ⚠ Fooocus took too long")
                return None

        return asyncio.run(go())

    def _first_image(self, data) -> bytes:
        """The bytes of the first image in a Gradio response."""
        def walk(node):
            if isinstance(node, str):
                if node.startswith("data:image"):
                    try:
                        return base64.b64decode(node.split(",", 1)[1])
                    except Exception:
                        return None
                if node.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    return self._fetch(node)
                return None
            if isinstance(node, dict):

                for key in ("value", "name", "path", "url", "data"):
                    if key not in node:
                        continue
                    got = walk(node[key])
                    if got:
                        return got
                return None
            if isinstance(node, (list, tuple)):
                for item in node:
                    got = walk(item)
                    if got:
                        return got
            return None

        return walk(data)

    def _fetch(self, ref: str) -> bytes:
        """A file Fooocus reported by path — read it, or pull it over HTTP."""
        p = Path(ref)
        if p.is_file():
            try:
                return p.read_bytes()
            except OSError:
                pass
        url = self.base_url()
        if not url:
            return b""
        for path in (f"/file={ref}", f"/file/{ref}", ref):
            try:
                r = requests.get(url + path if path.startswith("/") else path,
                                 timeout=60)
                if r.status_code == 200 and r.content[:4] in (b"\x89PNG", b"\xff\xd8\xff\xe0",
                                                              b"RIFF"):
                    return r.content
            except requests.RequestException:
                continue
        return b""

    @staticmethod
    def slug(text: str, limit: int = 40) -> str:
        """A filename for a prompt, stable across runs so caching works."""
        s = SAFE_RE.sub("-", (text or "image").lower()).strip("-")
        return (s[:limit].rstrip("-") or "image")

"""Unified Ollama client — local daemon + Ollama Cloud."""
import json
import logging
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

log = logging.getLogger("ollama")

DEFAULT_LOCAL_HOST = "http://localhost:11434"
CLOUD_HOST = "https://ollama.com"

SETTINGS_PATH = Path.home() / ".agentforge" / "settings.json"


FALLBACK_CLOUD = [
    "qwen3-coder:480b-cloud", "deepseek-v3.1:671b-cloud",
    "gpt-oss:120b-cloud", "kimi-k2:1t-cloud", "glm-4.6:cloud",
    "minimax-m2:cloud",
]


CLOUD_DEFAULT_CTX = 131072

LOCAL_DEFAULT_CTX = 16384

_ICONS = [
    ("coder", "⚛"), ("code", "⚛"), ("qwen", "⚛"), ("kimi", "🌙"),
    ("deepseek", "🔍"), ("gpt-oss", "🌀"), ("glm", "🧊"), ("minimax", "🎯"),
    ("nemotron", "🟩"), ("gemma", "💎"), ("llama", "🦙"), ("mistral", "⚡"),
    ("phi", "🔬"), ("vl", "👁"),
]


def _icon_for(model_id: str) -> str:
    m = model_id.lower()
    for key, icon in _ICONS:
        if key in m:
            return icon
    return "🧠"


def _pretty_label(model_id: str) -> str:
    """`qwen3.5:397b-cloud` → `Qwen3.5 397B`."""
    base = re.sub(r"[-:]cloud$", "", model_id.strip())
    base = base.split("/")[-1]
    parts = re.split(r"[:\-]", base)
    out = []
    for p in parts:
        if not p or p == "latest":
            continue
        if re.fullmatch(r"\d+(\.\d+)?[bmt]", p, re.I):
            out.append(p.upper())
        elif p.lower() in ("vl", "moe", "gpt", "oss", "glm"):
            out.append(p.upper())
        else:
            out.append(p[:1].upper() + p[1:])
    return " ".join(out) or model_id


def is_cloud_model(model: str) -> bool:
    """True when the model name marks it as an Ollama Cloud model."""
    if not model:
        return False
    m = model.strip().lower()
    if m.endswith("-cloud") or m.endswith(":cloud"):
        return True

    return model.strip() in _default_client().remote_names()


def max_context(model: str) -> int:
    """Context window to request for this model."""
    real = _default_client().model_context(model)

    if is_cloud_model(model):
        return real or CLOUD_DEFAULT_CTX

    override = (os.environ.get("AGENTFORGE_NUM_CTX", "").strip()
                or str(load_settings().get("local_num_ctx", "")).strip())
    want = max(4096, int(override)) if override.isdigit() else LOCAL_DEFAULT_CTX
    return min(want, real) if real else want


def load_settings() -> dict:
    """Read ~/.agentforge/settings.json."""
    try:
        if SETTINGS_PATH.exists():
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"settings read failed: {e}")
    return {}


def save_settings(data: dict) -> bool:
    """Merge-write ~/.agentforge/settings.json."""
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        current = load_settings()
        current.update(data)
        SETTINGS_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        log.warning(f"settings write failed: {e}")
        return False


def get_api_key() -> str:
    """API key from env first, then the settings file."""
    return (os.environ.get("OLLAMA_API_KEY", "").strip()
            or str(load_settings().get("ollama_api_key", "")).strip())


def get_local_host() -> str:
    """Local daemon URL — env override wins, then settings, then default."""
    host = (os.environ.get("OLLAMA_HOST", "").strip()
            or str(load_settings().get("ollama_host", "")).strip()
            or DEFAULT_LOCAL_HOST)
    if not host.startswith("http"):
        host = f"http://{host}"
    return host.rstrip("/")



# A busy daemon is not a bad answer. These are the shapes it comes back in.
TRANSIENT_STATUS = {429, 500, 502, 503, 504, 529}
TRANSIENT_TEXT = re.compile(
    r"overload|temporarily|try again|too many requests|unavailable|"
    r"connection reset|connection aborted|broken pipe|timed out|timeout|"
    r"no output for|sent nothing for", re.I)

RETRY_ATTEMPTS = 4
RETRY_BASE_DELAY = 2.5
RETRY_MAX_DELAY = 30.0


def is_transient(error) -> bool:
    """Whether this failure is the daemon being busy rather than a real answer."""
    if isinstance(error, int):
        return error in TRANSIENT_STATUS
    text = str(error or "")
    m = re.search(r"Ollama\s+(\d{3})\b", text)
    if m and int(m.group(1)) in TRANSIENT_STATUS:
        return True
    if m and int(m.group(1)) not in TRANSIENT_STATUS:
        return False
    return bool(TRANSIENT_TEXT.search(text))


def retry_delay(attempt: int) -> float:
    """Back off the way a person would: wait longer each time, not forever."""
    delay = RETRY_BASE_DELAY * (2 ** max(0, attempt - 1))
    return min(RETRY_MAX_DELAY, delay) * (0.75 + random.random() * 0.5)


def with_retry(call, *, what: str = "the model", attempts: int = RETRY_ATTEMPTS):
    """Run `call()`, waiting out a busy daemon instead of believing it."""
    last = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return call()
        except Exception as e:                                  # noqa: BLE001
            last = e
            if not is_transient(e) or attempt >= attempts:
                raise
            wait = retry_delay(attempt)
            log.warning(f"{what} is busy ({str(e)[:90]}) — waiting "
                        f"{wait:.0f}s, attempt {attempt + 1}/{attempts}")
            time.sleep(wait)
    raise last


class OllamaClient:
    """Thin requests wrapper that transparently routes local vs cloud."""

    def __init__(self, host: str = None, api_key: str = None):
        self.host = (host or get_local_host()).rstrip("/")
        self._api_key = api_key
        self._tags_cache = None
        self._cloud_tags_cache = None
        self._show_cache = {}

    @property
    def api_key(self) -> str:

        return self._api_key if self._api_key is not None else get_api_key()

    def route(self, model: str):
        """Return (base_url, headers) for this model."""
        headers = {"Content-Type": "application/json"}
        if is_cloud_model(model) and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            return CLOUD_HOST, headers
        return self.host, headers

    def describes_cloud(self, model: str) -> bool:
        return is_cloud_model(model)

    @staticmethod
    def _rejected_think(status: int, text: str) -> bool:
        """Whether this 4xx is the daemon saying the model cannot think."""
        return status == 400 and "think" in (text or "").lower()

    def chat_stream(self, model: str, messages: list, tools: list = None,
                    options: dict = None, keep_alive=None, timeout: int = 1800,
                    think: bool = None, stall: int = 300):
        """Yield decoded chunks from /api/chat with stream=True."""
        base, headers = self.route(model)
        payload = {"model": model, "messages": messages, "stream": True}
        if tools:
            payload["tools"] = tools
        if options:
            payload["options"] = options
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        if think is not None:
            payload["think"] = bool(think)

        r = requests.post(f"{base}/api/chat", json=payload, headers=headers,
                          stream=True, timeout=(15, stall))

        if r.status_code >= 400:
            body = r.text
            if self._rejected_think(r.status_code, body) and "think" in payload:
                log.info(f"{model} has no thinking mode — retrying without it")
                payload.pop("think")
                r = requests.post(f"{base}/api/chat", json=payload,
                                  headers=headers, stream=True,
                                  timeout=(15, stall))
                body = r.text if r.status_code >= 400 else ""
            if r.status_code >= 400:
                raise RuntimeError(
                    f"Ollama {r.status_code} @ {base}: {body[:300]}")

        deadline = time.time() + timeout
        chunks = 0
        try:
            for line in r.iter_lines():
                if time.time() > deadline:
                    log.warning(f"{model}: stopping the stream after {timeout}s "
                                f"and {chunks} chunk(s) — the total budget is "
                                f"spent")
                    break
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                chunks += 1
                yield chunk
        except requests.exceptions.ReadTimeout:

            log.warning(f"{model}: no output for {stall}s after {chunks} "
                        f"chunk(s) — giving up on this call")
            raise RuntimeError(
                f"{model} sent nothing for {stall}s. The model may be "
                f"overloaded or the prompt too large for it."
            ) from None
        finally:

            r.close()

    def chat(self, model: str, messages: list, tools: list = None,
             options: dict = None, keep_alive=None, timeout: int = 600,
             think: bool = None) -> dict:
        """Non-streaming chat. Returns the full response dict."""
        base, headers = self.route(model)
        payload = {"model": model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
        if options:
            payload["options"] = options
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        if think is not None:
            payload["think"] = bool(think)

        r = requests.post(f"{base}/api/chat", json=payload, headers=headers,
                          timeout=timeout)
        if self._rejected_think(r.status_code, r.text) and "think" in payload:
            log.info(f"{model} has no thinking mode — retrying without it")
            payload.pop("think")
            r = requests.post(f"{base}/api/chat", json=payload, headers=headers,
                              timeout=timeout)
        if r.status_code >= 400:
            raise RuntimeError(
                f"Ollama {r.status_code} @ {base}: {r.text[:300]}")
        return r.json()

    def tags(self, refresh: bool = False) -> list:
        """Raw /api/tags entries from the local daemon."""
        if self._tags_cache is not None and not refresh:
            return self._tags_cache
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=6)
            self._tags_cache = r.json().get("models", []) or []
        except Exception:
            self._tags_cache = []
        return self._tags_cache

    def cloud_tags(self, refresh: bool = False) -> list:
        """Models ollama.com exposes to this API key."""
        if not self.api_key:
            return []
        if self._cloud_tags_cache is not None and not refresh:
            return self._cloud_tags_cache
        try:
            r = requests.get(f"{CLOUD_HOST}/api/tags",
                             headers={"Authorization": f"Bearer {self.api_key}"},
                             timeout=10)
            self._cloud_tags_cache = r.json().get("models", []) or []
        except Exception:
            self._cloud_tags_cache = []
        return self._cloud_tags_cache

    def list_local(self) -> list:
        """Every model name the daemon knows about."""
        return [m.get("name") for m in self.tags() if m.get("name")]

    def remote_names(self) -> set:
        """Names the daemon proxies to ollama.com — i.e. cloud models."""
        return {m["name"] for m in self.tags()
                if "ollama.com" in str(m.get("remote_host") or "")
                and m.get("name")}

    def signed_in(self) -> bool:
        """True when the local daemon is signed in to ollama.com."""
        return bool(self.remote_names())

    def cloud_ready(self) -> bool:
        """Cloud models are usable via an API key or a signed-in daemon."""
        return bool(self.api_key) or self.signed_in()

    def show(self, model: str) -> dict:
        """Cached /api/show — real context length, family and capabilities."""
        if model in self._show_cache:
            return self._show_cache[model]
        base, headers = self.route(model)
        info = {}
        try:
            r = requests.post(f"{base}/api/show", json={"model": model},
                              headers=headers, timeout=15)
            if r.status_code < 400:
                info = r.json() or {}
        except Exception:
            pass
        self._show_cache[model] = info
        return info

    def supports_vision(self, model: str) -> bool:
        """Whether the model accepts images."""
        caps = (self.show(model) or {}).get("capabilities") or []
        return any(str(c).lower() == "vision" for c in caps)

    def model_context(self, model: str) -> int:
        """The model's real maximum context length, or 0 if unknown."""
        mi = (self.show(model) or {}).get("model_info") or {}
        for k, v in mi.items():
            if k.endswith("context_length") and isinstance(v, int):
                return v
        return 0

    def supports_tools(self, model: str) -> bool:
        return "tools" in ((self.show(model) or {}).get("capabilities") or [])

    def _entry(self, model_id: str, cloud: bool, probe: bool = True) -> dict:
        ctx = self.model_context(model_id) if probe else 0
        if not cloud and ctx:

            ctx = max_context(model_id)
        low = model_id.lower()
        if cloud:
            tag = "cloud"
        elif "coder" in low or "code" in low:
            tag = "code"
        elif re.search(r":(\d{2,3})b", low):
            tag = "big"
        else:
            tag = "quality"
        return {
            "id": model_id,
            "label": _pretty_label(model_id),
            "icon": _icon_for(model_id),
            "tag": tag,
            "role": "build" if "cod" in low else "both",
            "ctx": ctx or (CLOUD_DEFAULT_CTX if cloud else 0),
            "installed": True,

            "vision": self.supports_vision(model_id) if probe else False,
            "desc": (f"Cloud · {(ctx or CLOUD_DEFAULT_CTX) // 1024}k context · no VRAM"
                     if cloud else
                     f"Local · {ctx // 1024}k context" if ctx else "Local model"),
        }

    def discover(self, refresh: bool = False) -> dict:
        """Ask the daemon (and ollama.com, if a key is set) what actually exists."""
        if refresh:
            self._tags_cache = None
            self._cloud_tags_cache = None

        tags = self.tags(refresh=refresh)
        remote = self.remote_names()

        cloud_ids, local_ids = [], []
        for m in tags:
            name = m.get("name")
            if not name:
                continue
            (cloud_ids if name in remote else local_ids).append(name)

        for m in self.cloud_tags(refresh=refresh):
            name = m.get("name")
            if name and name not in cloud_ids:
                cloud_ids.append(name)

        with ThreadPoolExecutor(max_workers=8) as pool:
            cloud = list(pool.map(lambda i: self._entry(i, True), cloud_ids))
            local = list(pool.map(lambda i: self._entry(i, False), local_ids))

        if not cloud:

            cloud = [{**self._entry(i, True, probe=False), "installed": False}
                     for i in FALLBACK_CLOUD]

        cloud.sort(key=lambda e: -e["ctx"])
        local.sort(key=lambda e: (e["tag"] != "code", e["id"]))
        return {"cloud": cloud, "local": local}

    def cloud_reachable(self) -> bool:
        """True if a configured API key actually authenticates."""
        if not self.api_key:
            return False
        try:
            r = requests.get(f"{CLOUD_HOST}/api/tags",
                             headers={"Authorization": f"Bearer {self.api_key}"},
                             timeout=8)
            return r.status_code < 400
        except Exception:
            return False

    def has_model(self, model: str) -> bool:
        """Cloud models need no local install; local ones are matched by tag."""
        if is_cloud_model(model):

            if self.api_key:
                return True
            return any(model == n for n in self.list_local())
        names = self.list_local()
        return any(model == n or model.split(":")[0] == n.split(":")[0]
                   for n in names)

    def pull(self, model: str, on_progress=None) -> bool:
        """Pull a model into the local daemon."""
        if is_cloud_model(model):
            return True
        try:
            r = requests.post(f"{self.host}/api/pull", json={"name": model},
                              stream=True, timeout=1800)
            last = -1
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if chunk.get("total") and on_progress:
                    pct = int(chunk.get("completed", 0) / chunk["total"] * 100)
                    if pct != last and pct % 10 == 0:
                        on_progress(pct)
                        last = pct
                if "success" in chunk.get("status", ""):
                    return True
            return True
        except Exception as e:
            log.error(f"pull failed: {e}")
            return False

    def unload(self, model: str):
        """Free VRAM. No-op for cloud models — nothing is resident locally."""
        if is_cloud_model(model):
            return
        try:
            requests.post(f"{self.host}/api/generate",
                          json={"model": model, "keep_alive": 0}, timeout=8)
        except Exception:
            pass

    def catalog(self, refresh: bool = True) -> dict:
        """Everything the UI needs to render its model pickers."""
        found = self.discover(refresh=refresh)
        return {
            "cloud": found["cloud"],
            "local_models": found["local"],

            "local": [e["id"] for e in found["local"]] + [e["id"] for e in found["cloud"]
                                                          if e.get("installed")],
            "cloud_enabled": self.cloud_ready(),
            "cloud_via": ("api-key" if self.api_key
                          else "signed-in" if self.signed_in() else "none"),
            "host": self.host,
            "local_ctx": LOCAL_DEFAULT_CTX,
        }


_CLIENT = None


def _default_client() -> "OllamaClient":
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = OllamaClient()
    return _CLIENT


def set_default_client(client: "OllamaClient"):
    """Let the server share its configured client with the helpers above."""
    global _CLIENT
    _CLIENT = client

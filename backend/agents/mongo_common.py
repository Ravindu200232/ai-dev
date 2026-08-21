"""AgentForge-managed MongoDB."""
import io
import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from pathlib import Path

import requests

from .ollama_client import load_settings

log = logging.getLogger("mongo")

FEED_URL = "https://downloads.mongodb.org/current.json"
DEFAULT_PORT = 27017
HOME = Path.home() / ".agentforge" / "mongo"


FALLBACK_VERSION = "8.3.7"
FALLBACK_URLS = {
    ("windows", "x86_64"):
        f"https://fastdl.mongodb.org/windows/mongodb-windows-x86_64-{FALLBACK_VERSION}.zip",
    ("macos", "arm64"):
        f"https://fastdl.mongodb.org/osx/mongodb-macos-arm64-{FALLBACK_VERSION}.tgz",
    ("macos", "x86_64"):
        f"https://fastdl.mongodb.org/osx/mongodb-macos-x86_64-{FALLBACK_VERSION}.tgz",
    ("ubuntu2204", "x86_64"):
        f"https://fastdl.mongodb.org/linux/mongodb-linux-x86_64-ubuntu2204-{FALLBACK_VERSION}.tgz",
}


BUDGET_S = 240


def _arch() -> str:
    m = platform.machine().lower()
    return "arm64" if m in ("arm64", "aarch64") else "x86_64"


def _linux_targets() -> list:
    """MongoDB publishes per-distro Linux builds — there is no generic one."""
    info = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                info[k] = v.strip().strip('"')
    except Exception:
        pass
    dist, ver = info.get("ID", ""), info.get("VERSION_ID", "")
    major = ver.split(".")[0] if ver else ""
    table = {
        ("ubuntu", "24"): "ubuntu2404", ("ubuntu", "22"): "ubuntu2204",
        ("ubuntu", "20"): "ubuntu2004", ("debian", "12"): "debian12",
        ("debian", "11"): "debian11", ("rhel", "9"): "rhel93",
        ("centos", "9"): "rhel93", ("rocky", "9"): "rhel93",
        ("almalinux", "9"): "rhel93", ("rhel", "8"): "rhel8",
        ("amzn", "2023"): "amazon2023",
    }
    picked = table.get((dist, major))

    return ([picked] if picked else []) + ["ubuntu2204", "ubuntu2404", "rhel93"]


def _targets() -> list:
    if sys.platform.startswith("win"):
        return ["windows"]
    if sys.platform == "darwin":
        return ["macos"]
    return _linux_targets()


def _exe(name: str) -> str:
    return f"{name}.exe" if sys.platform.startswith("win") else name


def get_uri_override() -> str:
    """A user-supplied URI wins over anything AgentForge would manage."""
    return (os.environ.get("MONGODB_URI", "").strip()
            or str(load_settings().get("mongodb_uri", "")).strip())


def db_name_for(project: str) -> str:
    """Per-project database so generated apps never share collections."""
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", project or "").strip("_").lower()
    return f"agentforge_{slug[:48]}" if slug else "agentforge_app"


class _RangeReader(io.RawIOBase):
    """Random-access reader over an HTTP resource that supports byte ranges."""
    CHUNK = 4 << 20

    def __init__(self, url: str, session: requests.Session = None):
        self.url = url
        self.s = session or requests.Session()
        self.pos = 0
        self.buf = b""
        self.buf_start = -1
        self.transferred = 0

        head = self.s.head(url, timeout=30, allow_redirects=True)
        head.raise_for_status()
        if head.headers.get("Accept-Ranges") != "bytes":
            raise RuntimeError("server does not support range requests")
        self.size = int(head.headers["Content-Length"])

    def seekable(self): return True

    def readable(self): return True

    def seek(self, off, whence=io.SEEK_SET):
        self.pos = (off if whence == io.SEEK_SET else
                    self.pos + off if whence == io.SEEK_CUR else self.size + off)
        return self.pos

    def tell(self): return self.pos

    def _fetch(self, start: int, end: int) -> bytes:
        r = self.s.get(self.url, headers={"Range": f"bytes={start}-{end}"},
                       timeout=90)
        if r.status_code != 206:
            raise RuntimeError(f"range request returned {r.status_code}")
        self.transferred += len(r.content)
        return r.content

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        if n <= 0 or self.pos >= self.size:
            return b""
        end = min(self.pos + n, self.size)
        cached = (self.buf_start >= 0 and self.buf_start <= self.pos
                  and end <= self.buf_start + len(self.buf))
        if not cached:
            want = max(n, self.CHUNK)
            self.buf = self._fetch(self.pos, min(self.pos + want, self.size) - 1)
            self.buf_start = self.pos
        off = self.pos - self.buf_start
        out = self.buf[off:off + (end - self.pos)]
        self.pos += len(out)
        return out


class MongoManagerBase:
    """Owns at most one `mongod` child."""

    _RESET_SCRIPT = """\
import { MongoClient } from 'mongodb'
import { readFileSync } from 'fs'

const env = Object.fromEntries(
  readFileSync('.env.local', 'utf8')
    .split('\\n')
    .map(l => l.trim())
    .filter(l => l && !l.startsWith('#') && l.includes('='))
    .map(l => [l.slice(0, l.indexOf('=')).trim(),
               l.slice(l.indexOf('=') + 1).trim()]))

const uri = env.MONGODB_URI
const name = env.MONGODB_DB

// The guard that PERMITS a drop. Every database this app creates is
// `agentforge_`-prefixed, so anything else belongs to something that is not
// AgentForge and is refused rather than deleted.
if (!name.startsWith('agentforge_')) {
  console.log(JSON.stringify({ ok: false, error: 'refusing: ' + name }))
  process.exit(1)
}

const client = await new MongoClient(uri).connect()
const before = await client.db(name).listCollections().toArray()
await client.db(name).dropDatabase()
await client.close()
console.log(JSON.stringify({ ok: true, db: name, dropped: before.length }))
"""

    def __init__(self, port: int = DEFAULT_PORT, home: Path = HOME,
                 callbacks: dict = None):
        self.port = port
        self.home = Path(home)
        self.cb = callbacks or {}
        self.proc = None
        self.binary = None
        self.version = None
        self.available = False

        self.adopted = False
        self.override = False
        self.reason = ""
        self._lock = threading.Lock()
        self._out_lines = []
        self._saw_ready = False

    @property
    def bin_dir(self) -> Path: return self.home / "bin"

    @property
    def data_dir(self) -> Path: return self.home / "data"

    @property
    def pid_file(self) -> Path: return self.home / "mongod.pid"

    def set_callbacks(self, callbacks: dict):
        self.cb = callbacks or {}

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

    def _status(self, state, **extra):
        self._fire("on_status", {"state": state, **extra})

    def is_port_open(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=1.0):
                return True
        except OSError:
            return False


__all__ = [name for name in globals() if not name.startswith("__")]

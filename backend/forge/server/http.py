"""HTTP: the studio's API, and the studio itself."""
from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import api, state

log = logging.getLogger("forge.server.http")

MAX_BODY = 4 << 20
PROXY_TIMEOUT = 30
HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "upgrade"}


class Handler(BaseHTTPRequestHandler):
    """Serves `/__agentforge/api`, and proxies everything else to the studio."""

    protocol_version = "HTTP/1.1"
    server_version = "AgentForge"

    def log_message(self, fmt, *args):
        log.debug(fmt % args)

    # http.server dispatches these by name (`do_` + the HTTP method), so
    # nothing in this repo calls them. They are live; do not delete them.
    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_HEAD(self):
        self._route("HEAD")

    def _route(self, method: str) -> None:
        if self.path.startswith(api.PREFIX):
            return self._api(method, self.path[len(api.PREFIX):] or "/")
        self._proxy(method)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return {}

    def _api(self, method: str, path: str) -> None:
        path = path.split("?")[0]
        try:
            if method == "GET":
                status, payload = api.get(path)
            elif method == "POST":
                status, payload = api.post(path, self._body())
            else:
                status, payload = 405, {"error": f"{method} is not allowed here"}
        except Exception as e:                                     # noqa: BLE001
            log.exception(f"{method} {path}")
            status, payload = 500, {"error": f"{type(e).__name__}: {e}"}
        self._send(status, state.dump(payload), "application/json")

    def _send(self, status: int, body: bytes, content_type: str,
              headers=()) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _proxy(self, method: str) -> None:
        """Hand anything that is not the API to the studio dev server."""
        url = f"http://127.0.0.1:{state.STUDIO_PORT}{self.path}"
        payload = None
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            payload = self.rfile.read(length) if 0 < length <= MAX_BODY else b""
        request = Request(url, data=payload, method=method)
        for key, value in self.headers.items():
            if key.lower() not in HOP_BY_HOP and key.lower() != "host":
                request.add_header(key, value)
        try:
            with urlopen(request, timeout=PROXY_TIMEOUT) as response:
                body = response.read()
                kept = [(k, v) for k, v in response.headers.items()
                        if k.lower() not in HOP_BY_HOP
                        and k.lower() not in ("content-length", "content-type")]
                self._send(response.status, body,
                           response.headers.get("Content-Type", "text/html"),
                           kept)
        except HTTPError as e:
            body = e.read()
            self._send(e.code, body, e.headers.get("Content-Type", "text/plain"))
        except (URLError, OSError):
            self._send(502, self._studio_missing(), "text/html; charset=utf-8")

    @staticmethod
    def _studio_missing() -> bytes:
        return (f"<!doctype html><meta charset=utf-8>"
                f"<body style='font:16px system-ui;padding:3rem;background:#0b0d10;"
                f"color:#e6edf3'><h1>The studio is not running</h1>"
                f"<p>Start it with <code>npm run dev</code> in <code>studio/</code>,"
                f" then reload. It should answer on "
                f"<a style='color:#22d3ee' href='http://127.0.0.1:"
                f"{state.STUDIO_PORT}'>port {state.STUDIO_PORT}</a>.</p>"
                f"</body>").encode("utf-8")

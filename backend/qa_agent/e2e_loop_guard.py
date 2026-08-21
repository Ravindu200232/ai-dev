"""Circuit-break repeated same-page navigation without masking the app bug."""
from .e2e_common import *


class E2ELoopGuardMixin:
    """Convert a runaway client refresh/navigation cycle into one E2E failure."""

    def _known_page_routes(self) -> tuple[str, ...]:
        cached = getattr(self, "_page_path_cache", None)
        if cached is not None:
            return tuple(cached)
        out = []
        try:
            routes = (self.az.enumerate_routes() if self.az else {}) or {}
            for url, meta in routes.items():
                if meta.get("kind") != "page":
                    continue
                route = str(url or "/").split("?", 1)[0].rstrip("/") or "/"
                if route not in out:
                    out.append(route)
        except Exception:
            pass
        self._page_path_cache = tuple(out)
        return tuple(out)

    @staticmethod
    def _route_regex(route: str) -> str:
        if route == "/":
            return "/"
        parts = [p for p in str(route).strip("/").split("/") if p]
        out = ""
        for part in parts:
            if part.startswith("[[...") and part.endswith("]]" ):
                out += r"(?:/[^/?#]+(?:/[^/?#]+)*)?"
            elif part.startswith("[...") and part.endswith("]"):
                out += r"/[^/?#]+(?:/[^/?#]+)*"
            elif part.startswith("[") and part.endswith("]"):
                out += r"/[^/?#]+"
            else:
                out += "/" + re.escape(part)
        return out or "/"

    def _page_route_matcher(self):
        routes = self._known_page_routes()
        if not routes:
            return None
        alternatives = sorted({self._route_regex(r) for r in routes},
                              key=len, reverse=True)
        return re.compile(
            r"^" + re.escape(self.base_url) + r"(?:" + "|".join(alternatives) +
            r")/?(?:\?.*)?$"
        )

    def _install_route_loop_guard(self, ctx, *, burst: int = 10,
                                  window_seconds: float = 1.5):
        matcher = self._page_route_matcher()
        if matcher is None:
            return
        hits, warned = {}, set()

        def handle(route, request):
            try:
                method = str(getattr(request, "method", "GET") or "GET").upper()
                headers = getattr(request, "headers", {}) or {}
                if (method != "GET" or
                        str(headers.get("purpose") or "").lower() == "prefetch" or
                        str(headers.get("next-router-prefetch") or "") == "1"):
                    route.continue_(); return
                kind = str(getattr(request, "resource_type", "") or "")
                if kind not in {"document", "fetch", "xhr"}:
                    route.continue_(); return
                path = urlparse(str(getattr(request, "url", "") or "")).path
                path = path.rstrip("/") or "/"
                now = time.monotonic()
                recent = [t for t in hits.get(path, ()) if now - t <= window_seconds]
                recent.append(now)
                hits[path] = recent
                if len(recent) >= burst:
                    self._route_loop_fault = (
                        f"NAVIGATION_LOOP: {path} was requested {len(recent)} times "
                        f"within {window_seconds:.1f}s. The running app is repeatedly "
                        "refreshing/navigating the same page; inspect useEffect, "
                        "router.refresh/replace/push and state-driven redirects."
                    )
                    if path not in warned:
                        warned.add(path)
                        self._log("WARN", f"   🛑 {self._route_loop_fault}")
                    route.abort("aborted")
                    return
            except Exception as e:
                log.debug(f"route loop guard: {e}")
            try:
                route.continue_()
            except Exception:
                pass

        try:
            ctx.route(matcher, handle)
        except Exception as e:
            log.debug(f"install route loop guard: {e}")

    def _consume_route_loop_fault(self) -> str:
        out = str(getattr(self, "_route_loop_fault", "") or "")
        self._route_loop_fault = ""
        return out


__all__ = ["E2ELoopGuardMixin"]

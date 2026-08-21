"""Focused browser responsibilities for TesterAgent."""
from .tester_common import *


class TesterAgentBrowserMixin:
    def _no_response(self, page, route):
        """Decide what `page.goto` returning None actually meant."""
        landed = ""
        try:
            landed = urlparse(page.url).path or ""
        except Exception:
            pass

        if landed and landed.rstrip("/") != route.rstrip("/"):
            elog("INFO", f"   {route} → redirected to {landed}")
            return 302, ""

        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        landed = ""
        try:
            landed = urlparse(page.url).path or ""
        except Exception:
            pass
        if landed and landed.rstrip("/") != route.rstrip("/"):
            elog("INFO", f"   {route} → redirected to {landed}")
            return 302, ""
        try:
            resp = page.goto(self.base_url + route,
                             timeout=self.cfg["goto_timeout"],
                             wait_until="load")
        except Exception:
            resp = None
        if resp is not None:
            elog("INFO", f"   {route} → HTTP {resp.status} (compiled on retry)")
            return resp.status, ("" if resp.status < 400
                                 else self._body_text(page))
        try:
            landed = urlparse(page.url).path or ""
        except Exception:
            landed = ""
        if landed and landed.rstrip("/") != route.rstrip("/"):
            elog("INFO", f"   {route} → redirected to {landed}")
            return 302, ""

        body = self._body_text(page)
        self._collect_mcp(route)
        elog("WARN", f"   {route} → no response from the dev server")
        return None, (body or "The dev server returned no response and the "
                              "page stayed blank — the request was aborted "
                              "before any HTML arrived.")

    def _ensure_playwright(self) -> bool:
        try:
            import playwright  # noqa
            elog("INFO", "✅ Playwright Python package present")
            return True
        except ImportError:
            pass

        elog("INFO", "📦 Installing playwright Python package...")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "playwright",
             "--break-system-packages", "-q"],
            capture_output=True, timeout=120
        )
        if r.returncode != 0:
            elog("WARN", f"pip install failed: {r.stderr.decode()[:120]}")
            return False

        elog("INFO", "📦 Installing Chromium browser (may take a minute)...")
        r = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
            capture_output=True, text=True, timeout=300
        )
        if r.returncode != 0:
            elog("WARN", f"Chromium install failed: {r.stderr[:120]}")
            return False

        elog("INFO", "✅ Playwright + Chromium ready")
        return True

    def _run_browser_tests(self) -> list:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

        elog("INFO", "🎭 Launching Chromium (headless)...")
        etest("run", "Browser launch")
        errors = []
        console_errors = []

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                ctx = browser.new_context(viewport={"width": 1280, "height": 720})
                page = ctx.new_page()

                page.on("console", lambda m: console_errors.append(m.text)
                        if m.type == "error" else None)
                page.on("pageerror", lambda e: console_errors.append(f"PageError: {e}"))

                elog("INFO", f"→ Navigating to {self.base_url}...")
                try:
                    resp = page.goto(self.base_url,
                                     timeout=self.cfg["goto_timeout"],
                                     wait_until="load")
                    code = resp.status if resp else "?"
                    if resp and resp.status >= 400:
                        self._collect_mcp("/")
                        body = ""
                        try:
                            body = page.inner_text("body")[:400]
                        except Exception:
                            pass
                        msg = f"Page returned HTTP {code}"
                        errors.append(f"{msg}\n{body}" if body else msg)
                        etest("fail", msg, body[:120])
                        browser.close(); return errors
                    elog("INFO", f"✅ Page loaded (HTTP {code})")
                    etest("pass", f"Page loaded HTTP {code}")
                except PWTimeout:
                    msg = (f"Page load timeout — {self.cfg['label']} may still "
                           f"be compiling")
                    errors.append(msg); etest("fail", msg)
                    browser.close(); return errors
                except Exception as e:
                    msg = f"Navigation error: {e}"
                    errors.append(msg); etest("fail", msg)
                    browser.close(); return errors

                elog("INFO", "→ Waiting for app to render...")
                react_mounted = False
                per_sel = max(2000, self.cfg["mount_timeout"] //
                              max(len(self.cfg["mount_selectors"]), 1))
                for _sel in self.cfg["mount_selectors"]:
                    try:
                        page.wait_for_selector(_sel, timeout=per_sel)
                        react_mounted = True
                        elog("INFO", f"✅ App rendered (selector: {_sel})")
                        etest("pass", "App rendered")
                        break
                    except PWTimeout:
                        continue
                if not react_mounted:
                    real_content = page.evaluate("""() => {
                        const skip = new Set(['NEXTJS-PORTAL','NEXT-ROUTE-ANNOUNCER',
                            'SCRIPT','NOSCRIPT','TEMPLATE','STYLE','LINK']);
                        return Array.from(document.body.children).some(
                            el => !skip.has(el.tagName)
                                  && el.getBoundingClientRect().height > 0);
                    }""")
                    if real_content:
                        react_mounted = True
                        elog("INFO", "✅ Body has visible content — rendered")
                        etest("pass", "App rendered")
                    else:
                        msg = "App never rendered — likely a compile/runtime error"
                        errors.append(msg); etest("fail", msg)
                        elog("WARN", f"❌ {msg}")

                overlay_txt = self._overlay_error(page)

                label = self.cfg["label"]
                if overlay_txt and len(overlay_txt) > 15:
                    errors.append(f"{label} compile error: {overlay_txt[:500]}")
                    etest("fail", f"{label} compile error", overlay_txt[:120])
                    elog("WARN", f"❌ {label} compile error: {overlay_txt[:120]}")
                else:
                    etest("pass", f"No {label} error overlay")

                if self.stack == "next":
                    try:
                        health = page.evaluate("""async () => {
                            try {
                                const r = await fetch('/api/health')
                                return r.status + ' ' + (await r.text()).slice(0, 300)
                            } catch (e) { return 'ERR ' + e }
                        }""")
                        if '"ok":true' in (health or ""):
                            etest("pass", "Database reachable")
                            elog("INFO", "✅ MongoDB reachable via /api/health")
                        else:

                            errors.append(f"DB: /api/health failed → {health[:300]}")
                            etest("fail", "Database check", str(health)[:120])
                            elog("WARN", f"❌ /api/health → {str(health)[:120]}")
                    except Exception:
                        pass

                if react_mounted:
                    try:
                        has_visible = page.evaluate("""() => {
                            const sels = ['#root *', '#app *', 'main *',
                                          'body > div *', 'canvas', 'svg'];
                            for (const s of sels) {
                                for (const el of document.querySelectorAll(s)) {
                                    const r = el.getBoundingClientRect();
                                    if (r.width > 5 && r.height > 5) return true;
                                }
                            }
                            return false;
                        }""")
                        body_text = ""
                        try:
                            body_text = page.inner_text("body").strip()
                        except Exception:
                            pass
                        if not has_visible and len(body_text) < 10:
                            msg = "Page appears completely blank — nothing rendered"
                            errors.append(msg); etest("fail", msg)
                            elog("WARN", f"❌ {msg}")
                        else:
                            info = f"{len(body_text)} chars" if body_text else "visual content only"
                            elog("INFO", f"✅ Content visible ({info})")
                            etest("pass", "Content visible")
                    except Exception:
                        pass

                probed, bad_routes = 0, 0
                dynamic_probed, dynamic_bad = 0, 0
                if not self.smoke_only:
                    for route, status, detail, overlay in self._probe_routes(page):
                        probed += 1
                        if overlay:
                            bad_routes += 1
                            msg = f"Route {route} renders with a dev-server error"
                            errors.append(f"{msg}\n{overlay}")
                            etest("fail", msg, overlay[:120])
                            continue
                        if status and status < 400:
                            continue
                        bad_routes += 1
                        msg = (f"Route {route} returned HTTP {status}" if status
                               else f"Route {route} never responded")
                        errors.append(f"{msg}\n{detail}" if detail else msg)
                        etest("fail", msg, (detail or "")[:120])
                        elog("WARN", f"❌ {msg}")
                    if probed:
                        etest("pass" if not bad_routes else "fail",
                              f"Checked {probed} extra route(s)",
                              f"{bad_routes} failing" if bad_routes else "")

                    for route, status, detail, overlay, pattern, origin in self._probe_dynamic_links(page):
                        dynamic_probed += 1
                        if overlay:
                            dynamic_bad += 1
                            msg = f"Linked dynamic route {route} renders with a dev-server error"
                            errors.append(f"{msg}\n{overlay}")
                            etest("fail", msg, overlay[:120])
                            continue
                        if status is not None and status < 400:
                            continue
                        dynamic_bad += 1
                        msg = (f"Real link {route} matching {pattern} returned HTTP {status}"
                               if status else
                               f"Real link {route} matching {pattern} never responded")
                        detail2 = (f"linked from {origin}. " + (detail or "")).strip()
                        errors.append(f"{msg}\n{detail2}" if detail2 else msg)
                        etest("fail", msg, detail2[:120])
                        elog("WARN", f"❌ {msg}")
                    if dynamic_probed:
                        etest("pass" if not dynamic_bad else "fail",
                              f"Checked {dynamic_probed} real dynamic link(s)",
                              f"{dynamic_bad} failing" if dynamic_bad else "")

                    if probed or dynamic_probed:
                        try:
                            page.goto(self.base_url, timeout=self.cfg["goto_timeout"],
                                      wait_until="load")
                        except Exception:
                            pass
                else:
                    elog("INFO", "⚡ smoke-only browser check — exhaustive routes are owned by final E2E integrity")

                noise = self.cfg["noise"]
                real_signals = self.cfg["signals"]

                real_errors = [
                    e for e in console_errors
                    if not any(n.lower() in e.lower() for n in noise)
                    and any(s in e for s in real_signals)
                ]
                if real_errors:
                    for ce in real_errors[:5]:
                        short = ce[:160]
                        elog("WARN", f"⚠ JS error: {short}")
                        etest("fail", "JS runtime error", short)
                        errors.append(f"Console error: {short}")
                else:
                    elog("INFO", "✅ No blocking JS errors")
                    etest("pass", "No JS errors")

                try:
                    ss_path = self.project_dir / "test_screenshot.png"
                    page.screenshot(path=str(ss_path), full_page=False)
                    elog("INFO", f"📸 Screenshot saved → test_screenshot.png")
                    etest("pass", "Screenshot captured")
                except Exception as e:
                    elog("WARN", f"Screenshot failed: {e}")

                browser.close()

        except Exception as e:
            msg = f"Playwright runtime error: {e}"
            elog("WARN", f"⚠ {msg}")
            etest("fail", msg)
            errors.append(msg)

        if errors:
            elog("WARN", f"❌ {len(errors)} issue(s) found")
        else:
            elog("INFO", "🎉 All browser tests passed!")
            etest("pass", "All tests passed!")

        return errors



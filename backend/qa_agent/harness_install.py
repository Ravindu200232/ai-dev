"""Test mocks, dependency setup, and cache cleanup."""
from .harness_common import *


class HarnessInstallMixin:
    def _auth_mock(self):
        return textwrap.dedent("""\
            // Written by AgentForge. Stands in for @/lib/auth, which builds a
            // MongoClient at module scope.
            import { vi } from 'vitest'

            let current = null

            export const getSessionUser = vi.fn(async () => current)
            export const auth = { api: { getSession: vi.fn(async () => (
              current ? { user: current } : null)) } }

            /** Common aliases for the same session reader. */
            export const getCurrentUser = getSessionUser
            export const getUser = getSessionUser
            export const currentUser = getSessionUser
            export const requireUser = getSessionUser
            export default { getSessionUser, getCurrentUser, getUser,
                             currentUser, requireUser, auth }

            /** Set the next session user with a string `id`. */
            export function __setUser(user) {
              if (!user) { current = null; return }
              const id = String(user.id ?? user._id ?? '')
              current = { ...user, id }
              delete current._id
            }
            """)

    def _nav_mock(self):
        return textwrap.dedent("""\
            // Next.js navigation hooks for tests outside a Next render.
            // Keep navigation state mutable between test steps.
            import { vi } from 'vitest'

            let path = '/'
            let routeParams = {}
            let search = ''

            export const push = vi.fn()
            export const replace = vi.fn()
            export const back = vi.fn()
            export const forward = vi.fn()
            export const refresh = vi.fn()
            export const prefetch = vi.fn()

            /* Match Next.js redirects: record the call, then throw. */
            export const redirect = vi.fn((to) => {
              throw new Error('NEXT_REDIRECT:' + to)
            })
            export const notFound = vi.fn(() => {
              throw new Error('NEXT_NOT_FOUND')
            })

            export function useRouter() {
              return { push, replace, back, forward, refresh, prefetch }
            }
            export function usePathname()     { return path }
            export function useParams()       { return routeParams }
            export function useSearchParams() { return new URLSearchParams(search) }

            /** Set route, params, and query while keeping pathname clean. */
            export function __setPath(next, { params = {}, query = '' } = {}) {
              const raw = next ?? '/'
              const cut = raw.indexOf('?')
              path = cut < 0 ? raw : raw.slice(0, cut)
              routeParams = params
              search = query || (cut < 0 ? '' : raw.slice(cut + 1))
            }

            /** Back to `/`, with every spy cleared. Call it in beforeEach. */
            export function __resetNav() {
              path = '/'; routeParams = {}; search = ''
              for (const fn of [push, replace, back, forward, refresh, prefetch,
                                redirect, notFound]) {
                fn.mockClear()
              }
            }
            """)

    def _request_helper(self):
        return textwrap.dedent("""\
            // Written by AgentForge.
            import { ObjectId } from 'mongodb'

            /** A valid 24-char ObjectId. `new ObjectId('123')` throws. */
            export function oid(hex) { return hex ? new ObjectId(hex) : new ObjectId() }

            async function read(res) {
              let json = null
              try { json = await res.json() } catch { /* no body */ }
              return { status: res.status, json, res }
            }

            /** Build Next.js route context with async params. */
            function context(params) {
              return { params: Promise.resolve(params ?? {}) }
            }

            /** Call a form route with FormData or a plain object. */
            export async function postForm(handler, body, { url = 'http://localhost:5173/api/x',
                                                            method = 'POST',
                                                            headers = {},
                                                            params = {} } = {}) {
              // URL encoding gives jsdom a valid form content type.
              const fields = new URLSearchParams()
              const entries = body instanceof FormData
                ? [...body.entries()]
                : Object.entries(body ?? {})
              for (const [k, v] of entries) fields.append(k, v)
              const request = new Request(url, {
                method,
                headers: { 'Content-Type': 'application/x-www-form-urlencoded',
                           ...headers },
                body: fields.toString(),
              })
              return read(await handler(request, context(params)))
            }

            /** Call a route handler's POST/PUT/PATCH with a JSON body. */
            export async function postJson(handler, body, { url = 'http://localhost:5173/api/x',
                                                            method = 'POST',
                                                            headers = {},
                                                            params = {} } = {}) {
              // Route FormData through the matching request helper.
              if (body instanceof FormData) {
                return postForm(handler, body, { url, method, headers, params })
              }
              const request = new Request(url, {
                method,
                headers: { 'Content-Type': 'application/json', ...headers },
                body: JSON.stringify(body ?? {}),
              })
              return read(await handler(request, context(params)))
            }

            /** Call a route handler's GET, optionally with a query string. */
            export async function getJson(handler, url = 'http://localhost:5173/api/x',
                                          { params = {} } = {}) {
              return read(await handler(new Request(url), context(params)))
            }

            /* Named JSON helpers for PATCH, PUT, and DELETE routes. */
            export const patchJson  = (handler, body, opts = {}) =>
              postJson(handler, body, { ...opts, method: 'PATCH' })

            export const putJson    = (handler, body, opts = {}) =>
              postJson(handler, body, { ...opts, method: 'PUT' })

            export const deleteJson = (handler, body, opts = {}) =>
              postJson(handler, body, { ...opts, method: 'DELETE' })
            """)

    def deps_present(self) -> bool:
        nm = self.project_dir / "node_modules"
        return all((nm / n.split("@")[0] if not n.startswith("@")
                    else nm / n.rsplit("@", 1)[0]).is_dir() for n in DEV_DEPS)

    def install(self) -> bool:
        """Install the runner, once, and only when there is something to run."""
        if self.deps_present():
            return True
        if not self.cmd:
            return False

        with NPM_LOCK:
            return self._install_locked()

    def _install_locked(self) -> bool:

        # QASession is bound before the architect scaffolds the project.
        if not (self.project_dir / "package.json").is_file():
            self._log("INFO", "   ⏸ test runner deferred — package.json is not "
                              "written yet")
            return False

        if not (self.project_dir / "node_modules" / "next").is_dir():
            self._log("INFO", "   📦 installing the app's dependencies before "
                              "the first test run")
            app = self.cmd.run("npm install --no-audit --no-fund --prefer-offline --loglevel=error")
            if not app.ok:
                self._log("WARN", "   ⚠ app dependencies are not ready yet — "
                                  "deferring the test runner")
                return False
            if self.deps_present():
                self._log("INFO", "   ⚡ test runner arrived with the app dependencies — no second npm install")
                return True

        self._log("INFO", "   📦 installing the test runner (first time for "
                          "this project)")
        res = self.cmd.run(f"npm install -D {' '.join(DEV_DEPS)} --no-audit --no-fund --prefer-offline --loglevel=error")
        if not res.ok:
            self._log("WARN", f"   ⚠ could not install the test runner: "
                              f"{(res.output or '')[:160]}")
        return bool(res.ok)

    MISSING_PKG_RE = re.compile(
        r'Failed to resolve import "([^"./][^"]*)"|'
        r'Cannot find module \'([^\'./][^\']*)\'')

    @staticmethod
    def missing_packages(text: str) -> list:
        """Package names a run says are not installed, deduped, in order."""
        out = []
        for a, b in HarnessInstallMixin.MISSING_PKG_RE.findall(text or ""):
            spec = a or b
            if not spec or spec.startswith("@/") or spec.startswith("node:"):
                continue

            parts = spec.split("/")
            name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
            if name and name not in out:
                out.append(name)
        return out

    def install_missing(self, packages) -> bool:
        """Install packages a test run could not resolve."""
        with NPM_LOCK:
            wanted = [p for p in packages if p and not (
                self.project_dir / "node_modules" / p).is_dir()]
            if not wanted or not self.cmd:
                return False
            self._log("INFO", f"   📦 the tests need {', '.join(wanted)} — "
                              f"installing ahead of the app's own install")
            res = self.cmd.run(f"npm install {' '.join(wanted)} --no-audit --no-fund --prefer-offline --loglevel=error")
            if not res.ok:
                self._log("WARN", f"   ⚠ could not install {', '.join(wanted)}: "
                                  f"{(res.output or '')[:160]}")
                return False
            self._drop_vite_cache()
            return True

    def _drop_vite_cache(self) -> None:
        """Throw away Vite's pre-bundled dependencies after installing into them."""
        import shutil
        cache = self.project_dir / "node_modules" / ".vite"
        if not cache.is_dir():
            return
        try:
            shutil.rmtree(cache, ignore_errors=True)
            self._log("INFO", "   🧹 cleared Vite's dependency cache after the "
                              "install")
        except Exception as e:                              # noqa: BLE001
            self._log("WARN", f"   ⚠ could not clear {cache}: {e}")

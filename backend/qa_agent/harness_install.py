"""Auth/navigation/request mocks plus dependency installation and cache cleanup."""
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

            /**
             * The same reader under the other names apps give it.
             *
             * AgentForge's scaffold exports `getSessionUser` from `@/lib/auth`,
             * but a generated app often wraps it — `lib/session.js` exporting
             * `getCurrentUser` — and the test then mocks THAT module with this
             * helper. Without these aliases the import resolves to `undefined`,
             * calling it throws, the route's own try/catch turns that into a
             * 500, and every case in the file fails as "expected 500 to be
             * 401". Measured on one build: 32 of 46 failures, all of them this,
             * across twelve route files.
             *
             * Aliases rather than a rule the model must remember, because the
             * cost of being wrong is a whole file of tests that cannot pass and
             * a failure message that names the wrong problem.
             */
            export const getCurrentUser = getSessionUser
            export const getUser = getSessionUser
            export const currentUser = getSessionUser
            export const requireUser = getSessionUser
            export default { getSessionUser, getCurrentUser, getUser,
                             currentUser, requireUser, auth }

            /**
             * Who is signed in for the next call. `null` means nobody.
             *
             * Shaped like the real thing: Better Auth's session user is
             * `{ id, email, name, role, … }` where **id is a string** and there
             * is no `_id`. Measured against a running app. A test that sets
             * `_id` is testing a user that cannot exist, and it fails against
             * correct code — so the id is normalised here rather than left to
             * each generated test to get right.
             */
            export function __setUser(user) {
              if (!user) { current = null; return }
              const id = String(user.id ?? user._id ?? '')
              current = { ...user, id }
              delete current._id
            }
            """)

    def _nav_mock(self):
        return textwrap.dedent("""\
            // Written by AgentForge. Stands in for next/navigation, whose hooks
            // throw outside a Next render.
            //
            // Every generated test used to build this itself, and the shape is
            // easy to get wrong in a way that reads like an app bug. The one
            // measured: `vi.hoisted(() => ({ mockPathname: '/' }))` and then
            // `mocks.mockPathname.value = '/items'` — five cases in one file
            // died with "Cannot create property 'value' on string '/'". The
            // pathname has to be a mutable module value, not a string handed
            // out at mock time, which is exactly what a shared helper is for.
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

            /*
             * `redirect` and `notFound` are spies AND they throw, because the
             * real ones do. Both halves matter:
             *
             *   • A plain function cannot be asserted on —
             *     `expect(redirect).toHaveBeenCalledWith('/login')` dies with
             *     "[Function redirect] is not a spy or a call to a spy".
             *   • Next's redirect() throws to abort the render, and guard code
             *     is written assuming it never returns. A mock that returns
             *     normally lets the next line run against the state the guard
             *     just rejected, so `if (!user) redirect('/login')` falls
             *     through to `user.role` and the test reports
             *     "Cannot read properties of null" — which reads as a missing
             *     null check in correct code.
             *
             * So: assert the throw, then assert the target.
             *
             *   await expect(requireStaff()).rejects.toThrow('NEXT_REDIRECT')
             *   expect(redirect).toHaveBeenCalledWith('/login')
             */
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

            /**
             * Point the mocked router at a route.
             *
             *   __setPath('/items')
             *   __setPath('/orders/abc', { params: { id: 'abc' } })
             *   __setPath('/items', { query: 'kind=new' })
             *   __setPath('/items?kind=new')        // same as the line above
             *
             * A query written into the path is split off rather than left in
             * `usePathname()`. Next never puts one there, so a component doing
             * the ordinary `router.push(`${pathname}?${params}`)` would build
             * `/items?kind=new?sort=new` — two `?`, a URL no router can read
             * — and the test would look like it had found a bug in code that
             * is correct. Measured: a filter component failed exactly that way
             * and survived every repair round, with the test carrying a
             * comment asserting this splitting already happened.
             */
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

            /**
             * The second argument Next gives every route handler.
             *
             * Next calls `POST(request, { params })` — always, and `params` is
             * a PROMISE since Next 15, which is why every handler awaits it.
             * Passing only the request makes a dynamic route die on
             * `Cannot destructure property 'params' of 'undefined'`, which
             * reads like an application bug and is not one. Measured.
             */
            function context(params) {
              return { params: Promise.resolve(params ?? {}) }
            }

            /**
             * Call a route handler with a form body — for a handler that
             * reads `request.formData()`, which is what a route behind a
             * plain <form action="/api/…"> does.
             *
             * Takes a FormData or a plain object.
             */
            export async function postForm(handler, body, { url = 'http://localhost:5173/api/x',
                                                            method = 'POST',
                                                            headers = {},
                                                            params = {} } = {}) {
              // URLSearchParams, not FormData. Building a Request with a
              // FormData body does not set a Content-Type under jsdom, and
              // `request.formData()` then throws
              //   Content-Type was not one of "multipart/form-data" or
              //   "application/x-www-form-urlencoded"
              // which the handler's own try/catch turns into a 500 — the
              // failure reads as the route crashing and the route is fine.
              // Measured against a real generated route. Urlencoded is the
              // other type `formData()` accepts, it carries no boundary, and
              // every field a generated form posts is a string anyway.
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
              // A FormData handed to the JSON helper is the author reaching
              // for the only name they knew. Stringifying it yields "{}", the
              // handler's `request.formData()` throws on a JSON body, its own
              // try/catch turns that into a 500, and the whole thing reads as
              // the route crashing. Measured: two cases in one file, nine
              // repair rounds, and none of them could pass — the route was
              // never wrong. Nobody passes a FormData here wanting "{}".
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

            /*
             * PATCH, PUT and DELETE by name.
             *
             * `postJson` has always taken a `method` option, but nothing in
             * front of the test author said so, and an author writing a test
             * for a route that exports PATCH reaches for `patchJson` — which
             * did not exist, so four cases in one file died with
             * "patchJson is not a function" before a single assertion ran.
             * The need is real: a route with a PATCH handler has to be
             * callable. Cheaper to provide the name than to teach every
             * author not to want it.
             */
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

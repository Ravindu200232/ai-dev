"""Vitest config/setup and fixed MongoDB mock materialization."""
from .harness_common import *


class HarnessMockMixin:
    def __init__(self, project_dir, *, callbacks=None, cmd=None):
        self.project_dir = Path(project_dir)
        self.cb = callbacks or {}
        self.cmd = cmd

    def _fire(self, name, *a):
        fn = self.cb.get(name)
        if fn and callable(fn):
            try:
                fn(*a)
            except Exception as e:
                log.warning(f"callback {name} failed: {e}")

    def _log(self, lvl, txt):
        self._fire("on_log", lvl, txt)
        log.info(txt)

    def _write(self, rel, body):
        fp = self.project_dir / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        body = body.rstrip() + "\n"
        if fp.is_file() and fp.read_text(encoding="utf-8", errors="replace") == body:
            return False
        fp.write_text(body, encoding="utf-8")
        return True

    def materialise(self) -> list:
        """Write config, setup and helpers."""
        wrote = []
        for rel, body in ((CONFIG, self._config()),
                          (SETUP, self._setup()),
                          (f"{HELPERS}/nextLink.jsx", self._next_link()),
                          (f"{HELPERS}/mongoMock.js", self._mongo_mock()),
                          (f"{HELPERS}/authMock.js", self._auth_mock()),
                          (f"{HELPERS}/navMock.js", self._nav_mock()),
                          (f"{HELPERS}/request.js", self._request_helper())):
            if self._write(rel, body):
                wrote.append(rel)
        if wrote:
            self._log("INFO", f"   🧪 test harness ready ({len(wrote)} file(s))")
        return wrote

    def _config(self):
        return textwrap.dedent("""\
            import { defineConfig } from 'vitest/config'
            import { fileURLToPath } from 'node:url'

            // Written by AgentForge. `.mjs` because these projects have no
            // "type": "module", which would make a .js config ambiguous.
            export default defineConfig({
              resolve: {
                // Vitest does NOT read jsconfig.json, so the @/ alias has to be
                // repeated here or every import of @/lib/* fails to resolve.
                alias: {
                  '@': fileURLToPath(new URL('.', import.meta.url)),
                  // next/link needs a real client router to render. Every
                  // client component imports it, and a model mocking it by
                  // hand gets the default export wrong — measured, four
                  // failures in one file from a factory with no `default`.
                  // Aliasing it once here means no test ever has to.
                  'next/link': fileURLToPath(
                    new URL('./tests/helpers/nextLink.jsx', import.meta.url)),
                },
              },
              // The app's postcss.config.js is auto-discovered by Vite and would
              // run Tailwind on every imported stylesheet. Tests do not need it.
              css: { postcss: { plugins: [] } },
              // JSX without @vitejs/plugin-react — one fewer dependency and one
              // fewer Babel toolchain in a project that ships SWC.
              esbuild: { jsx: 'automatic', jsxImportSource: 'react' },
              test: {
                environment: 'jsdom',
                globals: true,
                setupFiles: ['./tests/setup.js'],
                include: ['tests/unit/**/*.test.{js,jsx}'],
                // tests/quarantine holds tests AgentForge could not make
                // trustworthy; they are kept to be read, never run.
                exclude: ['**/node_modules/**', 'tests/quarantine/**'],
                // A generated infinite loop dies here, not at the process cap.
                testTimeout: 10000,
                hookTimeout: 10000,
                restoreMocks: true,
              },
            })
            """)

    def _setup(self):
        return textwrap.dedent("""\
            // Written by AgentForge.
            import '@testing-library/jest-dom/vitest'
            import { cleanup } from '@testing-library/react'
            import { afterEach, beforeEach, vi } from 'vitest'

            // jsdom does not navigate, and its `location` is non-configurable —
            // so a component calling `window.location.reload()` cannot be
            // tested at all: asserting on `location.href` never matches, and
            // stubbing it throws `TypeError: Cannot redefine property: reload`.
            // Both were measured on real builds. Replacing the whole object on
            // `window` (not a property on `location`) makes the ordinary thing
            // work: the component reloads, the test asserts it did.
            const realLocation = window.location
            function freshLocation() {
              return {
                href: realLocation.href,
                origin: realLocation.origin,
                pathname: realLocation.pathname,
                search: '',
                hash: '',
                assign: vi.fn(),
                replace: vi.fn(),
                reload: vi.fn(),
                toString: () => realLocation.href,
              }
            }
            Object.defineProperty(window, 'location', {
              configurable: true, writable: true, value: freshLocation(),
            })

            beforeEach(() => { window.location = freshLocation() })
            afterEach(() => { cleanup() })
            """)

    def _next_link(self):
        return textwrap.dedent("""            // Written by AgentForge. `next/link` is aliased to this in
            // vitest.config.mjs, so a component that imports it renders a
            // plain anchor and no test has to mock it.
            export default function Link({ href, children, ...rest }) {
              return <a href={typeof href === 'string' ? href : '#'} {...rest}>{children}</a>
            }
            """)

    def _mongo_mock(self):
        return textwrap.dedent("""\
            // Written by AgentForge. Stands in for @/lib/mongodb, which throws at
            // import without MONGODB_URI and opens a connection at module
            // scope. A module, not a factory, because vi.mock is hoisted.
            import { ObjectId } from 'mongodb'

            const store = new Map()

            /*
             * A structural copy that KEEPS Date and ObjectId as themselves.
             *
             * This was `JSON.parse(JSON.stringify(d))`, which turns a Date
             * into a string and an ObjectId into a hex string. The real driver
             * hands both back as objects, so any route doing the ordinary
             * thing — `job.createdAt.toISOString()` — died with
             * "toISOString is not a function", got caught by its own
             * try/catch, and returned the 500 it reserves for a database
             * fault. The test then reported `expected 500 to be 200`, which
             * reads as a broken route and is not one; nothing in that chain
             * mentions the mock. Measured: every `expected 500 to be 200` in
             * two separate builds, and three repair rounds spent on a route
             * whose code was correct.
             */
            const clone = (d) => {
              if (d == null || typeof d !== 'object') return d
              if (d instanceof Date) return new Date(d.getTime())
              if (d instanceof ObjectId) return d
              if (Array.isArray(d)) return d.map(clone)
              const out = {}
              for (const [k, v] of Object.entries(d)) out[k] = clone(v)
              return out
            }

            /*
             * Query matching.
             *
             * This used to understand four operators — $in, $ne, $gte, $lte —
             * and fall through to a string comparison for everything else.
             * That is the failure mode the sort comment above warns about, in
             * its worst form: `{ price: { $gt: 10 } }` stringifies to
             * "[object Object]", equals nothing, and the route returns an
             * empty list. The route is correct. The test fails. Nothing in
             * the message mentions the mock, so the repair loop rewrites a
             * correct test until it agrees with a broken one.
             *
             * So: implement what a route actually uses, and THROW on anything
             * still unsupported. A named error points at the mock; a silent
             * empty result points at the app.
             */
            function valueAt(doc, path) {
              if (!path.includes('.')) return doc == null ? undefined : doc[path]
              return path.split('.').reduce(
                (o, part) => (o == null ? undefined : o[part]), doc)
            }

            function same(a, b) {
              if (a instanceof Date && b instanceof Date) return a.getTime() === b.getTime()
              if (a == null || b == null) return a === b
              if (typeof a === 'object' || typeof b === 'object') {
                return String(a) === String(b)
              }
              return String(a) === String(b)
            }

            /* One field against one condition. */
            function fieldMatches(got, cond) {
              if (cond instanceof Date || cond instanceof ObjectId) {
                return same(got, cond)
              }
              if (cond instanceof RegExp) {
                return cond.test(String(got ?? ''))
              }
              if (Array.isArray(cond)) {
                return Array.isArray(got)
                  ? got.length === cond.length && got.every((x, i) => same(x, cond[i]))
                  : same(got, cond)
              }
              if (cond && typeof cond === 'object') {
                const ops = Object.keys(cond)
                if (ops.some((o) => o.startsWith('$'))) {
                  return ops.every((op) => {
                    const want = cond[op]
                    switch (op) {
                      case '$eq':     return contains(got, want)
                      case '$ne':     return !contains(got, want)
                      case '$in':     return (want || []).some((x) => contains(got, x))
                      case '$nin':    return !(want || []).some((x) => contains(got, x))
                      case '$gt':     return cmp(got, want) > 0
                      case '$gte':    return cmp(got, want) >= 0
                      case '$lt':     return cmp(got, want) < 0
                      case '$lte':    return cmp(got, want) <= 0
                      case '$exists': return (got !== undefined) === !!want
                      case '$regex': {
                        const rx = want instanceof RegExp
                          ? want : new RegExp(want, cond.$options || '')
                        return rx.test(String(got ?? ''))
                      }
                      case '$options': return true          // handled with $regex
                      case '$not':    return !fieldMatches(got, want)
                      case '$all':    return Array.isArray(got)
                        && (want || []).every((x) => got.some((g) => same(g, x)))
                      case '$size':   return Array.isArray(got) && got.length === Number(want)
                      case '$elemMatch': return Array.isArray(got)
                        && got.some((g) => matches(g, want))
                      default:
                        throw new Error(
                          'mongoMock does not implement the query operator ' + op +
                          '. The mock is the limitation here, not the route — ' +
                          'add it in qa_agent/harness.py or assert a different way.')
                    }
                  })
                }
                return same(got, cond)
              }
              return contains(got, cond)
            }

            /* Mongo matches a scalar against an array field by membership. */
            function contains(got, want) {
              if (Array.isArray(got)) return got.some((g) => same(g, want))
              return same(got, want)
            }

            function cmp(a, b) {
              if (a === undefined || a === null) return -1
              const x = a instanceof Date ? a.getTime() : a
              const y = b instanceof Date ? b.getTime() : b
              if (typeof x === 'number' || typeof y === 'number') {
                return Number(x) - Number(y)
              }
              return String(x) < String(y) ? -1 : String(x) > String(y) ? 1 : 0
            }

            function matches(doc, query = {}) {
              return Object.entries(query).every(([k, v]) => {
                if (k === '$or')  return (v || []).some((q) => matches(doc, q))
                if (k === '$and') return (v || []).every((q) => matches(doc, q))
                if (k === '$nor') return !(v || []).some((q) => matches(doc, q))
                if (k === '$not') return !matches(doc, v)
                if (k === '$expr' || k === '$where') {
                  throw new Error(
                    'mongoMock does not implement ' + k + ' — the mock is the ' +
                    'limitation here, not the route.')
                }
                return fieldMatches(valueAt(doc, k), v)
              })
            }

            /*
             * Applying an update, in one place.
             *
             * Four call sites used to do this inline and all four understood
             * only $set and $inc. Everything else — $push above all — was
             * dropped on the floor: the route appended to an array, the mock
             * kept the old one, and the assertion that the array grew failed
             * against a route that is correct. Same trap as the query
             * operators, same answer: implement what routes use, throw by
             * name on the rest.
             */
            function setPath(doc, path, value) {
              if (!path.includes('.')) { doc[path] = value; return }
              const parts = path.split('.')
              let cur = doc
              for (const p of parts.slice(0, -1)) {
                if (cur[p] == null || typeof cur[p] !== 'object') cur[p] = {}
                cur = cur[p]
              }
              cur[parts[parts.length - 1]] = value
            }

            function applyUpdate(doc, update = {}) {
              const ops = Object.keys(update)
              // No operators at all is a whole-document replacement.
              if (ops.length && !ops.some((k) => k.startsWith('$'))) {
                const id = doc._id
                for (const k of Object.keys(doc)) delete doc[k]
                Object.assign(doc, clone(update), { _id: id })
                return doc
              }
              for (const op of ops) {
                const arg = update[op] || {}
                switch (op) {
                  case '$set':
                    for (const [k, v] of Object.entries(arg)) setPath(doc, k, v)
                    break
                  case '$setOnInsert':
                    break                       // only meaningful on an upsert
                  case '$unset':
                    for (const k of Object.keys(arg)) delete doc[k]
                    break
                  case '$inc':
                    for (const [k, n] of Object.entries(arg)) {
                      doc[k] = (Number(valueAt(doc, k)) || 0) + Number(n)
                    }
                    break
                  case '$mul':
                    for (const [k, n] of Object.entries(arg)) {
                      doc[k] = (Number(valueAt(doc, k)) || 0) * Number(n)
                    }
                    break
                  case '$min':
                    for (const [k, v] of Object.entries(arg)) {
                      const got = valueAt(doc, k)
                      if (got === undefined || cmp(v, got) < 0) setPath(doc, k, v)
                    }
                    break
                  case '$max':
                    for (const [k, v] of Object.entries(arg)) {
                      const got = valueAt(doc, k)
                      if (got === undefined || cmp(v, got) > 0) setPath(doc, k, v)
                    }
                    break
                  case '$push':
                    for (const [k, v] of Object.entries(arg)) {
                      if (!Array.isArray(doc[k])) doc[k] = []
                      const each = (v && typeof v === 'object' && '$each' in v)
                        ? v.$each : [v]
                      doc[k].push(...each)
                    }
                    break
                  case '$addToSet':
                    for (const [k, v] of Object.entries(arg)) {
                      if (!Array.isArray(doc[k])) doc[k] = []
                      const each = (v && typeof v === 'object' && '$each' in v)
                        ? v.$each : [v]
                      for (const x of each) {
                        if (!doc[k].some((g) => same(g, x))) doc[k].push(x)
                      }
                    }
                    break
                  case '$pull':
                    for (const [k, v] of Object.entries(arg)) {
                      if (!Array.isArray(doc[k])) continue
                      doc[k] = doc[k].filter((g) => !fieldMatches(g, v))
                    }
                    break
                  case '$pop':
                    for (const [k, v] of Object.entries(arg)) {
                      if (!Array.isArray(doc[k]) || !doc[k].length) continue
                      Number(v) < 0 ? doc[k].shift() : doc[k].pop()
                    }
                    break
                  case '$rename':
                    for (const [k, v] of Object.entries(arg)) {
                      if (k in doc) { doc[v] = doc[k]; delete doc[k] }
                    }
                    break
                  case '$currentDate':
                    for (const k of Object.keys(arg)) doc[k] = new Date()
                    break
                  default:
                    throw new Error(
                      'mongoMock does not implement the update operator ' + op +
                      '. The mock is the limitation here, not the route — add ' +
                      'it in qa_agent/harness.py or assert a different way.')
                }
              }
              return doc
            }

            function collection(name) {
              if (!store.has(name)) store.set(name, [])
              const docs = () => store.get(name)
              const cursor = (rows) => ({
                /*
                 * Actually sorts. It used to ignore its argument and hand the
                 * rows back in insertion order, which chains fine and returns
                 * the wrong answer — so `find().sort({ createdAt: -1 })` in a
                 * route was tested against a list that was never sorted, and
                 * "newest first" passed or failed by luck of the seed order.
                 * A mock that silently does nothing is worse than one that
                 * throws: nothing points at the mock.
                 */
                sort: (spec = {}) => {
                  const keys = Object.entries(spec)
                  if (!keys.length) return cursor(rows)
                  const out = [...rows].sort((a, b) => {
                    for (const [k, dir] of keys) {
                      const x = a[k], y = b[k]
                      if (x === y) continue
                      if (x === undefined || x === null) return 1
                      if (y === undefined || y === null) return -1
                      // Dates compare as numbers; strings need localeCompare.
                      const c = (x instanceof Date && y instanceof Date)
                        ? x.getTime() - y.getTime()
                        : (typeof x === 'string' && typeof y === 'string')
                          ? x.localeCompare(y)
                          : (x < y ? -1 : 1)
                      if (c) return (dir === -1 || dir === 'desc') ? -c : c
                    }
                    return 0
                  })
                  return cursor(out)
                },
                limit: (n) => cursor(rows.slice(0, n)),
                skip: (n) => cursor(rows.slice(n)),
                project: () => cursor(rows),
                toArray: async () => rows.map(clone),
              })
              return {
                findOne: async (q = {}) => clone(docs().find((d) => matches(d, q)) ?? null),
                find: (q = {}) => cursor(docs().filter((d) => matches(d, q))),
                countDocuments: async (q = {}) => docs().filter((d) => matches(d, q)).length,
                insertOne: async (doc) => {
                  const _id = doc._id ?? new ObjectId()
                  docs().push({ ...doc, _id })
                  return { acknowledged: true, insertedId: _id }
                },
                insertMany: async (rows) => {
                  rows.forEach((d) => docs().push({ _id: d._id ?? new ObjectId(), ...d }))
                  return { acknowledged: true, insertedCount: rows.length }
                },
                updateOne: async (q, update = {}, opts = {}) => {
                  const hit = docs().find((d) => matches(d, q))
                  if (hit) {
                    applyUpdate(hit, update)
                    return { matchedCount: 1, modifiedCount: 1, upsertedId: null }
                  }
                  if (opts.upsert) {
                    const doc = { _id: new ObjectId(), ...q,
                                  ...(update.$setOnInsert || {}), ...(update.$set || {}) }
                    docs().push(doc)
                    return { matchedCount: 0, modifiedCount: 0, upsertedId: doc._id }
                  }
                  return { matchedCount: 0, modifiedCount: 0, upsertedId: null }
                },
                /*
                 * The bulk verbs. Their absence does not read as a missing
                 * mock — the route calls `updateMany`, TypeError is thrown
                 * inside its own try/catch, and the handler returns the 500 it
                 * was written to return for a real database fault. So the test
                 * fails with "expected 500 to be 200" and every reader,
                 * including the repair agent, goes looking for a bug in a route
                 * that is correct. Measured: one build's last remaining failure
                 * after every other round had cleared, and three repair rounds
                 * spent on it.
                 */
                updateMany: async (q, update = {}) => {
                  const hits = docs().filter((d) => matches(d, q))
                  for (const hit of hits) applyUpdate(hit, update)
                  return { matchedCount: hits.length, modifiedCount: hits.length,
                           upsertedId: null }
                },
                deleteOne: async (q) => {
                  const i = docs().findIndex((d) => matches(d, q))
                  if (i < 0) return { deletedCount: 0 }
                  docs().splice(i, 1)
                  return { deletedCount: 1 }
                },
                deleteMany: async (q) => {
                  const rows = docs()
                  const keep = rows.filter((d) => !matches(d, q))
                  const n = rows.length - keep.length
                  store.set(name, keep)
                  return { deletedCount: n }
                },
                findOneAndUpdate: async (q, update = {}, opts = {}) => {
                  const hit = docs().find((d) => matches(d, q))
                  if (!hit) return opts.includeResultMetadata ? { value: null } : null
                  const before = clone(hit)
                  applyUpdate(hit, update || {})
                  // The driver returns the document BEFORE the update unless
                  // returnDocument: 'after' is asked for.
                  const out = opts.returnDocument === 'after' ? clone(hit) : before
                  return opts.includeResultMetadata ? { value: out } : out
                },
                /*
                 * `bulkWrite` is how a route applies a list of edits in one
                 * call — "mark these five attendances" is written that way far
                 * more often than as a loop. Missing, it fails the same silent
                 * way `updateMany` did: TypeError inside the route's own
                 * try/catch, a 500 that reads as a database fault, and a test
                 * reporting "expected 500 to be 200" about correct code.
                 */
                bulkWrite: async (ops = []) => {
                  const res = { insertedCount: 0, matchedCount: 0,
                                modifiedCount: 0, deletedCount: 0,
                                upsertedCount: 0 }
                  for (const op of ops) {
                    if (op.insertOne) {
                      const d = op.insertOne.document
                      docs().push({ _id: d._id ?? new ObjectId(), ...d })
                      res.insertedCount++
                    } else if (op.updateOne || op.updateMany) {
                      const spec = op.updateOne || op.updateMany
                      const all = docs().filter((d) => matches(d, spec.filter))
                      const hits = op.updateOne ? all.slice(0, 1) : all
                      for (const hit of hits) {
                        applyUpdate(hit, spec.update || {})
                      }
                      res.matchedCount += hits.length
                      res.modifiedCount += hits.length
                      if (!hits.length && spec.upsert) {
                        const d = { _id: new ObjectId(), ...spec.filter,
                                    ...(spec.update?.$setOnInsert || {}),
                                    ...(spec.update?.$set || {}) }
                        docs().push(d)
                        res.upsertedCount++
                      }
                    } else if (op.deleteOne || op.deleteMany) {
                      const spec = op.deleteOne || op.deleteMany
                      const rows = docs()
                      const gone = rows.filter((d) => matches(d, spec.filter))
                      const drop = op.deleteOne ? gone.slice(0, 1) : gone
                      store.set(name, rows.filter((d) => !drop.includes(d)))
                      res.deletedCount += drop.length
                    }
                  }
                  return { acknowledged: true, ...res }
                },
                distinct: async (field, q = {}) => [
                  ...new Set(docs().filter((d) => matches(d, q)).map((d) => d[field])),
                ],
                estimatedDocumentCount: async () => docs().length,
                createIndex: async () => 'ok',
              }
            }

            export async function getCollection(name) { return collection(name) }
            export async function getDb() { return { collection } }
            /*
             * NOT `clone`. The two do opposite jobs and sharing one function
             * for both is what hid the bug above. `clone` is the driver
             * handing a document back, so it keeps Date and ObjectId as
             * objects; `serialize` is the app flattening a document to cross
             * the RSC boundary, so it must turn them into strings — that is
             * the whole reason the app calls it. Matches `lib/mongodb.js`.
             */
            export function serialize(doc) {
              return doc == null ? doc : JSON.parse(JSON.stringify(doc))
            }
            export { ObjectId }
            export default Promise.resolve({ db: () => ({ collection }) })

            /** Fill a collection before the code under test reads it. */
            export function __seed(name, rows) {
              store.set(name, rows.map((d) => ({ _id: d._id ?? new ObjectId(), ...d })))
            }
            /** Empty every collection. Call it in beforeEach. */
            export function __reset() { store.clear() }
            /** Read a collection back, to assert on what the handler wrote. */
            export function __all(name) { return (store.get(name) || []).map(clone) }

            /**
             * A valid 24-char ObjectId, re-exported from here as well as from
             * request.js. `oid` reads like it belongs with the mongo mock, and
             * models import it from here — which yields undefined and fails as
             * `TypeError: oid is not a function`, eight times in one measured
             * run. Exporting it twice costs nothing and ends the class.
             */
            export function oid(hex) { return hex ? new ObjectId(hex) : new ObjectId() }
            """)

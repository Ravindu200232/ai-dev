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
                  // Use one stable `next/link` mock with a default export.
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

            // Replace jsdom's fixed location with testable navigation spies.
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
        return textwrap.dedent("""            // Stable `next/link` test alias.
            export default function Link({ href, children, ...rest }) {
              return <a href={typeof href === 'string' ? href : '#'} {...rest}>{children}</a>
            }
            """)

    def _mongo_mock(self):
        return textwrap.dedent("""\
            // Hoisted in-memory replacement for @/lib/mongodb.
            import { ObjectId } from 'mongodb'

            const store = new Map()

            /* Copy rows without flattening Date or ObjectId values. */
            const clone = (d) => {
              if (d == null || typeof d !== 'object') return d
              if (d instanceof Date) return new Date(d.getTime())
              if (d instanceof ObjectId) return d
              if (Array.isArray(d)) return d.map(clone)
              const out = {}
              for (const [k, v] of Object.entries(d)) out[k] = clone(v)
              return out
            }

            /* Match supported queries and reject unknown operators. */
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

            /* Apply every supported update operator in one place. */
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
                /* Sort rows with the same direction rules as MongoDB. */
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
                /* Match the driver's bulk update behavior. */
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
                /* Apply supported bulk write operations in order. */
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
            /* Flatten a row for the RSC boundary, unlike `clone`. */
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

            /** Create a valid ObjectId from either test helper module. */
            export function oid(hex) { return hex ? new ObjectId(hex) : new ObjectId() }
            """)

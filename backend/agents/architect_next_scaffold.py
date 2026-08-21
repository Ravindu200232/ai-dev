"""Next.js scaffold, generated context snapshot and file planning helpers."""
from .architect_common import *


class ArchitectNextScaffoldMixin:
    def _scaffold_next(self):
        """Next.js 16 App Router + MongoDB."""
        self._log("INFO", "🧱 Scaffolding Next.js + Tailwind + MongoDB")
        title = self.plan.get("title", "AgentForge App")
        slug = self.plan.get("project_name", "agentforge-app")
        db = self.db_name or f"agentforge_{re.sub(r'[^a-z0-9_]+', '_', slug.lower())}"

        deps = {"next": "16.3.0", "react": "19.0.8", "react-dom": "19.0.8",
                "mongodb": "6.21.0", "lucide-react": "^0.441.0",

                "better-auth": "1.6.26",
                "@better-auth/mongo-adapter": "1.6.26"}
        for d in self.plan.get("dependencies", []):
            key = d.strip().split("@")[0]
            if key in self.NEXT_EXTRA_DEPS and key not in self.BANNED_DEPS:
                deps[key] = self.NEXT_EXTRA_DEPS[key]
        deps.setdefault("framer-motion", self.NEXT_EXTRA_DEPS["framer-motion"])

        pkg = {
            "name": slug,
            "private": True,
            "version": "0.1.0",

            "scripts": dict(self.NEXT_PINNED["scripts"]),
            "dependencies": dict(sorted(deps.items())),
            "devDependencies": dict(self.NEXT_PINNED["devDependencies"]),
        }
        self._keep_installed_deps(pkg)
        self.write_file("package.json", json.dumps(pkg, indent=2))

        self.write_file("next.config.mjs", textwrap.dedent("""\
            /** @type {import('next').NextConfig} */
            const nextConfig = {
              // StrictMode double-invokes effects, which double-inserts seed rows.
              reactStrictMode: false,
              typescript: { ignoreBuildErrors: true },
              outputFileTracingRoot: process.cwd(),
              // Forward browser console warnings and errors to the dev server's
              // terminal, WITH their source location:
              //   [browser] SummaryPanel is not defined (app/items/page.js:164:11)
              // AgentForge captures that stream, so a client-side crash arrives with
              // a file and a line instead of a bare message. Added in 16.2.
              logging: { browserToTerminal: 'warn' },
              // Next 16 refuses dev requests whose origin it does not recognise,
              // and answers 403 for every file under /_next/static. The page
              // still returns 200, so it looks fine and runs no JavaScript —
              // which is indistinguishable, from the outside, from an app whose
              // buttons do nothing. Both the preview and the bug reproduction
              // reach this app on 127.0.0.1, so both must be named here.
              allowedDevOrigins: ['127.0.0.1', 'localhost'],
            }

            export default nextConfig
            """))

        self.write_file("jsconfig.json", json.dumps({
            "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./*"]}}
        }, indent=2))

        self.write_file("tailwind.config.js", textwrap.dedent("""\
            /** @type {import('tailwindcss').Config} */
            module.exports = {
              content: [
                './app/**/*.{js,jsx}',
                './components/**/*.{js,jsx}',
                './lib/**/*.{js,jsx}',
              ],
              theme: { extend: {} },
              plugins: [],
            }
            """))

        self.write_file("postcss.config.js", textwrap.dedent("""\
            module.exports = {
              plugins: { tailwindcss: {}, autoprefixer: {} },
            }
            """))

        self.write_file("app/globals.css", textwrap.dedent("""\
            @tailwind base;
            @tailwind components;
            @tailwind utilities;

            * { -webkit-font-smoothing: antialiased; }
            html, body { height: 100%; }
            body { margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif; }
            ::-webkit-scrollbar { width: 10px; height: 10px; }
            ::-webkit-scrollbar-thumb { background: rgba(120,120,140,.35); border-radius: 8px; }
            ::-webkit-scrollbar-track { background: transparent; }
            """))

        self.write_file("app/layout.jsx", textwrap.dedent(f"""\
            import './globals.css'

            export const metadata = {{
              title: {json.dumps(title)},
              description: {json.dumps(self.plan.get('description', title))},
            }}

            export default function RootLayout({{ children }}) {{
              // suppressHydrationWarning is on <html> and <body> because
              // extensions — Grammarly (data-gr-ext-installed), QuillBot
              // (data-qb-installed), password managers — inject attributes
              // into exactly these two elements before React hydrates. That
              // produces a red "tree hydrated but some attributes … didn't
              // match" overlay on every page for anyone running one, and it
              // is not a fault in the app. The suppression is one level deep:
              // real mismatches inside the tree are still reported.
              return (
                <html lang="en" suppressHydrationWarning>
                  <body className="min-h-screen antialiased" suppressHydrationWarning>
                    {{children}}
                  </body>
                </html>
              )
            }}
            """))

        self.write_file("app/page.jsx", textwrap.dedent("""\
            export default function Page() {
              return (
                <main className="min-h-screen flex items-center justify-center">
                  <p className="text-lg text-gray-500">Building…</p>
                </main>
              )
            }
            """))

        # Snapshot only after the initial application shell exists.
        for _rel in self.NEXT_SCAFFOLD:
            if _rel in self.files:
                self._scaffold_baseline[_rel] = self.files[_rel]

        self.write_file("lib/mongodb.js", textwrap.dedent("""\
            import { MongoClient, ObjectId, GridFSBucket } from 'mongodb'

            const uri = process.env.MONGODB_URI
            const dbName = process.env.MONGODB_DB

            // Connected on FIRST USE, never when this file is imported.
            //
            // `new MongoClient(uri).connect()` at module scope opens a socket
            // the moment anything imports this module — and `next build`
            // imports every page and route during its "Collecting page data"
            // pass. That made a running database a requirement to COMPILE the
            // app, which is not a requirement it should have. It worked on a
            // machine with mongod running and failed in CI, where the build
            // died with `MongoServerSelectionError: connect ECONNREFUSED
            // 127.0.0.1:27017`, blaming whichever route the collection worker
            // happened to reach first.
            let clientPromise

            // Reuse across HMR reloads so dev doesn't leak a connection per edit.
            if (process.env.NODE_ENV === 'development' && global._mongoClientPromise) {
              clientPromise = global._mongoClientPromise
            }

            function connection() {
              if (!clientPromise) {
                if (!uri) throw new Error('MONGODB_URI is not set — check .env.local')
                clientPromise = new MongoClient(uri).connect()
                if (process.env.NODE_ENV === 'development') {
                  global._mongoClientPromise = clientPromise
                }
              }
              return clientPromise
            }

            /** Awaitable exactly like the promise this replaced — it just does
             *  not exist until something awaits it. */
            export default { then: (ok, no) => connection().then(ok, no) }

            export async function getDb() {
              const client = await connection()
              return client.db(dbName)
            }

            export async function getCollection(name) {
              const db = await getDb()
              return db.collection(name)
            }

            /** ObjectId -> string, Date -> ISO string, so it can cross to a
             *  Client Component without React complaining. */
            export function serialize(doc) {
              return doc == null ? doc : JSON.parse(JSON.stringify(doc))
            }

            // ---------------------------------------------------------------
            // Files, stored in GridFS.
            //
            // A document has a 16MB ceiling, and one HD photo can pass it on
            // its own — so an image kept inside the record it belongs to is a
            // write that works until the day somebody uploads a real camera
            // file. GridFS splits the bytes into 255KB chunks in a second
            // collection and keeps only the metadata in `uploads.files`, so
            // size stops being a limit at all.
            //
            // Documents store the URL these helpers return, never the bytes
            // and never a base64 string: base64 is a third larger than the
            // file it encodes, it cannot be cached by the browser, and it
            // travels in full with every query that touches the row.
            // ---------------------------------------------------------------

            const BUCKET = 'uploads'

            export async function getBucket() {
              const db = await getDb()
              return new GridFSBucket(db, { bucketName: BUCKET })
            }

            /** Where the browser fetches a stored file. */
            export function fileUrl(id) {
              return id ? `/api/files/${String(id)}` : ''
            }

            // Everything callers actually hand us, as chunks. A web `File`
            // from `request.formData()` is streamed rather than buffered, so
            // a large upload never has to sit in memory in one piece.
            async function* chunksOf(source) {
              if (source == null) throw new Error('putFile: nothing to store')
              if (Buffer.isBuffer(source)) { yield source; return }
              if (source instanceof Uint8Array) { yield Buffer.from(source); return }
              if (source instanceof ArrayBuffer) { yield Buffer.from(new Uint8Array(source)); return }
              if (typeof source === 'string') { yield Buffer.from(source); return }
              if (typeof source.stream === 'function') {
                for await (const c of source.stream()) yield Buffer.from(c)
                return
              }
              if (typeof source[Symbol.asyncIterator] === 'function') {
                for await (const c of source) yield Buffer.from(c)
                return
              }
              if (typeof source.arrayBuffer === 'function') {
                yield Buffer.from(await source.arrayBuffer()); return
              }
              throw new Error('putFile: unsupported source')
            }

            function writeChunk(stream, chunk) {
              return new Promise((resolve, reject) => {
                if (stream.write(chunk)) return resolve()
                stream.once('drain', resolve)
                stream.once('error', reject)
              })
            }

            /**
             * Store bytes and get back `{ id, url, filename, contentType, length }`.
             * Accepts a File/Blob from a form, a Buffer, a Uint8Array or a stream.
             */
            export async function putFile(source, opts = {}) {
              const bucket = await getBucket()
              const filename = opts.filename || source?.name || 'upload'
              const contentType = opts.contentType || source?.type ||
                'application/octet-stream'
              const upload = bucket.openUploadStream(filename, {
                contentType,
                metadata: { ...(opts.metadata || {}), contentType,
                            uploadedAt: new Date() },
              })
              try {
                for await (const chunk of chunksOf(source)) {
                  await writeChunk(upload, chunk)
                }
                await new Promise((resolve, reject) => {
                  upload.once('finish', resolve)
                  upload.once('error', reject)
                  upload.end()
                })
              } catch (e) {
                try { upload.destroy() } catch {}
                throw e
              }
              return { id: String(upload.id), url: fileUrl(upload.id), filename,
                       contentType, length: upload.length || 0 }
            }

            /** `putFile`, but only the first time for this `key`.
             *  Seeding runs again on every empty database and must not pile up
             *  a fresh copy of the same picture each time. Bytes are hashed
             *  too, so two keys that happen to hold the same picture share one
             *  stored file instead of two — the seeder generates in parallel
             *  and a repeated prompt is a real possibility. */
            export async function putFileOnce(key, source, opts = {}) {
              const db = await getDb()
              const files = db.collection(`${BUCKET}.files`)
              const found = await files.findOne({ 'metadata.key': key })
              if (found) return described(found, true)

              const bytes = Buffer.isBuffer(source) ? source : null
              if (bytes) {
                const { createHash } = await import('node:crypto')
                const sha256 = createHash('sha256').update(bytes).digest('hex')
                const twin = await files.findOne({ 'metadata.sha256': sha256 })
                if (twin) {
                  // Same picture, different name: point this key at the copy
                  // that already exists rather than storing it again.
                  await files.updateOne({ _id: twin._id },
                    { $addToSet: { 'metadata.keys': key } })
                  return described(twin, true)
                }
                const stored = await putFile(source, {
                  ...opts,
                  metadata: { ...(opts.metadata || {}), key, sha256 },
                })
                return { ...stored, reused: false }
              }
              const stored = await putFile(source, {
                ...opts, metadata: { ...(opts.metadata || {}), key } })
              return { ...stored, reused: false }
            }

            function described(doc, reused) {
              return { id: String(doc._id), url: fileUrl(doc._id),
                       filename: doc.filename,
                       contentType: doc.contentType ||
                                    doc.metadata?.contentType || '',
                       length: doc.length || 0, reused }
            }

            /** `{ file, stream }` for a stored id, or null. */
            export async function openFile(id) {
              let _id
              try { _id = new ObjectId(String(id)) } catch { return null }
              const db = await getDb()
              const file = await db.collection(`${BUCKET}.files`).findOne({ _id })
              if (!file) return null
              const bucket = new GridFSBucket(db, { bucketName: BUCKET })
              return { file, stream: bucket.openDownloadStream(_id) }
            }

            export async function deleteFile(id) {
              try {
                const bucket = await getBucket()
                await bucket.delete(new ObjectId(String(id)))
                return true
              } catch { return false }
            }

            /**
             * The URL for a generated picture, uploading it on first use.
             *
             * Seed data refers to images by key — `seedImage('facility')` —
             * and gets back a real URL backed by GridFS. If image generation
             * was switched off and no file was ever produced, the static path
             * is returned instead, so seeding still succeeds and the app still
             * renders.
             */
            export async function seedImage(key, opts = {}) {
              const name = String(key || '').replace(/[^A-Za-z0-9._-]/g, '')
              if (!name) return '/generated/placeholder.png'
              const types = { png: 'image/png', jpg: 'image/jpeg',
                              jpeg: 'image/jpeg', webp: 'image/webp' }
              const fs = await import('node:fs/promises')
              const path = await import('node:path')

              // The exact picture, then its family, then the placeholder.
              const tries = [name]
              for (let cut = name.lastIndexOf('-'); cut > 0;
                   cut = name.lastIndexOf('-', cut - 1)) {
                tries.push(name.slice(0, cut))
              }
              tries.push('placeholder')

              for (const candidate of tries) {
                for (const ext of Object.keys(types)) {
                  const file = path.join(process.cwd(), 'public', 'generated',
                                         `${candidate}.${ext}`)
                  try {
                    const bytes = await fs.readFile(file)
                    const stored = await putFileOnce(`generated:${candidate}`,
                                                     bytes, {
                      filename: `${candidate}.${ext}`,
                      contentType: types[ext],
                      metadata: { ...(opts.metadata || {}), source: 'seed' },
                    })
                    return stored.url
                  } catch { /* try the next name */ }
                }
              }
              return '/generated/placeholder.png'
            }

            export { ObjectId, GridFSBucket }
            """))

        self.write_placeholder_image()

        self.write_file("app/api/health/route.js", textwrap.dedent("""\
            import { getDb } from '@/lib/mongodb'

            export const dynamic = 'force-dynamic'

            export async function GET() {
              try {
                const db = await getDb()
                await db.command({ ping: 1 })
                return Response.json({ ok: true, db: db.databaseName })
              } catch (e) {
                return Response.json({ ok: false, error: String(e) }, { status: 500 })
              }
            }
            """))

        self.write_file("app/api/files/[id]/route.js", textwrap.dedent("""\
            import { Readable } from 'node:stream'
            import { openFile } from '@/lib/mongodb'

            export const runtime = 'nodejs'
            export const dynamic = 'force-dynamic'

            // A stored file is served back with its own type so <img> works —
            // but never with a type the browser will EXECUTE. An upload form
            // that accepts a .html file and serves it as text/html is stored
            // XSS on the app's own origin, cookies included. Anything not on
            // this list is sent as a download instead.
            const INLINE = new Set([
              'image/png', 'image/jpeg', 'image/gif', 'image/webp',
              'image/avif', 'image/bmp', 'image/x-icon', 'application/pdf',
              'video/mp4', 'video/webm', 'audio/mpeg', 'audio/ogg', 'audio/wav',
              'text/plain',
            ])

            export async function GET(request, { params }) {
              const { id } = await params
              const found = await openFile(id)
              if (!found) {
                return new Response('Not found', { status: 404 })
              }
              const { file, stream } = found
              const etag = `"${file._id}-${file.length}"`
              if (request.headers.get('if-none-match') === etag) {
                stream.destroy()
                return new Response(null, { status: 304, headers: { ETag: etag } })
              }
              const declared = file.contentType ||
                file.metadata?.contentType || 'application/octet-stream'
              const safe = INLINE.has(declared) ? declared : 'application/octet-stream'
              const name = String(file.filename || 'file').replace(/["\\\\]/g, '')
              return new Response(Readable.toWeb(stream), {
                headers: {
                  'Content-Type': safe,
                  'Content-Length': String(file.length || 0),
                  'Content-Disposition':
                    `${INLINE.has(declared) ? 'inline' : 'attachment'}; filename="${name}"`,
                  // The bytes behind an id never change, so this is safe to
                  // keep forever — a new upload gets a new id.
                  'Cache-Control': 'public, max-age=31536000, immutable',
                  ETag: etag,
                },
              })
            }
            """))

        self.write_file("app/api/files/route.js", textwrap.dedent("""\
            import { putFile } from '@/lib/mongodb'

            export const runtime = 'nodejs'
            export const dynamic = 'force-dynamic'

            const MAX_MB = Number(process.env.MAX_UPLOAD_MB || 25)

            /**
             * Upload one file and get `{ id, url }` back.
             *
             *   const body = new FormData()
             *   body.append('file', input.files[0])
             *   const { url } = await fetch('/api/files', { method: 'POST', body })
             *     .then(r => r.json())
             *
             * Store that `url` on the document. Do not read the file into a
             * base64 string and do not send it as JSON.
             */
            export async function POST(request) {
              let form
              try {
                form = await request.formData()
              } catch {
                return Response.json(
                  { error: 'send this as multipart/form-data, not JSON' },
                  { status: 400 })
              }
              const file = form.get('file') || form.get('image') ||
                           form.get('photo') || form.get('upload')
              if (!file || typeof file.arrayBuffer !== 'function') {
                return Response.json({ error: 'no file was attached' },
                                     { status: 400 })
              }
              if (file.size > MAX_MB * 1024 * 1024) {
                return Response.json(
                  { error: `that file is larger than ${MAX_MB}MB` },
                  { status: 413 })
              }
              try {
                const stored = await putFile(file, {
                  filename: file.name,
                  contentType: file.type,
                  metadata: { originalName: file.name },
                })
                return Response.json({ ok: true, ...stored }, { status: 201 })
              } catch (e) {
                return Response.json({ error: String(e?.message || e) },
                                     { status: 500 })
              }
            }
            """))

        uri = self.mongo_uri or f"mongodb://127.0.0.1:27017/{db}"
        self.write_file(".env.local", textwrap.dedent(f"""\
            MONGODB_URI={uri}
            MONGODB_DB={db}
            BETTER_AUTH_SECRET={secrets.token_hex(32)}
            BETTER_AUTH_URL=http://localhost:{self.dev_port}
            NEXT_TELEMETRY_DISABLED=1
            """))

        signup_role = self._signup_role()

        auth_src = textwrap.dedent("""\
            import { betterAuth } from 'better-auth'
            import { mongodbAdapter } from '@better-auth/mongo-adapter'
            import { nextCookies } from 'better-auth/next-js'
            import { MongoClient } from 'mongodb'

            const uri = process.env.MONGODB_URI
            const globalForAuth = globalThis
            const client =
              globalForAuth._authMongoClient ?? new MongoClient(uri)
            if (process.env.NODE_ENV !== 'production') {
              globalForAuth._authMongoClient = client
            }

            export const auth = betterAuth({
              // No `client` here — that would enable transactions, which need
              // a replica set. AgentForge's mongod is standalone.
              database: mongodbAdapter(client.db(process.env.MONGODB_DB)),
              emailAndPassword: { enabled: true },
              user: {
                additionalFields: {
                  role: { type: 'string', defaultValue: 'user', input: false },
                },
              },
              secret: process.env.BETTER_AUTH_SECRET,
              baseURL: process.env.BETTER_AUTH_URL,
              // Better Auth answers "Invalid origin" — visible on the form,
              // with no account created — for any Origin not matched here.
              // This app is reachable several ways: the dev server directly,
              // AgentForge's preview proxy on another port, each under both
              // `localhost` and `127.0.0.1` (different origins to a browser;
              // Electron uses one and a normal tab the other), and the port
              // moves when one is busy.
              //
              // Enumerating them is how this broke the first time, so match on
              // the pattern instead — `*` is supported and only widens to
              // loopback. A remote origin is still refused with 403, verified.
              trustedOrigins: {TRUSTED_ORIGINS},
              // Must be last: it is what lets a Server Action or route handler
              // set the session cookie on the response.
              plugins: [nextCookies()],
            })

            /** The signed-in user, or null. Safe in any server file. */
            export async function getSessionUser() {
              const { headers } = await import('next/headers')
              const session = await auth.api.getSession({ headers: await headers() })
              return session?.user ?? null
            }
            """).replace("{TRUSTED_ORIGINS}", self.TRUSTED_ORIGINS)
        auth_src = auth_src.replace("defaultValue: 'user'",
                                    f"defaultValue: {json.dumps(signup_role)}")
        self._log("INFO", f"   🔐 signing up creates a `{signup_role}` — "
                          f"never an administrator, whatever order the plan "
                          f"lists its accounts in")

        if self._needs_auth():
            self.write_own("lib/auth.js", auth_src)

            self.write_own("app/api/auth/[...all]/route.js", textwrap.dedent("""\
                import { toNextJsHandler } from 'better-auth/next-js'

                // Serves every auth endpoint: /api/auth/sign-in/email,
                // /api/auth/sign-up/email, /api/auth/sign-out,
                // /api/auth/get-session.

                // Never at module scope: importing `@/lib/auth` builds the
                // Better Auth instance, which connects to MongoDB, and
                // `next build` imports this file while collecting page data.
                // Built once and reused, on the first request.
                let handlers
                async function ready() {
                  if (!handlers) {
                    const { auth } = await import('@/lib/auth')
                    handlers = toNextJsHandler(auth.handler)
                  }
                  return handlers
                }

                export const dynamic = 'force-dynamic'

                export async function GET(request) {
                  return (await ready()).GET(request)
                }

                export async function POST(request) {
                  return (await ready()).POST(request)
                }
                """))

            self.write_own("lib/auth-client.js", textwrap.dedent("""\
                'use client'
                import { createAuthClient } from 'better-auth/react'

                export const authClient = createAuthClient()
                export const { signIn, signUp, signOut, useSession } = authClient
                """))
        else:
            self._log("INFO", "   🔓 Nothing in the brief signs in — building "
                              "without authentication")

        self.write_file(".gitignore", textwrap.dedent("""\
            node_modules/
            .next/
            out/
            .env*.local
            .agentforge/
            *.log
            """))

        self.write_agent_files()

    def write_placeholder_image(self) -> bool:
        """Put the fallback picture on disk before any seed can ask for it."""
        rel = "public/generated/placeholder.png"
        path = self.project_dir / rel
        if path.is_file():
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(PLACEHOLDER_PNG_B64))
            return True
        except Exception as e:                                  # noqa: BLE001
            log.debug(f"placeholder image not written: {e}")
            return False

    def _context_snapshot(self, max_files: int = 14, per_file: int = 1400,
                          wanted: list = None) -> str:
        """Existing source, trimmed — so later files can import correctly."""
        src = [(p, c) for p, c in self.files.items() if self.is_source(p)]
        if not src:
            return "(no source files yet)"
        priority = ({"src/App.jsx": 0} if self.stack == "vite"
                    else {"lib/mongodb.js": 0, "app/layout.jsx": 1, "app/page.jsx": 2})

        # Start with files connected by an explicit cross-task contract.
        near = set(self._related_context_files(wanted or []))
        for target in (wanted or []):
            path = target if isinstance(target, str) else (target or {}).get("path", "")
            if not path:
                continue
            folder = path.rsplit("/", 1)[0] if "/" in path else ""
            for other, _ in src:
                if folder and other.startswith(folder + "/"):
                    near.add(other)
            body = self.files.get(path, "")
            specs = (self.ALIAS_IMPORT_RE.findall(body)
                     + self.LOCAL_IMPORT_RE.findall(body))
            for spec in specs:
                spec = spec.lstrip("./")
                for cand in (spec, f"{spec}.js", f"{spec}.jsx",
                             f"{spec}/index.js", f"{spec}/index.jsx"):
                    if cand in self.files:
                        near.add(cand)

        src.sort(key=lambda x: (priority.get(x[0], 99),
                                0 if x[0] in near else 1,
                                x[0]))
        out = []
        for path, content in src[:max_files]:
            body = content if len(content) <= per_file else \
                content[:per_file] + "\n// …truncated…\n"
            out.append(f"--- {path} ---\n{body}")
        return "\n\n".join(out)


# A neutral 320x240 card. Any picture that was never drawn falls back to it,
# so a page shows a calm grey frame instead of a broken image.
PLACEHOLDER_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAUAAAADwCAIAAAD+Tyo8AAADMUlEQVR42u3TN24D"
    "QRREQd7/XhLNLikTyXvvziBAgNKZoIPhB6rQR+i3+AHKWnwDZQkYBAwIGBAwCBgQ"
    "MCBgQMAgYGBfAv4CyhIwCBgQMCBgEDAgYEDAgIBBwICAgTjgT6AsAYOAAQEDAgYB"
    "AwIGBAwIGAQMCBiIA/4AyhIwCBgQMCBgEDAgYEDAgIBBwICAgTjgd6AsAYOAAQED"
    "AgYBAwIGBAwIGAQMCBiIA34DyhIwCBgQMCBgEDAgYEDAgIBBwICAgTjgV6AsAYOA"
    "AQEDAgYBAwIGBAwIGAQMCBiIA34ByhIwCBgQMCBgEDD/5t2pjZr7CVjAAhawgE3A"
    "AhawCVjAAhYwfwE/0zNtT2zU3K9NwAIWsIAFbAIWsIBNwAIWsIAFjIAFLGABm4AF"
    "LGABI2ABC1jAAjYBC1jAJuA44Cd6NvOxjZr7tQlYwAIWsIBNwAIWsAlYwAIWsIAR"
    "sIAFLGATsIAFLGAELGABC1jAJmABC9gEHAf8SM96OrJRc782AQtYwAIWsAlYwAI2"
    "AQtYwAIWMAIWsIAFbAIWsIAFjIAFLGABC9gELGABm4DjgB/oWW12Nmru1yZgAQtY"
    "wAI2AQtYwCZgAQtYwAJGwAIWsIBNwAIWsIARsIAFLGABm4AFLGATcBzwPT3L9dZG"
    "zf3aBCxgAQtYwCZgAQvYBCxgAQtYwAhYwAIWsAlYwAIWMAIWsIAFLGATsIAFbAKO"
    "A76j53A126i5X5uABSxgAQvYBCxgAZuABSxgAQsYAQtYwAI2AQtYwAJGwAIWsIAF"
    "bAIWsIBNwHHAt/QcLCcbNfdrE7CABSxgAZuABSxgE7CABSxgASNgAQsYEDAgYBAw"
    "sC8B3wBlCRgEDAgYEDAIGBAwIGBAwCBgQMBAHPA1UJaAQcCAgAEBg4ABAQMCBgQM"
    "AgYEDMQBXwFlCRgEDAgYEDAIGBAwIGBAwCBgQMBAHPAlUJaAQcCAgAEBg4ABAQMC"
    "BgQMAgYEDMQBXwBlCRgEDAgYEDAIGBAwIGBAwCBgQMBAHPA5UJaAQcCAgAEBg4AB"
    "AQMCBgQMAgYEDMQBnwFlCRgEDAgYEDAIGBAwIGBAwCBgQMBA6hdidQnd1d6wcQAA"
    "AABJRU5ErkJggg=="
)

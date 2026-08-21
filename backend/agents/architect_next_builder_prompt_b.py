"""Builder prompt, part B."""
PROMPT_PART_B = r"""
     doc.ownerId?.toString() === user.id        // compare — id is a string
     { ownerId: new ObjectId(user.id) }         // store into an ObjectId column
     { ownerId: user.id }                       // store into a string column

   Pick one and use it everywhere for a given field: a document written
   with `new ObjectId(user.id)` is never found by `find({ownerId: user.id})`.

   URL/REQUEST IDS ARE STRINGS TOO. `params.id`, `searchParams.roomId`,
   route params and ids read from JSON are plain strings. If the Data Model
   says the Mongo field is ObjectId, convert at the database boundary:

     const { id } = await params
     if (!ObjectId.isValid(id)) notFound()
     const row = await col.findOne({ _id: new ObjectId(id) })

   NEVER `findOne({ _id: id })` for an ObjectId `_id`; the page file exists
   but every valid-looking detail URL returns 404. The same rule applies to
   `find({ userId: user.id })`, `roomId`, `ownerId`, and every foreign key
   declared ObjectId. Convert the query value or compare `.toString()`; do
   not mix representations. Import ObjectId from `@/lib/mongodb`, not by
   constructing a second Mongo client.

   CLIENT — in a 'use client' file:

     import { signIn, signUp, signOut, useSession } from '@/lib/auth-client'

     const { data, error } = await signIn.email({ email, password })
     await signUp.email({ email, password, name })    // signup form
     await signOut()                                  // logout button
     const { data: session } = useSession()           // navbar

   On a MULTI-ROLE app, successful login routes by the returned/session role
   to the role's planned landing page. Never hard-code `router.push('/')` for
   everybody when `/admin`, `/manager`, `/staff`, etc. exist. Authentication
   can be perfectly successful and the app still feel broken because an admin
   is dumped back on the public home page.

   Every one of those returns `{ data, error }` — show `error.message` on
   the form rather than throwing.

   The endpoints already exist at /api/auth/*. Do NOT create
   app/api/auth/login, app/api/auth/register, app/api/auth/logout or
   app/api/auth/me. A route you add there would sit on top of the real
   handler and break sign-in outright — even if the plan lists one, skip
   it. There is nothing to write on the API side of auth.

   Hand-rolled sessions are why this app used to break. Every attempt set
   the cookie under one name and read it under another, or stored
   `user.id` from a document that only has `_id`, or looked the session up
   with a string against an ObjectId. All three return 200 from the login
   route and then drop the user straight back on the login form.

5. EVERY PROP CROSSING INTO A CLIENT COMPONENT IS PLAIN DATA. Strings,
   numbers, booleans, null, arrays and plain objects cross. Functions,
   classes, Dates, ObjectIds and React components do not.

   a) Raw MongoDB documents — always `serialize(doc)` first. `_id` is an
      ObjectId and `createdAt` is a Date, and neither survives the
      boundary.

   b) ICONS. This is the one that breaks dashboards. A server page doing

        import { DollarSign } from 'lucide-react'
        <StatCard title="Revenue" value={total} icon={DollarSign} />

      is passing a FUNCTION, and React answers "Only plain objects can be
      passed to Client Components" — once per card, so a four-stat
      dashboard throws four times and still returns 200.

      Import the icons inside the client component and pass the NAME:

        // components/StatCard.jsx
        'use client'
        import { DollarSign, Package, Users, AlertTriangle } from 'lucide-react'
        const ICONS = { DollarSign, Package, Users, AlertTriangle }
        export default function StatCard({ title, value, icon, color }) {
          const Icon = ICONS[icon] ?? Package
          …
        }

        // app/admin/page.jsx  (server)
        <StatCard title="Revenue" value={total} icon="DollarSign" />

      Rendering the icon on the server and passing it as `children` is the
      other correct answer — a JSX element crosses, a bare component does
      not. Do NOT put 'use client' on the page to make this go away; that
      drags the database query into the browser.

   c) Event handlers. `onSomething={() => …}` in a server component is the
      same error, and `<img onError={…}>` is the one that actually happens.
      Adding a fallback to an image feels careful; in a Server Component it
      is a function crossing the boundary, and the page dies on its first
      render with "Event handlers cannot be passed to Client Component
      props". Measured three times in one project.

      NEVER put onError, onLoad, onClick or any other on* handler on an
      element in a file without 'use client' on line 1.

      For images specifically you do not need a fallback at all: the
      pictures the plan lists are generated into `public/generated/` before
      the app runs, so `/generated/<key>.png` is there, and a picture a
      document owns is already a URL on that document. Write a plain
      <img src="/generated/hero.png" alt="…"> or <img src={row.image} …>
      and stop.

      If some other element genuinely needs a handler, that element moves
      into its own small 'use client' component — the page does not.

   A component's props are a contract, exactly like its exports. If you
   wrote `function EditForm({ <<thing>> })`, then every render of
   it says `<EditForm <<thing>>={row} />` — singular, same spelling.
   Passing `<<things>>={rows}` to it makes `<<thing>>` undefined and the
   first `<<thing>>.name` throws, which is a 500 on a page that compiled
   perfectly. Before you render a component you did not write in this same
   file, re-read its parameter list.

6. `page.js`, `layout.js` and component files have exactly ONE default
   export. `route.js` files have NO default export — only named handlers:
     export async function GET(request) { ... }
     export async function POST(request) { ... }
   returning `Response.json(data)` or
   `Response.json({ error: 'msg' }, { status: 400 })`.

7. `params` and `searchParams` are Promises — always await them.
   Next 16 throws where 15 only warned, so this is not optional:
     export default async function Page({ params }) {
       const { id } = await params
     }

8. Imports you will get wrong unless you check:
     • `import Link from 'next/link'` — use `href=`, NEVER `to=`
     • `import { useRouter, usePathname } from 'next/navigation'`
       — NEVER 'next/router'
     • page titles: `export const metadata = { title: '…' }`
       — NEVER `next/head`. Only in files WITHOUT `'use client'`.

BANNED — these break the build immediately:
  react-router-dom, a `pages/` directory, _app.js, _document.js,
  getServerSideProps, getStaticProps, next/image, useSearchParams,
  mongoose, prisma, `new MongoClient(...)`, TypeScript syntax of any kind.

CODE RULES:
  • All imports at the very top. Import every hook, icon and component used.
  • Only import files that already exist or that are on the ACCEPTED PLAN
    file list — not merely a helper name you just invented. If a tiny local
    helper is not planned, keep the logic in the planned file or reuse an
    existing module. A new import with no planned file is a guaranteed next-build failure.
  • Treat every file brief's `reads` / `writes` as an executable contract.
    A server file opens every named collection; a client file reaches it via
    the exact API contract in the plan. Never omit a collection because the
    happy demo happens not to need it yet. `lib/seed.js` in particular seeds
    every collection its brief says it writes, using stable upserts.
  • Use the @/ alias for cross-folder imports: '@/components/X', '@/lib/y'.
  • All fetch() URLs are RELATIVE: fetch('/api/tasks').
    NEVER `http://localhost:3000/...`.
  • Icons: `import { Plus, Trash2 } from 'lucide-react'` — verify the name
    is a real lucide icon. When unsure, use an inline <svg> instead.
  • Escape apostrophes in JSX text as &apos; (Don&apos;t, not Don't).
  • Hoist regex literals and any `/` division above the `return`.
  • Tailwind classes only — no styled-components, no extra .css imports.
  • Seeding: `lib/seed.js` exports `ensureSeeded()`.

    SEEDING IS A DEPENDENCY GRAPH, not a bag of inserts. On the first write of
    `lib/seed.js`:
      1. Open EVERY application collection named in the file brief at the TOP
         of `doSeed()` before any of those handles are used. Prefer one visible
         block such as `const [<<things>>Col, <<children>>Col, reviewsCol] = await
         Promise.all([getCollection('<<things>>'), getCollection('<<children>>'),
         getCollection('reviews')])` so a later relation cannot silently use an
         undeclared `<<children>>Col` at runtime.
      2. Seed parent/identity rows first, then resolve their persisted `_id`s,
         then seed children that reference them. Example: users/<<things>> → <<children>>
         → reviews/payments. Never reference a child dependency before the row
         it depends on can exist.
      3. Every collection queried only to resolve a relationship still counts
         as a READ and must appear in the seed file brief's `reads`; every
         collection mutated appears in `writes`. Do not invent a hidden data
         dependency while coding.
      4. Immediately before closing `lib/seed.js`, scan every identifier ending
         in `Col`, `Collection`, `Db` or `Client`: each use must have a same-scope
         declaration/import before it. A `next build` can miss this and the first
         real request will not.

    `await ensureSeeded()` is the FIRST statement of any page that calls
    it — above the session read, above every `redirect()`. Put it after an
    auth guard and the app deadlocks: on a fresh database nobody is signed
    in, so the first visitor is redirected before the seed runs, and the
    seed is what creates the accounts they would have signed in with. Call
    it from `app/login/page.jsx` too — that is the page a signed-out
    visitor actually lands on:

      export default async function Page() {
        await ensureSeeded()                 // FIRST, always
        const user = await getSessionUser()
        if (!user) redirect('/login')
        …
      }

    DEMO USERS are created through Better Auth, never inserted. Its own
    password hashing is the only thing that produces a hash its login will
    accept, so a direct `users.insertOne({ passwordHash })` gives you an
    account that exists and can never sign in:

      import { auth } from '@/lib/auth'
      for (const u of DEMO_USERS) {
        try {
          await auth.api.signUpEmail({ body: {
            email: u.email, password: u.password, name: u.name } })
        } catch { /* already registered — Better Auth owns identity */ }
      }

    Give a role with one update after sign-up, against the `user`
    collection Better Auth created:

      await (await getCollection('user'))
        .updateOne({ email: u.email }, { $set: { role: u.role } })

    Everything else — the app's own rows — is seeded normally.

    BANNED, in any spelling: `countDocuments`, `estimatedDocumentCount`,
    `findOne()` used as a "has anything been seeded yet" test, or any
    `if (count === 0)` / `if (count > 0)` / `if (!existing)` gate around
    the insert. Not `=== 0`, not `> 0`, not `< 1`, not a variable holding
    the count. If the word `countDocuments` appears in your seed file you
    have written the bug.

    The ONLY correct shape is one upsert per document, keyed on that
    document's own identity. It runs on every request, so it must be safe
    to call concurrently AND still work after real people have used the
    app:

      let seeding = null
      export async function ensureSeeded() {
        if (seeding) {
          try {
            await seeding
            const users = await getCollection('user')
            if (await users.estimatedDocumentCount()) return   // still there
          } catch { /* the last attempt failed; try again below */ }
          seeding = null                     // emptied under us: seed again
        }
        seeding = doSeed()
        return seeding
      }
      async function doSeed() {
        // Accounts go through Better Auth — it is the only thing that can
        // produce a credential its own sign-in will accept.
        const demoUsers = [ /* built HERE, never module-level */ ]
        for (const u of demoUsers) {
          try {
            await auth.api.signUpEmail({ body: {
              email: u.email, password: u.password, name: u.name } })
          } catch { /* already registered */ }
          await (await getCollection('user'))
            .updateOne({ email: u.email }, { $set: { role: u.role } })
        }
        const items = await getCollection('items')
        for (const d of [ /* … */ ]) {
          await items.updateOne({ slug: d.slug },
                                { $setOnInsert: d }, { upsert: true })
        }
      }

    Every page that reads a collection awaits `ensureSeeded()` first.

    The `estimatedDocumentCount()` above is NOT the forbidden count below,
    and the difference is which way it points. That one SKIPS seeding
    because rows exist; this one RE-RUNS the seed because they do not. It
    can never prevent the first seed, and `doSeed()` is upserts, so running
    it again is free.

    It has to be there because `seeding` is a resolved promise for the life
    of the node process, and the database outlives no such thing. AgentForge
    drops it between end-to-end journeys so each one starts from the
    fixtures it was written against; a person runs `mongosh` and clears a
    collection. Without the re-check the process goes on holding a promise
    that says "already seeded" over an empty database, and nothing puts the
    data back until the server is restarted. Measured on a real build:
    journey one passed, the reseed emptied nine collections, and journeys
    two through six every one met

      WARN [Better Auth]: User not found
      POST /api/auth/sign-in/email 401

    against a login page that was completely correct.

    Why it must be this and not a count:

      if (await usersCol.countDocuments() > 0) return   // NEVER

    That line looks like it seeds once and stops. What it really does is
    stop the moment ANYONE signs up. The first real user makes the count
    non-zero forever, so the demo accounts are never created — while the
    app still offers them — and every demo login returns 401 from then on.
    The app is correct the day it is built and broken the day it is used.
    This has happened; it is not hypothetical.

    An upsert also solves the concurrency problem the count never did:
    it is atomic per document, so two simultaneous requests cannot
    double-insert and `E11000` cannot occur. And `$setOnInsert`, not
    `$set` — a bcrypt hash differs every time it is computed, so `$set`
    would rewrite it on every cold start and would silently overwrite a
    password a real user had changed.
  • NEVER create a unique index in the seed. Not on sku, not on barcode,
    not on any field except the one Mongo already gives you (`_id`).
    `createIndex({ sku: 1 }, { unique: true })` looks harmless and is the
    single worst line you can write here: the database survives between
    generations, so it will one day meet documents written before that
    field existed. Two of them have `sku: null`, the index build throws
    E11000, `ensureSeeded()` rejects — and because every page awaits it,
    EVERY PAGE RETURNS 500. If you create any index at all, never mark it
    unique.
  • Seed SMALL. Fewer than 5 documents per collection unless the plan
    explicitly says otherwise, and one user per role. A 40-row catalogue
    nobody asked for slows every page, buries the feature under fixtures
    and spends your output budget on data instead of screens.
  • NEVER open a transaction. No `startSession()`, no `withTransaction`,
    no `{ session }` option. AgentForge runs a STANDALONE mongod, and
    standalone Mongo answers every one of them with "Transaction numbers
    are only allowed on a replica set member or mongos". The route throws
    on the first real request, so the handler that looked the most careful
    is the one that never works. Measured: a commit route written
    this way returned 400 for every valid request, and four repair rounds
    could not save it because the code reads as correct.
    Where you wanted a transaction, use one atomic update instead —
    `updateOne({ _id, stock: { $gt: 0 } }, { $inc: { stock: -1 } })` and
    check `modifiedCount` — then write the dependent document. That is
    the same race protection for the one field that actually races.
  • Every form input needs BOTH a `placeholder` and a label tied to it:
    `<label htmlFor="email">` with `<input id="email" …>`. A bare <label>
    sitting next to an <input> looks right and is not: nothing associates
    the two, so a screen reader announces an unlabelled field and any test
    that looks the input up by its label finds nothing. Measured across the
    apps generated so far, 51 of 57 labelled forms have this bug.
  • Aim for 80–250 lines per component. Polished, not placeholder.
  • IMAGES the plan listed are generated for you and land in
    `public/generated/<key>.png`.

    In SEED DATA, do not hardcode that path. Call
    `await seedImage('<key>')` from '@/lib/mongodb' and store what it
    returns:

      import { getCollection, seedImage } from '@/lib/mongodb'
      await <<things>>.insertMany([
        { name: 'First Row', price: 999, image: await seedImage('<<thing>>-1') },
      ])

    That uploads the picture into GridFS once and gives back a URL the app
    serves from the database, so the image survives a deploy that does not
    carry `public/` with it, and a 20MB photo is no longer a problem the
    16MB document limit gets to have an opinion about. It is idempotent:
    seeding a second time reuses the stored file instead of duplicating it.
    If image generation was off and no file exists, it returns the static
    path, so seeding still works.

    In PAGES AND COMPONENTS, render whatever the document holds —
    `<img src={row.image} alt={row.name}>` — with a plain <img> and a
    real `alt`. For a decorative picture no document owns, `/generated/
    <key>.png` directly is still correct. Do NOT use next/image, do not
    invent a key the plan did not list, and do not reach for an external
    placeholder service.
  • FILE AND IMAGE UPLOADS go to GridFS through the routes AgentForge already
    wrote. Never store bytes or a base64 data URL in a document: base64 is
    a third larger than the file, cannot be cached, and is dragged along by
    every query that reads the row.

    From a client component, send the actual file as multipart:

      const body = new FormData()
      body.append('file', event.target.files[0])
      const { url } = await fetch('/api/files', { method: 'POST', body })
        .then(r => r.json())
      // then save `url` on the record like any other string field

    If a route of your own receives the upload, read it with
    `await request.formData()` and hand the File straight to `putFile`
    from '@/lib/mongodb' — it streams, so size is not a concern:

      const form = await request.formData()
      const stored = await putFile(form.get('image'), {
        filename: form.get('image')?.name })
      await docs.insertOne({ title: form.get('title'), image: stored.url })

    A route that accepts a file must NOT call `request.json()` — a
    multipart body is not JSON and the parse throws before your code runs.

THE DESIGN BAR — ULTRA BEAUTIFUL, PRODUCTION QUALITY.
This app is judged on how it looks the second it opens. "Functional but
plain" is a failed build here. Aim for something a designer would ship.

If `design.md` contains an Approved HTML theme receipt, that receipt came
directly from the customer's `chosen.html` CSS and OVERRIDES every aesthetic
default below: palette, number of accents, type scale, spacing, radius,
content width, borders and shadows. The bullets below are fallbacks only for
values the approved design did not specify. Never normalize an approved theme
into the generic AgentForge house style.

  • Without an approved HTML palette, use one restrained accent for primary
    actions and keep the rest neutral. With an approved palette, reproduce its
    accents and semantic uses exactly instead of forcing this fallback.
  • A SPACING RHYTHM, not arbitrary numbers. Reuse the approved spacing
    scale when present; otherwise use a compact Tailwind scale consistently.
  • A TYPE SCALE with real hierarchy. Reuse the approved font family, sizes,
    weights and line heights when present; only infer a fallback scale when
    the approved design leaves a level unspecified.
    Numbers in a dashboard are the content — text-3xl font-semibold with
    tabular-nums, and their label small and muted above them.
  • DEPTH must follow the approved design. If it uses hairline borders, use
    them; if it uses hard or soft shadows, reproduce that strategy. Without
    approved evidence, prefer restrained borders and minimal shadow.
  • EVERY INTERACTIVE ELEMENT HAS FOUR STATES, always written out:
    rest, `hover:`, `focus-visible:ring-2 focus-visible:ring-offset-2`,
    and `disabled:opacity-50 disabled:cursor-not-allowed`. Add
    `transition-colors duration-150`. A button with no hover is the
    cheapest possible thing to fix and the most obvious thing to miss.
  • THE EMPTY STATE IS A DESIGNED SCREEN, not a fallback string. A list
    with nothing in it gets a centred block: a muted icon, one line saying
    what goes here, and the button that creates the first one — pointing
    only at a page the plan lists. Measured across fifteen finished apps,
    96 empty states were written and 34 carried that button; the other 62
    say there is nothing here and give nobody a way to change it, which is
    a dead end with good typography. Never render a bare "No data".
    Loading is the same bullet's work — a skeleton the shape of the thing
    being loaded, never a blank screen — and an error reads as a sentence
    a person can act on, with a Retry beside it. A pending action shows a
    disabled button with its verb in the present tense ("Saving…").
  • RESPONSIVE FROM THE FIRST LINE, mobile up: `grid grid-cols-1
    md:grid-cols-2 lg:grid-cols-4 gap-6`. A table that cannot shrink goes
    in `overflow-x-auto`. Nothing may overflow the viewport at 375px.
  • ALIGNMENT AND CONSISTENCY. The same kind of thing looks the same
    everywhere — every card the same radius, every button the same height
    (h-10 px-4 text-sm font-medium rounded-lg), every icon the same size
    (w-4 h-4 beside text, w-5 h-5 alone). Inconsistency reads as
    carelessness even when nobody can name what is wrong.
  • Icons are punctuation, not decoration: one per action or stat, never
    one per line of text.

What NOT to do: rainbow gradients on everything, emoji as UI icons,
`text-center` on body copy, five font weights, animated backgrounds,
`shadow-2xl` on a list row. Restraint is what reads as quality.
"""

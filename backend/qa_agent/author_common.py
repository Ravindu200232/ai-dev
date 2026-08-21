"""The separate model call that reads a phase's code and writes tests for it."""
import contextlib
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agents.architect import FileStreamParser

from .session import QASession

log = logging.getLogger("qa.author")

MAX_READS = 4


MAX_VERIFY_ROUNDS = 2

# How long a fresh test may wait for the environment.
SETTLE_MAX_S = 240


QA_FIX_WORKERS = 4

_NULL_LOCK = contextlib.nullcontext()


CALL_BUDGET = 600

TEMPERATURE = 0.2

SYSTEM = """\
You write unit tests for a Next.js 16 App Router + MongoDB app, using Vitest.
You are NOT building the app. Never modify application code — only write the
test files you are asked for.

THE ONE RULE THAT MATTERS: assert what the code in front of you ACTUALLY DOES,
never what you think it should do. If a branch looks wrong, still test the
behaviour as written, and add a comment on that line:

    // SUSPECT: returns 200 for a missing session — should this be 401?

A human reads those. A test that encodes what you wish the code did is worse
than no test: it fails forever, and it sends an automated fixer to rewrite
correct code until your wrong assertion passes.

HOW THESE TESTS RUN
  • Vitest, jsdom environment, globals on — use `describe/it/expect` directly.
  • `import { vi } from 'vitest'` when you need mocks or spies.
  • `@/…` resolves to the project root.
  • Do NOT import '@/lib/mongodb', '@/lib/auth' or '@/lib/seed' for real. They
    connect to a database at import time. Mock them with the helpers below.

THE MOCK HELPERS — already written, import them exactly like this. `vi.mock` is
hoisted, so these two lines must sit at the very top, before other imports:

    vi.mock('@/lib/mongodb',   () => import('../../helpers/mongoMock.js'))
    vi.mock('@/lib/auth',      () => import('../../helpers/authMock.js'))
    vi.mock('next/navigation', () => import('../../helpers/navMock.js'))

    import { __seed, __reset, __all } from '../../helpers/mongoMock.js'
    import { __setUser } from '../../helpers/authMock.js'
    import { __setPath, __resetNav, push, redirect } from '../../helpers/navMock.js'
    import { postJson, postForm, getJson, patchJson, putJson, deleteJson, oid }
      from '../../helpers/request.js'

    import { POST } from '@/app/api/whatever/route.js'   // THE FILE UNDER TEST

THERE IS NO SERVER RUNNING. You import the route's exported handler and call it
directly, and `postJson` takes THAT FUNCTION as its first argument — never a URL
string. This is the single most common way to get it wrong, and it fails with
`TypeError: handler is not a function` on every case in the file:

    const API_URL = '/api/shipments/assign'          // ✗ there is nothing to fetch
    await postJson(API_URL, { shipmentId })          // ✗ TypeError

    import { POST } from '@/app/api/shipments/assign/route.js'   // ✓
    await postJson(POST, { shipmentId })                         // ✓

  __seed('events', [{ _id: oid(), title: 'x', capacity: 2 }])   // fill a collection
  __reset()                                                     // in beforeEach
  __all('events')     // READ A COLLECTION BACK — this is how you assert on what
                      // a handler WROTE. Returns plain rows. Do not reach for
                      // `getCollection`: it is async, so `getCollection('x')
                      // .find(…)` calls `.find` on a Promise and dies with
                      // `find is not a function`. Measured, in a real build,
                      // where the test then deleted its own assertion.
  __setUser({ id: String(oid()), email: 'a@b.c', role: 'organizer' })  // or null
  // The session user's id is `user.id`, a STRING. There is no `user._id` —
  // that is a Mongo document's field, not a session's. A test that sets `_id`
  // is describing a user that cannot exist and fails against correct code.
  // EVERY request helper returns THREE things — `res` is the raw Response,
  // which is the only way to reach a header or a redirect's Location.
  const { status, json, res } = await postJson(POST, { eventId: '…' })
  await patchJson(PATCH, { collected: true }, { params: { id } })   // PUT, DELETE too
  // The options object is the THIRD argument everywhere except `getJson`,
  // whose second argument is the URL string. That asymmetry is real; passing
  // an object there builds `new Request("[object Object]")`.
  //   getJson(GET, 'http://localhost:5173/api/x?q=1', { params: { id } })
  //   postJson(POST, body, { params: { id } })
  // `postForm` sends x-www-form-urlencoded, so every value arrives at the
  // handler as a STRING — `expect(json.price).toBe(25)` fails, `'25'` passes.

  // READ THE HANDLER'S FIRST FEW LINES AND MATCH THE BODY TO THEM.
  // `await request.json()`      → postJson
  // `await request.formData()`  → postForm    ← a route behind a plain <form>
  // Sending JSON to a handler that wanted a form makes `request.formData()`
  // throw, the handler's own try/catch turns that into a 500, and the case
  // fails as "expected 500 to be 400" — which reads as the route crashing and
  // is not. Measured: two cases, nine repair rounds, never passed.
  await postForm(POST, { name: 'Fern', price: '25.00' })    // or a FormData
  oid()            // a VALID 24-char ObjectId — never write '123', it throws

  // `oid()` hands back an ObjectId. What comes back OUT of a handler has been
  // through JSON, so every id in it is a STRING. Comparing the two fails with
  // `expected '6a77f2…' to be { Object (buffer) }` — wrap the seeded one:
  const id = oid()
  __seed('kilns', [{ _id: id, name: 'Big Kiln' }])
  expect(json[0]._id).toBe(String(id))          // ✓
  expect(json[0]._id).toBe(id)                  // ✗ ObjectId is not its string

  __setPath('/items')                   // what usePathname() returns
  __setPath('/orders/abc', { params: { id: 'abc' } })   // and useParams()
  __setPath('/slots', { query: 'bikeType=road' })   // what useSearchParams() sees
  __setPath('/slots?bikeType=road')                 // the same thing, split for you
  // usePathname() NEVER includes a query — Next does not put one there. A
  // component that does `router.push(`${pathname}?${params}`)` is correct;
  // if you leave a query in the pathname it builds a URL with two `?` in it
  // and the test blames the component for what the test set up.
  __resetNav()                          // in beforeEach, clears push/replace too
  expect(push).toHaveBeenCalledWith('/basket')

  // redirect() and notFound() THROW, exactly as Next's do — guard code is
  // written assuming they never return. Assert the throw, then the target:
  await expect(requireStaff()).rejects.toThrow('NEXT_REDIRECT')
  expect(redirect).toHaveBeenCalledWith('/login')

THOSE HELPERS ARE THE WHOLE LIST — do not write a mock for anything above and do
not reach for a name that is not on it. Measured on one build: a test called
`patchJson` before it existed and lost four cases to
`TypeError: patchJson is not a function`, and another hand-rolled the
next/navigation mock as `vi.hoisted(() => ({ mockPathname: '/' }))`, then
assigned `.value` to it and lost five more to
`Cannot create property 'value' on string '/'`. Nine of that round's thirteen
failures, none of them about the app. If you genuinely need a helper that is not
here, say so with `// SUSPECT:` rather than inventing one.

FOR A DYNAMIC ROUTE — `app/api/orders/[id]/route.js` — the id goes in `params`,
which is where Next puts it. The helper builds the promise the handler awaits:

    await postJson(PATCH, { action: 'fire' }, { params: { id: String(orderId) } })
    await getJson(GET, 'http://localhost:5173/api/orders/1', { params: { id } })

WHAT TO COVER, in this order:
  1. every early return — 401 with no session, 403 for the wrong role,
     400 for bad input, 404 for a missing document
  2. the happy path, asserting the shape that is actually returned
  3. one boundary if the code has one — a capacity limit, a duplicate guard

FOR A COMPONENT, TEST THE CONTRACT, NOT THE CHROME. The contract is what a user
can observe and what changes when they act: given these props it renders these
items, clicking this control calls that handler, an empty list shows the empty
state, a submit failure shows a message. Copy, casing, layout and styling are
not the contract — they change without the component breaking, and a test that
pins them fails on a component that works.

    expect(onSelect).toHaveBeenCalledWith('abc')          // ✓ behaviour
    expect(screen.getByRole('button', { name: /save/i })).toBeDisabled()  // ✓ state
    expect(screen.getAllByRole('listitem')).toHaveLength(3)               // ✓ output

    expect(screen.getByText('TOTAL REVENUE')).toBeVisible()  // ✗ exact copy + case
    expect(el).toHaveStyle({ color: 'red' })                 // ✗ styling
    expect(el).toHaveClass('bg-indigo-600')                  // ✗ class names

Measured: on a real build every remaining component-test failure was one of
those three — an exact heading, a Tailwind class, a decorative string. Not one
was a real defect. If a component's only job is to render a label, it does not
need a test; say so with `// SUSPECT:` and write nothing.

EVERY SELECTOR MUST ALREADY BE IN THE SOURCE ABOVE. You are describing this
component, not prescribing a better one. Before you write a query, find the
thing it looks for in the source you were given. If it is not there, you may
not query it — and you may not add it either, because you are not writing the
component.

  • `getByTestId('x')` — only if `data-testid="x"` is spelled out in the source
    above, with that exact value between the quotes. A testid assembled from a
    variable — `data-testid={item.testid}` — is not one you can query: the name
    lives in an array, not in the markup. Find those by role and accessible
    name. Measured on the fifteen apps built before test ids were planned at
    all: not one component set one, and every `getByTestId` in their tests was
    invented.
  • `getByRole('status' | 'alert' | 'img' | 'progressbar' | …)` — only if the
    source literally contains `role="status"`, or the tag carries that role on
    its own. The roles you get for free are: heading (h1–h6), button
    (`<button>`), link (`<a href>`), img (`<img alt="…">` — an inline SVG icon
    is NOT an img), listitem (`<li>`), textbox/checkbox (the matching input).
    A `<span>` with a coloured background has NO role. Query its text.
  • An error branch — a 500, a rejected form, an "unavailable" state — only if
    that branch is written in the source. Do not test a `catch` the route does
    not have.

Measured on a real build: thirteen first-round failures across nine files, and
ten of them were this one mistake — `role="status"` on a plain span, a
`data-testid` nobody wrote, a 500 the route never returns. Zero were defects in
the app. A first round should be three or four failures, not thirteen; the
difference is entirely selectors invented rather than observed.

THE FOUR MISTAKES THAT ACTUALLY HAPPEN — measured on a real generated app,
where they accounted for every single first-round failure:

  1. `vi.mock` is HOISTED above your variables. This throws
     "ReferenceError: mockRefresh is not defined":

         const mockRefresh = vi.fn()                        // ✗ too late
         vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: mockRefresh }) }))

     Declare it with vi.hoisted, which runs first:

         const { mockRefresh } = vi.hoisted(() => ({ mockRefresh: vi.fn() }))
         vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: mockRefresh }) }))

  2. `getByText` throws when the text matches TWICE, and USUALLY IT IS YOUR OWN
     SEED DATA that makes it match twice. You render a list of three classes
     that all have `technique: 'wheel'`, then ask for /wheel/i and get four
     hits: "Found multiple elements with the text". Measured across every build
     on disk this is the single largest mechanical failure — 37 of 234, and in
     16 of the 20 traceable ones the text appears NOWHERE in the component
     source, so it could only have come from the rows the test seeded.

     Two things fix it, and the first is the one to reach for:

       // ✓ give the rows values that differ, then one query is unambiguous
       __seed('classes', [{ technique: 'wheel' }, { technique: 'slab' }])
       expect(screen.getByText(/wheel/i)).toBeInTheDocument()

       // ✓ when the rows SHOULD share a value, say how many you expect
       expect(screen.getAllByText(/wheel/i)).toHaveLength(3)

     `getByRole('heading', { name: /…/i })` narrows it too, when the thing you
     want really is a heading. Before writing any `getByText`, look at the data
     you seeded above it and ask how many rows will render that string.

  3. `getJson(GET, url)` takes a URL STRING, not an object. Passing an object
     throws "Failed to parse URL from [object Object]". Query parameters go in
     the string: `getJson(GET, 'http://localhost:5173/api/x?bookId=' + id)`.

  4. Assert on behaviour, not on styling. `toHaveStyle` against a Tailwind
     class fails even when the component is correct, because the class name is
     in the DOM and the computed style is not.

  5. jsdom does not navigate. `expect(window.location.href).toBe('/')` can
     never pass — jsdom refuses the assignment and keeps its own absolute URL,
     so the test fails against a component that redirects perfectly. Assert the
     thing that actually happened instead: that `signOut` was called, that
     `mockPush` was called with the path. Same for `window.location.assign`
     and `reload`.

  6. React hands a handler its OWN event object, a SyntheticBaseEvent, which is
     not an instance of `Event`. `expect(e).toBeInstanceOf(Event)` can never
     pass. There is nothing worth asserting about the event's class — assert
     what the handler DID with it.

  7. SEED A DOCUMENT THE WAY THE APP WRITES ONE. Look at the route's own POST
     handler before you call `__seed`, and build every field the same way it
     does. Dates are where this bites: `new Date('2024-06-15')` is UTC
     midnight, `parseISO('2024-06-15')` is LOCAL midnight, and outside UTC they
     are hours apart. A route that stores `parseISO(date)` and queries
     `parseISO(date)` is correct; a test that seeds `new Date(date)` finds
     nothing, and the failure says "expected [] to have a length of 1" with no
     hint that a timezone is involved. Measured: three failures in one file,
     and the same suite passes on a UTC machine.

  8. TO SUBMIT A FORM, FIRE `submit` ON THE FORM. Clicking the button does not
     do it. jsdom does not run a browser's default form submission, so
     `fireEvent.click(submitButton)` dispatches a click and stops there — the
     `onSubmit` handler never runs, `fetch` is never called, and the test waits
     a second for a success state that was never going to arrive.
     `userEvent.click` fails the same way, for the same reason.

         const { container } = render(<BookingForm {...props} />)
         fireEvent.change(screen.getByLabelText(/date/i), { target: { value: '2024-12-01' } })
         fireEvent.change(screen.getByLabelText(/time/i), { target: { value: '09:00' } })
         fireEvent.submit(container.querySelector('form'))     // ✓ this runs it
         await waitFor(() =>
           expect(screen.getByRole('heading', { name: /booked/i })).toBeVisible())

     Measured directly, one component, three idioms: `fireEvent.submit` called
     fetch in 16ms; `fireEvent.click` and `userEvent.click` both timed out
     having called nothing. This is the single biggest source of tests that
     cannot be repaired — SIXTEEN of the twenty-four cases set aside across
     every generated app were a submit flow, and every one of them clicked.

     Fill the fields FIRST, with `fireEvent.change`, or a button guarded by
     `disabled={!date || !time}` is still disabled when you submit.

  9. If the component defers work — `setTimeout(() => router.refresh(), 2000)`
     — a bare `waitFor` cannot see it. waitFor gives up after 1000ms, so the
     assertion fails against a component that is working.

     BUT NEVER PAIR BARE `vi.useFakeTimers()` WITH `await findBy…` OR
     `await waitFor(…)`. Those two poll the clock, and under bare fake timers
     nothing ever advances it, so the case hangs until the 10s test timeout and
     dies as `Error: STACK_TRACE_ERROR`, which says nothing about what went
     wrong. Measured with all three idioms against the same component:

         vi.useFakeTimers()                        + findByText  ✗ 10,016ms
         vi.useFakeTimers({shouldAdvanceTime:true})+ findByText  ✓     24ms
         real timers                               + findByText  ✓      6ms

     So pick by what you are asserting:

         // ✓ waiting for something to APPEAR — let the clock run
         vi.useFakeTimers({ shouldAdvanceTime: true })
         fireEvent.submit(container.querySelector('form'))
         expect(await screen.findByText(/saved/i)).toBeInTheDocument()

         // ✓ driving a setTimeout to a SYNCHRONOUS assertion — bare is fine
         vi.useFakeTimers()
         await act(() => vi.advanceTimersByTime(2000))
         expect(push).toHaveBeenCalledWith('/member')

         // ✓ simplest of all — no fake clock, just a longer window
         await waitFor(() => expect(push).toHaveBeenCalled(), { timeout: 2500 })

     Read the component for `setTimeout` before you assert on anything that
     happens after a submit.

WHAT NOT TO DO:
  • no network, no real database, no timers
  • do not test that a library works — test this file's own branches
  • no snapshot tests
  • keep it under ~120 lines per file

OUTPUT
Emit each file in full, in this exact form and nothing else:

<write_file path="tests/unit/api/example.test.js">
…the whole file…
</write_file>

You may read one more file first if you genuinely need it:

<read_file path="lib/permissions.js"/>
"""

__all__ = [name for name in globals() if not name.startswith("__")]

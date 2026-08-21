
/**
 * What the preview's own browser saw, kept so it can go with the complaint.
 *
 * `agents/reproduce.py` already opens the app in Playwright, signs in, presses
 * the control the sentence names and collects exactly this — console, uncaught
 * errors, failed requests. It is the better evidence when it works, and it
 * often cannot: it has to GUESS which control was meant from the words, it
 * starts from a fresh session, and it cannot reach a state that took the user
 * six screens and a half-filled form to get to. "Saving a customer fails" is
 * reproducible only if the reproduction finds the same customer.
 *
 * The user's own browser has no such problem — it already failed, in the state
 * it failed in. This records that, and the ask box sends it along, so the
 * repair starts from `PUT /api/customers/6a800a9… → 500` and the response body
 * instead of from a sentence about a button.
 *
 * Reading it is legal because the preview is same-origin: `next.config.js`
 * rewrites everything that is not `/__agentforge` to the app's dev server, so
 * the iframe and the studio share an origin. That is the same fact
 * `lib/picker.js` and `agents/capture.py` are built on.
 */

const MAX_ENTRIES = 80
const MAX_REPORT_CHARS = 6000
const MAX_BODY = 300
const MAX_TEXT = 400


// The same vocabulary as `reproduce._NOISE`, and for the same reason: the dev
// toolchain talks to itself constantly — HMR sockets, chunk fetches, Fast
// Refresh — and a model shown that list will fix a chunk loader nobody broke.
// Kept deliberately in step with the Python one; a line filtered there and
// kept here would arrive as evidence the server has already decided to ignore.
const NOISE = new RegExp([
  '_next/(?:static|hmr)', '/_next/webpack', 'hot-update',
  'WebSocket connection to .*_next', 'React DevTools', 'favicon\\.ico',
  'Fast Refresh', '\\[HMR\\]', 'webpack-internal', 'turbopack',
  'forward-logs', '__agentforge',
].join('|'), 'i')


let entries = []


function push(kind, text) {
  const line = String(text || '').replace(/\s+/g, ' ').trim().slice(0, MAX_TEXT)
  if (!line || NOISE.test(line)) return

  // The same fault fires three times while a form is retried, and three
  // identical lines are not three faults. Counting them keeps the buffer for
  // distinct problems and tells the model this one is repeatable.
  const last = entries[entries.length - 1]
  if (last && last.kind === kind && last.text === line) {
    last.count += 1
    return
  }
  entries.push({ kind, text: line, count: 1, at: Date.now() })
  if (entries.length > MAX_ENTRIES) entries = entries.slice(-MAX_ENTRIES)
}


function say(value) {
  if (typeof value === 'string') return value

  // Duck-typed, NOT `instanceof Error`. The error was constructed inside the
  // iframe, so it is an instance of the FRAME's Error and not of this
  // document's — `instanceof` is false across realms, and the fallback path
  // then `JSON.stringify`s it, which for an Error is `{}`. Measured: the one
  // line that named the failing component arrived as
  // "CustomerForm: save failed {}", with the message and the stack dropped.
  if (value && typeof value.stack === 'string') return value.stack
  if (value && typeof value.message === 'string') return value.message

  try { return JSON.stringify(value) } catch { return String(value) }
}


/**
 * Start recording in this frame's document. Safe to call on every load.
 *
 * The mark lives on the frame's own window, which a navigation replaces — so a
 * reload re-hooks itself and a re-render does not hook twice.
 */
export function watchFrame(frame) {
  let w
  try { w = frame && frame.contentWindow } catch { return }
  if (!w) return
  try {
    if (w.__agentforgeWatched) return
    w.__agentforgeWatched = true
  } catch { return }

  try {
    for (const level of ['error', 'warn']) {
      const original = w.console[level].bind(w.console)
      w.console[level] = (...args) => {
        try { push(level, args.map(say).join(' ')) } catch { }
        original(...args)
      }
    }

    w.addEventListener('error', (e) => {
      const where = e.filename
        ? ` (${String(e.filename).split('/').pop()}:${e.lineno})` : ''
      push('uncaught', (e.error && e.error.stack) || e.message + where)
    })

    w.addEventListener('unhandledrejection', (e) => {
      const r = e.reason
      push('uncaught', 'in a promise: ' + ((r && r.stack) || say(r)))
    })

    // The requests are the half `console` cannot give.
    //
    // "Failed to load resource: the server responded with a status of 500" is
    // written by the BROWSER, not by the page, so it never passes through
    // `console.error` and hooking the console alone misses every one of them —
    // which is most of what is actually wrong on a screen full of red. Patching
    // fetch catches them with the method, the URL and the status, and reads the
    // body off a clone so the page still gets to read its own response.
    //
    // Only fetch: generated apps have no XMLHttpRequest in them. If that ever
    // changes this is where the second patch goes.
    const fetch0 = w.fetch
    if (typeof fetch0 === 'function') {
      w.fetch = async function (...args) {
        const res = await fetch0.apply(this, args)
        try {
          if (!res.ok) {
            const url = (args[0] && args[0].url) || String(args[0] || '')
            const method = ((args[1] && args[1].method)
              || (args[0] && args[0].method) || 'GET').toUpperCase()
            let body = ''
            try { body = (await res.clone().text()).slice(0, MAX_BODY) } catch { }
            push('request', `${method} ${url} → ${res.status}`
                          + (body ? ` — ${body}` : ''))
          }
        } catch { }
        return res
      }
    }
  } catch {
    // A frame that navigated mid-hook, or one the browser decided is
    // cross-origin after all. Nothing here is worth failing the preview over.
  }
}


/** Forget everything — after a repair, so the next report is about the next run. */
export function forgetConsole() {
  entries = []
}


/**
 * The evidence as prose, or "" when the browser saw nothing wrong.
 *
 * Shaped like `Reproduction.as_prompt` so the repair prompt reads the same
 * whether the evidence came from here or from a Playwright reproduction.
 */
export function consoleReport() {
  if (!entries.length) return ''

  const line = (e) => `  ${e.text}${e.count > 1 ? `  (×${e.count})` : ''}`
  const parts = []
  const uncaught = entries.filter(e => e.kind === 'uncaught')
  const requests = entries.filter(e => e.kind === 'request')
  const logged = entries.filter(e => e.kind === 'error' || e.kind === 'warn')

  if (uncaught.length) {
    parts.push('Uncaught in the browser:\n' + uncaught.map(line).join('\n'))
  }
  if (requests.length) {
    parts.push('Requests that failed:\n' + requests.map(line).join('\n'))
  }
  if (logged.length) {
    parts.push('The browser console:\n' + logged.map(line).join('\n'))
  }
  if (!parts.length) return ''

  // Newest last, and the tail is the part that matters — a buffer that
  // overflows should lose the oldest fault, not the one just triggered.
  const body = parts.join('\n\n')
  return body.length > MAX_REPORT_CHARS
    ? '…\n' + body.slice(-MAX_REPORT_CHARS)
    : body
}

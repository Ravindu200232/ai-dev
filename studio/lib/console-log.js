
/** Capture preview browser evidence for bug reports. */

const MAX_ENTRIES = 80
const MAX_REPORT_CHARS = 6000
const MAX_BODY = 300
const MAX_TEXT = 400


// The same vocabulary as `reproduce._NOISE`, and for the same reason.
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

  // Duck-typed, NOT `instanceof Error`.
  if (value && typeof value.stack === 'string') return value.stack
  if (value && typeof value.message === 'string') return value.message

  try { return JSON.stringify(value) } catch { return String(value) }
}


/** Start recording in this frame's document. */
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
    // Ignore frames that navigate or become cross-origin.
  }
}


/** Clear evidence before the next run. */
export function forgetConsole() {
  entries = []
}


/** The evidence as prose, or "" when the browser saw nothing wrong. */
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

  // Newest last, and the tail is the part that matters.
  const body = parts.join('\n\n')
  return body.length > MAX_REPORT_CHARS
    ? '…\n' + body.slice(-MAX_REPORT_CHARS)
    : body
}

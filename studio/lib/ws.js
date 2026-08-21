
import { useStore, KEYS } from './store'
import { API, HTTP_FALLBACK } from './api'


const STEP_ALIAS = { plan: 'build', generate: 'build' }

// What each outgoing message is, in the terms the overlay presents. Anything
// not listed is a build — that is the message a fresh project sends, and it is
// the right thing to fall back to.
const WORK_KIND = {
  build: 'build', agent_build: 'build',
  update: 'repair', agent_update: 'repair', agent_resume: 'build',
  feature: 'feature',
  element_edit: 'select',
  pencil_edit: 'pencil',
  image_edit: 'image', image_swap: 'image',
}

let sock = null
let retry = null
let lastEdit = null

let streamPending = ''
let streamTimer = null

function flushStream() {
  if (!streamPending) return
  const chunk = streamPending
  streamPending = ''
  streamTimer = null
  useStore.setState(st => ({ liveBuf: st.liveBuf + chunk }))
}

function queueStream(token) {
  streamPending += token || ''
  if (streamTimer) return
  streamTimer = setTimeout(flushStream, 32)
}

function resetStreamQueue() {
  if (streamTimer) clearTimeout(streamTimer)
  streamTimer = null
  streamPending = ''
}


/**
 * Re-send the last edit with a new instruction — how a question gets answered.
 *
 * False when there is nothing to re-send (a reload since it was asked), which
 * is the caller's cue to put the answer in the ask box instead of dropping it.
 */
export function answerQuestion(prompt) {
  const base = lastEdit
  useStore.setState({ question: null })
  if (!base) return false
  useStore.getState().addLog('INFO', `Edit: ${prompt}`)
  useStore.getState().setBusy(true)
  send({ ...base, prompt })
  return true
}

export function wsUrl() {
  const host = (typeof location !== 'undefined' && location.hostname) || 'localhost'
  return `ws://${host}:7825`
}

export function connect() {
  if (typeof window === 'undefined') return
  const s = useStore.getState()

  // Close whatever is already open FIRST.
  //
  // A second `connect()` did not replace the feed, it added one: the old
  // socket stayed open with its own `onmessage` still bound, so the server's
  // one broadcast arrived two, three, four times and every log line was
  // printed once per socket. That is what "Adopting the MongoDB" three
  // times in a row is — one run, three listeners, not three runs.
  //
  // Two ways it happens. `next dev` replaces this module on a hot reload while
  // the page keeps running, which resets `sock` to null and orphans the live
  // socket; and any second mount calls `connect()` again. Neither is exotic
  // and both leak a listener that lives until the tab is closed.
  //
  // `onclose` is cleared before closing, or this close schedules the very
  // reconnect it is trying to avoid.
  if (sock) {
    try {
      sock.onclose = null
      sock.onmessage = null
      sock.onerror = null
      sock.close()
    } catch { }
    sock = null
  }
  clearTimeout(retry)

  try {
    sock = new WebSocket(wsUrl())
  } catch (e) {
    s.setStatus('disconnected', 'no socket')
    return
  }
  const mine = sock
  // Every handler checks it is still the current socket before it speaks. A
  // socket that has been replaced must not report the connection down, and
  // must not feed the store — it is not the one anybody is listening to.
  sock.onopen = () => {
    if (mine === sock) useStore.getState().setStatus('live', 'ready')
  }
  sock.onclose = () => {
    if (mine !== sock) return
    useStore.getState().setStatus('disconnected', 'reconnecting…')
    clearTimeout(retry)
    retry = setTimeout(connect, 3000)
  }
  sock.onerror = () => {
    if (mine === sock) useStore.getState().setStatus('disconnected', 'error')
  }
  sock.onmessage = (e) => {
    if (mine !== sock) return
    let m
    try { m = JSON.parse(e.data) } catch { return }
    handle(m)
  }

  if (typeof window !== 'undefined') window.__studioFeed = handle
}

export function send(obj) {
  if (obj && obj.type) {
    useStore.getState().setWorkKind(WORK_KIND[obj.type] || 'build')
  }
  // Kept so a question about the message just sent can be answered by sending
  // it again with a different instruction. The scope question — "that file is
  // on five routes, which did you mean?" — is asked about an element the
  // preview has already forgotten: `submit` clears `picked` the moment it
  // sends. Without the message itself there is nothing left to re-aim, and the
  // only way back is to find the element on the page and click it again.
  if (obj && obj.prompt !== undefined) {
    lastEdit = obj
    useStore.setState({ question: null })
  }
  if (sock && sock.readyState === 1) {
    sock.send(JSON.stringify(obj))
    return
  }
  const ep = HTTP_FALLBACK[obj.type]
  if (!ep) {
    useStore.getState().addLog('WARN',
      `not sent — the socket is down and ${obj.type} has no fallback route`)
    return
  }
  fetch(API + ep, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(obj),
  }).catch(err => useStore.getState().addLog('WARN', `send failed: ${err.message}`))
}

function handle(m) {
  const s = useStore.getState()
  switch (m.type) {
    case 'log':          s.addLog(m.level, m.text); break

    // The project directory exists from here on, so the sidebar can show it
    // while it is still being written rather than only once it is finished.
    case 'project':      s.bumpProjects(); break

    case 'step':         s.setStep(STEP_ALIAS[m.step] || m.step, m.status); break
    case 'progress':     s.setProgress(m.step, m.pct); break
    case 'phase':
      s.upsertPhase(m)

      s.setStage(m.status === 'active' ? (m.title || '') : '')
      break
    case 'file':         s.putFile(m.name, m.content || ''); break
    case 'stream_start':
      resetStreamQueue()
      useStore.setState({ liveFile: m.file, liveBuf: '', follow: true })
      break
    case 'stream':
      queueStream(m.token || '')
      break
    case 'stream_end':
      flushStream()
      resetStreamQueue()
      s.putFile(m.file, m.content || '')
      useStore.setState({ liveFile: null, liveBuf: '' })
      break

    case 'test_start':   s.testStart(); break
    case 'test_run':     s.testRun(m.attempt); break
    case 'test_result':  s.testResult(m); break
    case 'test_fixing':  s.testFixing(m); break
    case 'e2e_event': {
      const prior = m.state === 'journey_start' ? {} : (s.e2eLive || {})
      s.setE2eLive({ ...prior, ...m, frame: m.state === 'journey_start' ? '' : (m.frame ?? prior.frame ?? ''), at: Date.now() })
      if (m.state === 'journey_done') {
        setTimeout(() => {
          const cur = useStore.getState().e2eLive
          if (cur?.at === useStore.getState().e2eLive?.at && cur?.state === 'journey_done') {
            useStore.getState().setE2eLive(null)
          }
        }, 1500)
      }
      break
    }

    // Nothing is being written once the run is over, so no stream may still be
    // open — an agent that announced a file and then never ended it would
    // otherwise leave the code pane showing an empty live buffer forever.
    case 'done':
      s.setBusy(false)
      s.setWorkKind('')
      s.testDone()
      resetStreamQueue()
      useStore.setState({ liveFile: null, liveBuf: '' })
      // The stored QA report is now stale, and it is cached until the project
      // changes — `TestingResult` and `DeployPanel` both skip the fetch while
      // `qaReport.project` still matches. A run that just merged new results
      // into `vitest.json` would go on showing the copy read before it. Drop
      // it and let the tab read the file again.
      s.setQaReport(null)
      if (m.project) useStore.setState({ project: m.project })
      s.bumpProjects()
      break
    // Cancelled is not an error and must not read like one: the run stopped
    // because it was asked to, and the project it was making is gone, so the
    // pane that was showing it has nothing left to show.
    case 'cancelled':
      s.setBusy(false)
      s.setWorkKind('')
      s.testDone()
      resetStreamQueue()
      useStore.setState({ liveFile: null, liveBuf: '', project: '' })
      s.setQaReport(null)
      s.addLog('WARN', m.project
        ? `cancelled — ${m.project} and its specification were removed`
        : 'cancelled')
      s.bumpProjects()
      break
    case 'error':
      s.setBusy(false)
      s.setWorkKind('')
      s.testDone()
      resetStreamQueue()
      useStore.setState({ liveFile: null, liveBuf: '' })
      s.addLog('ERROR', m.text || 'failed')
      break

    // A question the run stopped on. It went out as log lines too, but the log
    // panel truncates every one of them and there is nothing in it to press —
    // so it is held here for something that can render the choice.
    case 'ask':
      useStore.setState({ question: {
        kind: m.kind || 'scope', file: m.file || '', route: m.route || '',
        routes: m.routes || [], options: m.options || [],
      } })
      break

    case 'detected':     s.addLog('INFO', `type: ${m.site_type} · ${m.strategy}`); break
    case 'chat_intent':  s.addLog('INFO', `${m.intent || 'ask'} — ${m.summary || ''}`); break
    case 'agent_msg':    s.addLog('INFO', m.text); break
    case 'memory':       break
    case 'mongo':        break
    case 'command':      break
    case 'demo_accounts': break
    case 'feature_plan': break
    case 'element_picked':
      s.addLog('INFO', `   ${m.file}${m.line ? ':' + m.line : ''}`)
      if (m.file) s.setActiveFile(m.file)
      break
    case 'undo_point':
      s.setUndo({ id: m.id, files: m.files || [] })
      s.addLog('INFO', `undo point saved (${(m.files || []).join(', ')})`)
      break
    default: break
  }
}

export { KEYS }

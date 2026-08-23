
import { useStore, KEYS } from './store'
import { API, HTTP_FALLBACK } from './api'


const STEP_ALIAS = { plan: 'build', generate: 'build' }

// What each outgoing message is, in the terms the overlay presents.
const WORK_KIND = {
  agent_build: 'build',
  agent_update: 'repair', agent_resume: 'build',
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
let e2eVersion = 0

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


/** Answer a forge plan review: approve it, send it back, or stop the run. */
export function decidePlan(verdict, note = '') {
  const { project, planReview } = useStore.getState()
  if (!planReview) return false
  useStore.getState().setPlanReview(null)
  send({ type: 'plan_decision', project, verdict, note })
  useStore.getState().addLog('INFO',
    verdict === 'approve' ? 'plan approved — building'
    : verdict === 'revise' ? `plan sent back — ${note}`
    : 'plan rejected — nothing will be written')
  return true
}


/** Resend the last edit with an answer or instruction. */
export function answerQuestion(prompt) {
  const base = lastEdit
  useStore.setState({ question: null })
  if (!base) return false
  useStore.getState().addLog('INFO', `Edit: ${prompt}`)
  useStore.getState().setBusy(true)
  send({ ...base, prompt })
  return true
}

function wsUrl() {
  const host = (typeof location !== 'undefined' && location.hostname) || 'localhost'
  return `ws://${host}:7825`
}

export function connect() {
  if (typeof window === 'undefined') return
  const s = useStore.getState()

  // Close whatever is already open FIRST.
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
  // Every handler checks it is still the current socket before it speaks.
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

    case 'test_start':
      e2eVersion += 1
      s.setE2eLive(null)
      s.testStart()
      break
    case 'test_run':     s.testRun(m.attempt); break
    case 'test_result':  s.testResult(m); break
    case 'test_fixing':  s.testFixing(m); break
    case 'e2e_event': {
      const prior = m.state === 'journey_start' ? {} : (s.e2eLive || {})
      const at = Date.now()
      s.setE2eLive({ ...prior, ...m,
        frame: m.state === 'journey_start' ? '' : (m.frame ?? prior.frame ?? ''), at })
      if (m.state === 'journey_done') {
        const version = ++e2eVersion
        setTimeout(() => {
          const current = useStore.getState().e2eLive
          if (version === e2eVersion && current?.at === at) {
            useStore.getState().setE2eLive(null)
          }
        }, 1500)
      }
      break
    }

  // Close every stream when the run ends.
    case 'done':
      s.setBusy(false)
      s.setWorkKind('')
      s.testDone()
      resetStreamQueue()
      useStore.setState({ liveFile: null, liveBuf: '', e2eLive: null })
  // Invalidate the cached QA report after project changes.
      s.setQaReport(null)
      if (m.project) useStore.setState({ project: m.project })
      s.bumpProjects()
      break
    // Cancelled is not an error and must not read like one.
    case 'cancelled':
      s.setBusy(false)
      s.setWorkKind('')
      s.testDone()
      resetStreamQueue()
      useStore.setState({ liveFile: null, liveBuf: '', e2eLive: null, project: '' })
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
      useStore.setState({ liveFile: null, liveBuf: '', e2eLive: null })
      s.addLog('ERROR', m.text || 'failed')
      break

    // Question that paused the run.
    case 'ask':
      useStore.setState({ question: {
        kind: m.kind || 'scope', file: m.file || '', route: m.route || '',
        routes: m.routes || [], options: m.options || [],
      } })
      break

    // A forge run has a plan and will not write anything until it is answered.
    case 'plan_review':
      s.setPlanReview(m.plan || null)
      s.addLog('INFO', `plan ready — ${(m.plan?.files || []).length} files, `
                     + `${(m.plan?.steps || []).length} steps`)
      break

    case 'forge_result':
      s.setPlanReview(null)
      s.addLog('INFO', m.result?.summary || 'forge run finished')
      break

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

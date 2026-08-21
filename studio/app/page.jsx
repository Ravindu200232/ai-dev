'use client'

import { useEffect, useState } from 'react'
import { Eye, Code2, FileText, FlaskConical, Plus, Rocket } from 'lucide-react'
import { useStore } from '@/lib/store'
import { answerQuestion, connect, send } from '@/lib/ws'
import { consoleReport, forgetConsole } from '@/lib/console-log'
import { api } from '@/lib/api'
import { catalogue } from '@/lib/models'
import { readFolder } from '@/lib/importer'
import Sidebar from '@/components/Sidebar'
import Home from '@/components/Home'
import PreviewPane from '@/components/PreviewPane'
import CodePane from '@/components/CodePane'
import SettingsModal from '@/components/SettingsModal'
import TunePrompt from '@/components/TunePrompt'
import TestingResult from '@/components/testing/TestingResult'
import SrsResult from '@/components/srs/SrsResult'
import DeployPanel from '@/components/deploy/DeployPanel'
import EditAttach from '@/components/EditAttach'
import { useEditAttachments } from '@/lib/use-edit-attachments'
import { Badge, Button, Input, Modal } from '@/components/ui'
import { cn } from '@/lib/utils'


const TABS = [

  { id: 'srs', label: 'SRS', Icon: FileText },
  { id: 'preview', label: 'Preview', Icon: Eye },
  { id: 'code', label: 'Code', Icon: Code2 },
  { id: 'testing', label: 'Testing', Icon: FlaskConical },

  { id: 'deploy', label: 'Deploy', Icon: Rocket },
]


async function retry(fn, times, waitMs) {
  let last
  for (let i = 0; i < times; i++) {
    try { return await fn() } catch (e) {
      last = e
      await new Promise(r => setTimeout(r, waitMs))
    }
  }
  throw last
}

/**
 * The question a picker edit stopped on, with its answers as buttons.
 *
 * It was already being said — as log lines, in a panel that truncates every
 * one of them at the width of a sidebar, with nothing in it to press. What
 * arrived on screen was a spinner reading "Waiting for you" above three cut
 * sentences, and the instruction to retype a sixty-character sentence with a
 * suffix on it.
 *
 * `answerQuestion` re-sends the edit that was asked about, so the element the
 * preview has already forgotten does not have to be found and clicked again.
 * If that message is gone — a reload since — the answer goes into the ask box
 * instead, which is where the log lines were telling them to type it anyway.
 */
function ScopeQuestion({ onType }) {
  const question = useStore(s => s.question)
  if (!question) return null

  const routes = question.routes || []
  return (
    <div className="mb-2 border-l-[3px] border-accent bg-tint p-2.5">
      <p className="text-[12px] leading-relaxed text-ink">
        <b className="font-semibold">{question.file}</b> is on {routes.length} route
        {routes.length === 1 ? '' : 's'}, not just{' '}
        {question.route || 'this page'} — which did you mean?
      </p>
      {routes.length > 0 && (
        <p className="mt-1 truncate font-mono text-[10px] text-muted"
           title={routes.join(', ')}>
          {routes.join(' · ')}
        </p>
      )}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {/* The server sends the narrow answer first and the wide one second,
            and the label comes from that position rather than from reading the
            sentence — the instruction is pasted into both and may contain
            either word itself. The full sentence is the title, so what is
            actually being sent is one hover away. */}
        {(question.options || []).map((option, i) => (
          <button key={option} title={option}
                  onClick={() => { if (!answerQuestion(option)) onType(option) }}
                  className="border border-line2 bg-panel px-2.5 py-1
                             text-left text-[11px] font-semibold text-ink
                             transition-colors hover:border-accent hover:text-accent">
            {i === 0 ? `${question.route || 'This page'} only`
                     : `All ${routes.length} routes`}
          </button>
        ))}
        <button onClick={() => useStore.setState({ question: null })}
                className="px-1.5 text-[11px] text-muted hover:text-ink">
          Leave it
        </button>
      </div>
    </div>
  )
}


export default function Studio() {
  const view = useStore(s => s.view)
  const setView = useStore(s => s.setView)
  const project = useStore(s => s.project)
  const busy = useStore(s => s.busy)
  const askOpen = useStore(s => s.askOpen)
  const setAskOpen = useStore(s => s.setAskOpen)
  const tests = useStore(s => s.tests)
  const liveFile = useStore(s => s.liveFile)

  const [projects, setProjects] = useState([])
  const [cat, setCat] = useState(() => catalogue(null))
  const [screen, setScreen] = useState('home')
  const [ask, setAsk] = useState('')
  const [reading, setReading] = useState(false)
  const attach = useEditAttachments()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [pendingAsk, setPendingAsk] = useState(null)

  const refreshProjects = () => api.projects()
    .then(r => setProjects(Array.isArray(r) ? r : (r.projects || [])))
    .catch(() => { })

  // The socket cannot call refreshProjects — it lives out here with the state
  // it fills — so it bumps a number instead and this watches it. A build makes
  // a project on its first few seconds and a cancel takes one away, and the
  // sidebar should show both without anybody reloading the page.
  const projectsStamp = useStore(s => s.projectsStamp)
  useEffect(() => {
    if (projectsStamp) refreshProjects()
  }, [projectsStamp])

  useEffect(() => {

    useStore.getState().hydrate()
    connect()
    refreshProjects()
    api.models().then(r => {
      const c = catalogue(r)
      setCat(c)

      const cur = useStore.getState().models
      if (cur.agent) return
      // No model chosen in this browser yet. The backend keeps a saved
      // default (Settings → agent_model) and falls back to it when a build
      // arrives with no model — so the picker should show THAT, not whatever
      // Ollama happens to list first. Listing first is not a recommendation:
      // it put the dearest cloud model in the box on every fresh browser.
      const known = new Set([...c.cloud, ...(c.local || [])].map(m => m.id))
      api.settings().then(s => {
        const saved = String(s?.agent_model || '').trim()
        const pick = (saved && known.has(saved)) ? saved : (c.cloud[0]?.id || '')
        const now = useStore.getState().models
        if (!now.agent && pick) useStore.setState({ models: { ...now, agent: pick } })
      }).catch(() => {
        const now = useStore.getState().models
        if (!now.agent && c.cloud[0]) useStore.setState({ models: { ...now, agent: c.cloud[0].id } })
      })
    }).catch(() => { })
  }, [])

  // A run brings the workspace up. It does NOT choose a tab.
  //
  // This used to switch to Code on the first file of every run — every build,
  // every edit, every repair — so whatever you were reading was taken away
  // from you the moment the model started writing, and the only way back was
  // to click the tab you were already on. It is the studio deciding it knows
  // better than the person watching, several times an hour.
  //
  // There is less than nothing to gain from it now: the preview holds the
  // build overlay with the log feed and the file being written already, so
  // Preview during a run shows the same progress Code would, over the pane
  // that was asked for. Code is one click away for anyone who wants it.
  useEffect(() => {
    if (liveFile) setScreen('workspace')
  }, [liveFile])

  async function openProject(name) {
    const st = useStore.getState()
    st.reset(name)
    useStore.setState({ project: name })
    setScreen('workspace')

    // Busy from the click, not from the first file.
    //
    // `api.open` returns as soon as the server has STARTED the thread that
    // installs dependencies and boots the dev server; the app is not on screen
    // for another few seconds to a minute. Until now nothing said so, and the
    // preview went on showing the project that had just been closed — the last
    // one's pages, under this one's name, live enough to click around in.
    //
    // `_open_project` ends with `edone`, which is what clears this. The
    // overlay covers the frame until then.
    st.setBusy(true)
    st.setOpening(true)
    st.setProgress(`Opening ${name}…`, 0)
    st.addLog('INFO', `Opening ${name}`)

    // The previous project's console errors are not this one's evidence.
    forgetConsole()

    try {
      await api.open(name)

      const raw = await retry(() => api.files(name), 4, 700)

      const out = {}
      for (const [path, v] of Object.entries(raw || {})) {
        out[path] = typeof v === 'string' ? v : (v?.content ?? '')
      }
      useStore.getState().setFiles(out)
    } catch (e) {
      useStore.getState().addLog('WARN', `could not open ${name}: ${e.message}`)
      useStore.getState().setBusy(false)
    }
  }

  function resumeBuild() {
    const st = useStore.getState()
    const name = st.project
    if (!name || st.busy) return
    st.setBusy(true)
    st.setProgress('Resuming…', 0)
    st.addLog('INFO', `Resuming ${name} — picking up where it stopped`)
    setScreen('workspace')
    send({ type: 'agent_resume', project: name,
           model: st.models.agent, think: st.think,
           qa_model: st.models.qa })
  }

  async function importFolder(list) {
    const s = useStore.getState()
    setScreen('workspace')
    s.reset(null)
    s.setBusy(true)
    s.setStatus('busy', 'importing…')
    try {
      const { name, title, files, skipped } = await readFolder(list, (done, total) => {
        if (done % 25 === 0 || done === total) {
          s.setProgress(`Reading ${done}/${total}…`, Math.round(done / total * 60))
        }
      })
      s.addLog('INFO', `${Object.keys(files).length} file(s) to import`
                     + (skipped ? `, ${skipped} skipped` : ''))
      const r = await api.uploadProject({ name, title, files })
      const imported = r.project || r.name || name
      s.addLog('SUCCESS', `Imported ${imported}`)
      s.setBusy(false)
      s.setStatus('live', 'ready')
      await refreshProjects()
      await openProject(imported)
    } catch (e) {
      s.addLog('ERROR', 'Import failed: ' + e.message)
      s.setBusy(false)
      s.setStatus('disconnected', 'import failed')
    }
  }

  async function downloadZip() {
    const st = useStore.getState()
    if (!st.project) return
    try {
      const { default: JSZip } = await import('jszip')
      const zip = new JSZip()
      for (const [path, body] of Object.entries(st.files)) zip.file(path, body)
      const blob = await zip.generateAsync({ type: 'blob' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = st.project + '.zip'
      a.click()
      URL.revokeObjectURL(url)
      st.addLog('SUCCESS', `Downloaded ${st.project}.zip`)
    } catch (e) {
      st.addLog('WARN', 'Could not build the zip: ' + e.message)
    }
  }

  function fireAsk(payload, text, shown, hadConsole) {
    const st = useStore.getState()
    send({ ...payload, prompt: text })
    if (hadConsole) st.addLog('INFO', 'Sent with what the browser had already logged')
    forgetConsole()
    st.addLog('INFO', 'Update: ' + shown)
    st.setBusy(true)
    attach.reset()
    setAsk('')
    setPendingAsk(null)
  }

  async function sendAsk() {
    const v = ask.trim()
    if (!v || !project) return
    const st = useStore.getState()

    let full = v
    if (attach.items.length) {
      setReading(true)
      try {
        full = v + await attach.collect(project)
      } catch (e) {
        st.addLog('WARN', `${e.message}`)
      }
      setReading(false)
    }

    const payload = {
      type: 'agent_update', project, route: st.previewRoute || '',
      model: st.models.agent || st.models.build, think: st.think,
      qa_model: st.models.qa || '', console: consoleReport(),
    }

    let tuned = full
    try {
      const r = await api.tune({ prompt: full, project,
                                 route: payload.route, model: payload.model })
      tuned = (r?.prompt || '').trim() || full
    } catch (e) {
      st.addLog('WARN', `Could not reword the request (${e.message}) — sending it as typed`)
    }

    // Feature and bug updates always wait for explicit approval, even when
    // the tuner decides the user's wording is already precise.
    setPendingAsk({ payload, shown: v, typed: full, tuned })
  }
  return (
    <div className="flex h-full bg-[radial-gradient(circle_at_20%_0%,#f8faff_0%,#edf1f7_42%,#e7ebf3_100%)] p-2.5 dark:bg-[radial-gradient(circle_at_20%_0%,#1a2030_0%,#111722_42%,#0c1119_100%)]">
      <Sidebar models={cat} projects={projects} onOpen={openProject}
               onImport={importFolder} onSettings={() => setSettingsOpen(true)}
               onZip={downloadZip} onResume={resumeBuild}
               onDeleted={(name) => {
                 refreshProjects()
                 if (project === name) setScreen('home')
               }} />

      {settingsOpen && (
        <SettingsModal onClose={() => setSettingsOpen(false)}
                       onSaved={() => api.models().then(r => setCat(catalogue(r)))
                                        .catch(() => { })} />
      )}

      {askOpen && (
        <Modal onClose={() => setAskOpen(false)} className="max-w-[640px]">
          <h2 className="text-[15px] font-semibold text-ink">Ask about this app</h2>
          <p className="mt-1 text-[11.5px] text-muted">
            Describe a change, report a bug, or ask a question. What the
            browser has already logged is sent with it.
          </p>

          <textarea value={ask} autoFocus rows={5}
                    placeholder={project
                      ? 'e.g. the booking button on /rooms does nothing'
                      : 'Open a project first'}
                    disabled={!project || busy || reading}
                    onChange={e => setAsk(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                        setAskOpen(false)
                        sendAsk()
                      }
                    }}
                    className="mt-4 w-full resize-y rounded-xl border border-line bg-white/65 px-3 py-2.5 text-[12.5px] leading-relaxed shadow-sm outline-none focus:border-accent disabled:opacity-45 dark:bg-white/5" />

          {project && <EditAttach attach={attach} disabled={busy || reading} className="mt-2" />}

          <footer className="mt-5 flex items-center gap-2 border-t border-line/70 pt-4">
            <span className="flex-1 text-[10.5px] text-muted2">⌘↵ / Ctrl↵ to send</span>
            <Button variant="outline" onClick={() => setAskOpen(false)}>Cancel</Button>
            <Button variant="solid" disabled={!project || busy || reading || !ask.trim()}
                    onClick={() => { setAskOpen(false); sendAsk() }}>
              Send
            </Button>
          </footer>
        </Modal>
      )}

      {pendingAsk && (
        <TunePrompt
          typed={pendingAsk.shown}
          tuned={pendingAsk.tuned}
          onSend={(text) => fireAsk(pendingAsk.payload, text, pendingAsk.shown, Boolean(pendingAsk.payload.console))}
          onSendTyped={() => fireAsk(pendingAsk.payload, pendingAsk.typed, pendingAsk.shown, Boolean(pendingAsk.payload.console))}
          onCancel={() => setPendingAsk(null)}
          onRetune={async (text) => {
            const r = await api.tune({ prompt: text, project,
                                       route: pendingAsk.payload.route,
                                       model: pendingAsk.payload.model })
            return (r?.prompt || '').trim()
          }} />
      )}

      <div className="ml-2.5 flex min-w-0 flex-1 flex-col overflow-hidden rounded-[30px] bg-panel/92 shadow-[0_28px_75px_rgba(30,41,59,.13)] ring-1 ring-white/75 backdrop-blur-2xl dark:shadow-[0_28px_75px_rgba(0,0,0,.42)] dark:ring-white/[.055]">
        {/* macOS-style workspace tabs. */}
        <div className="flex h-[60px] shrink-0 items-center gap-1.5 border-b border-line/55 bg-white/48 px-4 backdrop-blur-2xl dark:bg-white/[.02]">
          {screen === 'home' && (
            <span className="flex items-center rounded-full bg-panel2 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[.16em] text-label">
              New project
            </span>
          )}
          {screen === 'workspace' && TABS.map(({ id, label, Icon }) => (
            <button key={id} onClick={() => setView(id)}
                    className={cn('inline-flex h-9 items-center gap-[7px] rounded-full px-3.5',
                      'font-display text-[11px] font-semibold transition-all',
                      view === id ? 'bg-white/90 text-ink shadow-[0_5px_16px_rgba(30,41,59,.08)] ring-1 ring-black/[.04] dark:bg-white/10 dark:ring-white/[.06]'
                                  : 'text-muted hover:bg-white/55 hover:text-ink dark:hover:bg-white/5')}>
              <Icon className="size-[13px] shrink-0" />
              {label}
              {id === 'testing' && tests.fail > 0 && (
                <Badge tone={view === id ? 'ok' : 'bad'}>{tests.fail}</Badge>
              )}
            </button>
          ))}
          <span className="flex-1" />
          {screen === 'home' && !busy && (
            <span className="flex items-center px-5 font-mono text-[10.5px] text-muted2">
              ⌘↵ to build
            </span>
          )}
          {busy && (
            <span className="flex items-center gap-2 rounded-full bg-accent/10 px-3 py-1.5 text-[11px] font-medium text-accent">
              <span className="size-1.5 animate-pulse bg-accent" />
              working
            </span>
          )}
          {screen === 'workspace' && (
            <button onClick={() => {

                      useStore.getState().resetSrs()
                      setScreen('home')
                    }}
                    className="inline-flex h-9 items-center gap-2 rounded-full bg-white/72 px-3.5 text-[11px] font-semibold text-ink shadow-sm ring-1 ring-line/70 transition-all hover:bg-white dark:bg-white/5 dark:hover:bg-white/10">
              <Plus className="size-[13px]" /> New
            </button>
          )}
        </div>

        {screen === 'home' ? (
          <Home onStarted={() => setScreen('workspace')} />
        ) : (
          <div className="flex min-h-0 flex-1 bg-bg/40">
            <div className="relative flex min-w-0 flex-1 flex-col">
              {/* The ask box used to sit here, above the preview, on every
                  tab — including Code and Testing, where there is nothing to
                  ask about. It lives in the preview's own drawer now, beside
                  the console and the terminal, and opens as a composer. */}
              <ScopeQuestion onType={setAsk} />

              {/* Keyed on the project, every one of them.

                  These panels each hold a project's worth of state in local
                  `useState` — the deploy payload and its monitor snapshot, the
                  SRS document, which sub-tab is open, the preview's own
                  history trail — and none of it is reachable from `reset()`.
                  Switching projects while one was open left the panel showing
                  the project that had just been closed: another app's
                  deployment being monitored under this app's name. A key is
                  what says "this is a different one now", and React throws the
                  whole component away and builds it again. */}
              <PreviewPane key={`preview-${project}`} hidden={view !== 'preview'} />
              <CodePane hidden={view !== 'code'} />
              {view === 'testing' && <TestingResult key={`testing-${project}`} />}
              {view === 'srs' && <SrsResult key={`srs-${project}`} />}
              {view === 'deploy' && (
                <DeployPanel key={`deploy-${project}`}
                             onSettings={() => setSettingsOpen(true)} />
              )}
</div>
          </div>
        )}
      </div>
    </div>
  )
}

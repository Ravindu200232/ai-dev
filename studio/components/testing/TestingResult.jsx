'use client'

import { useEffect, useMemo, useState } from 'react'
import { Download, Loader2, RefreshCw, Activity, Layers3 } from 'lucide-react'
import { api } from '@/lib/api'
import { useStore } from '@/lib/store'
import { Badge, Button, Empty, SubTab, SubTabs } from '../ui'
import { cn } from '@/lib/utils'
import { deriveVitestCounts } from '@/lib/test-counts'
import Overview from './Overview'
import UnitTests from './UnitTests'
import Timeline from './Timeline'
import EndToEnd from './EndToEnd'
import Routes from './Routes'
import BugReports from './BugReports'
import Security from './Security'
import Performance from './Performance'
import Coder from './Coder'

const VIEWS = [
  { id: 'overview', label: 'Overview', C: Overview },
  { id: 'unit', label: 'Unit tests', C: UnitTests },
  { id: 'timeline', label: 'Timeline', C: Timeline },
  { id: 'e2e', label: 'Browser flows', C: EndToEnd },
  { id: 'routes', label: 'API contracts', C: Routes },
  { id: 'bugs', label: 'Bugs', C: BugReports },
  { id: 'security', label: 'Security', C: Security },
  { id: 'perf', label: 'Performance', C: Performance },
  { id: 'coder', label: 'Test code', C: Coder },
]

export default function TestingResult() {
  const project = useStore(s => s.project)
  const live = useStore(s => s.tests)
  const qa = useStore(s => s.qaReport)
  const setQa = useStore(s => s.setQaReport)
  const addLog = useStore(s => s.addLog)
  const [sub, setSub] = useState('overview')
  const [state, setState] = useState('idle')
  const [error, setError] = useState('')
  const [pdf, setPdf] = useState(false)

  async function downloadPdf() {
    setPdf(true)
    try {
      const r = await fetch(api.qaPdfUrl(project))
      if (!r.ok) {
        let why = `HTTP ${r.status}`
        try { why = (await r.json()).error || why } catch { }
        throw new Error(why)
      }
      const url = URL.createObjectURL(await r.blob())
      const a = document.createElement('a')
      a.href = url
      a.download = `${project}-test-report.pdf`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      addLog('INFO', 'Test report downloaded')
    } catch (e) {
      addLog('WARN', `The test report could not be built — ${e.message}`)
    }
    setPdf(false)
  }

  // A load retries four times with a pause between, so one can still be in
  // flight when the project changes — and because this panel is keyed on the
  // project it has been thrown away and rebuilt by then, leaving the old
  // request to finish and write into the shared store anyway. Measured:
  // opening one project showed the QA of the one before it, so a run with a
  // full green suite read as "Nothing has been recorded for this project yet".
  //
  // The guard is the store rather than anything owned by this instance,
  // because the instance that started the request no longer exists.
  async function load() {
    if (!project) return
    const want = project
    const stale = () => useStore.getState().project !== want
    setState('loading')
    let last
    for (let i = 0; i < 4; i++) {
      try {
        const data = await api.qa(want)
        if (stale()) return
        setQa(data)
        setState('ready')
        return
      } catch (e) {
        if (stale()) return
        last = e
        await new Promise(r => setTimeout(r, 700))
      }
    }
    if (stale()) return
    setError(last?.message || 'unknown error')
    setState('error')
  }

  useEffect(() => { load() }, [project])
  // `project` belongs in these deps: without it this effect keeps the closure
  // from whichever render last flipped `live.running`, and reloads the QA of
  // whatever project was open then.
  useEffect(() => { if (!live.running && project) load() }, [live.running, project])

  const counts = useMemo(() => badges(qa), [qa])
  const suite = deriveVitestCounts(qa?.vitest)
  const View = (VIEWS.find(v => v.id === sub) || VIEWS[0]).C

  if (!project) return <Empty>Open a project to see its test results.</Empty>

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-[linear-gradient(180deg,rgba(255,255,255,.22),transparent_22%)]">
      <div className="flex shrink-0 items-center gap-3 border-b border-line/70 bg-white/45 px-4 py-3 backdrop-blur-xl dark:bg-white/[.02]">
        <div className="grid size-9 place-items-center rounded-xl bg-accent/10 text-accent">
          <Layers3 className="size-4" />
        </div>
        <div>
          <h2 className="text-[14px] font-semibold text-ink">Quality workspace</h2>
          <p className="text-[11px] text-muted">
            The complete stored suite stays visible while focused tests run for a feature or repair.
          </p>
        </div>
        <span className="flex-1" />
        {qa?.vitest && (
          <div className="rounded-full border border-line bg-white/70 px-3 py-1.5 text-[11px] shadow-sm dark:bg-white/5">
            <b className={suite.failed ? 'text-bad' : 'text-ok'}>{suite.passed}</b>
            <span className="text-muted"> / {suite.total} passing</span>
          </div>
        )}
        <Button variant="outline" disabled={!project || state !== 'ready' || pdf} onClick={downloadPdf}>
          {pdf ? <Loader2 className="size-3 animate-spin" /> : <Download className="size-3" />} PDF
        </Button>
        <Button variant="outline" onClick={load}><RefreshCw className="size-3" /> Refresh</Button>
      </div>

      {live.running && (
        <LiveRun live={live} suite={suite} />
      )}

      <SubTabs className="px-2 pt-2">
        {VIEWS.map(v => (
          <SubTab key={v.id} on={sub === v.id} onClick={() => setSub(v.id)}>
            {v.label}
            {counts[v.id] != null && (
              <Badge tone={counts[v.id].bad ? 'bad' : 'ok'}>{counts[v.id].n}</Badge>
            )}
          </SubTab>
        ))}
      </SubTabs>

      <div className="min-h-0 flex-1 overflow-y-auto p-5">
        {state === 'loading' && !qa && <Empty>Reading the results…</Empty>}
        {state === 'error' && !qa && <Empty bad>Could not read them — {error}</Empty>}
        <View qa={qa} live={live} />
      </div>
    </div>
  )
}

function LiveRun({ live, suite }) {
  const current = live.rows.slice(-5)
  return (
    <div className="mx-4 mt-4 rounded-[20px] border border-accent/20 bg-accent/[.06] p-3.5 shadow-sm">
      <div className="flex items-center gap-3">
        <span className="grid size-8 place-items-center rounded-xl bg-accent text-white shadow-sm">
          <Activity className="size-4 animate-pulse" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[12px] font-semibold text-ink">Focused verification is running</p>
          <p className="text-[11px] text-muted">
            Previous {suite.total || 0} test cases remain below. This run only updates the files touched by the latest change.
          </p>
        </div>
        <span className="rounded-full bg-white/70 px-2.5 py-1 font-mono text-[10px] text-muted shadow-sm dark:bg-white/5">
          try {live.attempt || 1}
        </span>
      </div>
      {current.length > 0 && (
        <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {current.map((r, i) => (
            <div key={`${r.at}-${i}`} className={cn('rounded-xl border px-3 py-2 text-[11px]',
              r.status === 'fail' ? 'border-bad/25 bg-bad/[.06] text-bad' :
              r.status === 'pass' ? 'border-ok/25 bg-ok/[.06] text-ok' :
              'border-line bg-white/55 text-muted dark:bg-white/[.03]')}>
              {r.msg || 'running check'}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function badges(qa) {
  const out = {}
  const r = qa?.report
  const c = deriveVitestCounts(qa?.vitest)
  if (qa?.vitest) out.unit = { n: `${c.passed}/${c.total}`, bad: c.failed > 0 || c.skipped > 0 }
  if (r) {
    const bugs = r.suite?.unresolved?.length || 0
    out.bugs = { n: bugs, bad: bugs > 0 }
    const e2e = r.e2e || {}
    const failed = Number(e2e.failed || 0)
    if (e2e.ran || e2e.flows?.length) out.e2e = { n: failed ? `${failed} fail` : 'pass', bad: failed > 0 }
  }
  if (r?.security) {
    const sec = r.security.findings?.length || 0
    out.security = { n: sec, bad: sec > 0 }
  }
  if (qa?.history?.length) out.timeline = { n: qa.history.length, bad: false }
  return out
}

export const Summary = ({ children }) => (
  <div className="mb-5 flex flex-wrap items-stretch gap-2">{children}</div>
)

export function Stat({ n, label, tone }) {
  return (
    <span className="min-w-[110px] rounded-2xl border border-line/80 bg-white/65 px-4 py-3 shadow-sm dark:bg-white/[.03]">
      <b className={cn('block text-[24px] font-semibold leading-none tracking-[-.02em]', tone || 'text-ink')}>{n ?? '—'}</b>
      <span className="mt-1 block text-[10.5px] text-muted">{label}</span>
    </span>
  )
}

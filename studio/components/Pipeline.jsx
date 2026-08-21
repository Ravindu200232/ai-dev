'use client'

import { useEffect, useMemo, useRef } from 'react'
import { ArrowRight, BrainCircuit, Bug, FileCode2, FlaskConical, Orbit, Sparkles, Check, Loader2 } from 'lucide-react'
import { useStore } from '@/lib/store'
import { humanActivity, activityAgent } from '@/lib/activity'
import { cn } from '@/lib/utils'

const AGENTS = [
  { id: 'analyst', label: 'Analyze', Icon: BrainCircuit },
  { id: 'builder', label: 'Build', Icon: FileCode2 },
  { id: 'qa', label: 'Test', Icon: FlaskConical },
  { id: 'fixer', label: 'Repair', Icon: Bug },
  { id: 'orchestrator', label: 'Ready', Icon: Sparkles },
]

export default function Pipeline() {
  const logs = useStore(s => s.logs)
  const files = useStore(s => s.files)
  const tests = useStore(s => s.tests)
  const phases = useStore(s => s.phases)
  const busy = useStore(s => s.busy)
  const activeFile = useStore(s => s.activeFile)
  const setActiveFile = useStore(s => s.setActiveFile)
  const setView = useStore(s => s.setView)
  const tail = useRef(null)

  const activity = useMemo(() => {
    const seen = new Set()
    const rows = []
    for (const l of logs.slice(-180).reverse()) {
      const text = humanActivity(l.text)
      if (!text || seen.has(text)) continue
      seen.add(text)
      rows.push({ text, level: l.level, at: l.at, agent: activityAgent(l.text) })
      if (rows.length >= 9) break
    }
    return rows.reverse()
  }, [logs])

  useEffect(() => { tail.current?.scrollIntoView({ block: 'end' }) }, [activity.length])

  const currentAgent = activity.length ? activity[activity.length - 1].agent : busy ? 'analyst' : 'orchestrator'
  const names = Object.keys(files).sort()
  const activePhases = phases.filter(p => p.status === 'active')

  return (
    <aside className="flex w-[292px] shrink-0 flex-col overflow-hidden border-r border-line/70 bg-white/36 backdrop-blur-xl dark:bg-white/[.018]">
      <div className="border-b border-line/70 px-4 py-3.5">
        <div className="flex items-center gap-2">
          <span className="grid size-8 place-items-center rounded-xl bg-accent/10 text-accent"><Orbit className="size-4" /></span>
          <div className="min-w-0">
            <p className="text-[12px] font-semibold text-ink">Agent activity</p>
            <p className="text-[10.5px] text-muted">A readable view of what the system is doing</p>
          </div>
        </div>
      </div>

      <div className="border-b border-line/70 p-3">
        <div className="grid grid-cols-5 gap-1.5">
          {AGENTS.map(({ id, label, Icon }, i) => {
            const active = busy && currentAgent === id
            const passed = !busy || AGENTS.findIndex(a => a.id === currentAgent) > i
            return (
              <div key={id} className="relative flex min-w-0 flex-col items-center gap-1.5">
                {i < AGENTS.length - 1 && <span className="absolute left-[62%] top-4 h-px w-[76%] bg-line" />}
                <span className={cn('relative z-10 grid size-8 place-items-center rounded-xl border transition-all',
                  active ? 'border-accent bg-accent text-white shadow-[0_6px_16px_rgba(93,106,251,.28)]' :
                  passed ? 'border-ok/20 bg-ok/10 text-ok' : 'border-line bg-panel text-muted2')}>
                  {active ? <Loader2 className="size-3.5 animate-spin" /> : passed ? <Check className="size-3.5" /> : <Icon className="size-3.5" />}
                </span>
                <span className={cn('truncate text-[9px] font-medium', active ? 'text-accent' : 'text-muted2')}>{label}</span>
              </div>
            )
          })}
        </div>
      </div>

      {activePhases.length > 0 && (
        <div className="border-b border-line/70 px-3 py-2.5">
          <p className="mb-2 text-[9.5px] font-semibold uppercase tracking-[.16em] text-label">Now</p>
          {activePhases.slice(-2).map((p, i) => (
            <div key={`${p.phase}-${i}`} className="mb-1.5 rounded-xl bg-accent/[.055] px-2.5 py-2 text-[11px] text-ink last:mb-0">
              {humanActivity(p.title) || p.title}
            </div>
          ))}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        <p className="mb-2 text-[9.5px] font-semibold uppercase tracking-[.16em] text-label">Activity</p>
        <div className="space-y-2">
          {activity.map((row, i) => (
            <div key={`${row.at}-${i}`} className="lc-rise flex gap-2.5 rounded-xl border border-line/70 bg-white/55 px-2.5 py-2 shadow-sm dark:bg-white/[.025]">
              <span className={cn('mt-1 size-1.5 shrink-0 rounded-full', row.level === 'ERROR' ? 'bg-bad' : row.level === 'WARN' ? 'bg-warn' : row.level === 'SUCCESS' ? 'bg-ok' : 'bg-accent')} />
              <p className="text-[10.8px] leading-[1.45] text-muted">{row.text}</p>
            </div>
          ))}
          {!activity.length && <p className="rounded-xl border border-dashed border-line px-3 py-4 text-[10.5px] text-muted2">Activity will appear here when a run starts.</p>}
          <div ref={tail} />
        </div>
      </div>

      {(tests.running || tests.rows.length > 0) && (
        <div className="border-t border-line/70 p-3">
          <button onClick={() => setView('testing')} className="w-full rounded-2xl border border-line/80 bg-white/65 p-3 text-left shadow-sm transition hover:bg-white dark:bg-white/[.03] dark:hover:bg-white/[.06]">
            <div className="flex items-center gap-2">
              <FlaskConical className={cn('size-4', tests.running ? 'text-accent' : 'text-muted')} />
              <span className="text-[11.5px] font-semibold text-ink">Testing</span>
              <span className="flex-1" />
              <span className="font-mono text-[10px] text-muted">{tests.pass} pass · {tests.fail} fail</span>
            </div>
            <div className="mt-2 flex gap-1">
              {[0,1,2,3].map(i => <span key={i} className={cn('h-1.5 flex-1 rounded-full', tests.running && i <= Math.min(3, tests.attempt || 0) ? 'bg-accent animate-pulse' : 'bg-panel2')} />)}
            </div>
            <p className="mt-2 flex items-center gap-1.5 text-[10.5px] text-muted"><ArrowRight className="size-3" /> Open complete results</p>
          </button>
        </div>
      )}

      <div className="border-t border-line/70 p-3">
        <div className="mb-2 flex items-center"><p className="text-[9.5px] font-semibold uppercase tracking-[.16em] text-label">Project files</p><span className="flex-1" /><span className="font-mono text-[9.5px] text-muted2">{names.length}</span></div>
        <div className="max-h-[132px] overflow-y-auto rounded-xl border border-line/70 bg-white/45 p-1 dark:bg-white/[.02]">
          {names.slice(-80).map(n => (
            <button key={n} onClick={() => { setActiveFile(n); setView('code') }} className={cn('block w-full truncate rounded-lg px-2 py-1 text-left font-mono text-[9.8px] transition', activeFile === n ? 'bg-accent/10 text-accent' : 'text-muted hover:bg-white/70 hover:text-ink dark:hover:bg-white/5')}>
              {n}
            </button>
          ))}
          {!names.length && <p className="px-2 py-2 text-[10px] text-muted2">No files yet.</p>}
        </div>
      </div>
    </aside>
  )
}

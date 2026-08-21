'use client'

import { useMemo, useRef, useEffect, useState } from 'react'
import {
  Activity, ChevronUp, CircleCheck, CircleAlert, MessageSquarePlus, Sparkles,
  SquareTerminal,
  Wrench, FlaskConical, FileCode2, Search,
} from 'lucide-react'
import { useStore } from '@/lib/store'
import { activityEvent } from '@/lib/activity'
import { cn } from '@/lib/utils'

const ICONS = {
  plan: Search,
  build: FileCode2,
  test: FlaskConical,
  fix: Wrench,
  verify: CircleCheck,
  done: Sparkles,
  warn: CircleAlert,
  setup: Sparkles,
}

export default function PreviewConsoleDrawer() {
  const logs = useStore(s => s.logs)
  const busy = useStore(s => s.busy)
  const project = useStore(s => s.project)
  const setAskOpen = useStore(s => s.setAskOpen)
  const [open, setOpen] = useState(null)
  const end = useRef(null)

  const friendly = useMemo(() => {
    const rows = []
    const seen = new Set()
    for (const row of logs.slice(-220)) {
      const event = activityEvent(row.text, row.level)
      if (!event) continue
      const key = `${event.kind}:${event.title}`
      if (seen.has(key)) continue
      seen.add(key)
      rows.push({ ...event, at: row.at, level: row.level })
    }
    return rows.slice(-18)
  }, [logs])

  useEffect(() => {
    if (open) end.current?.scrollIntoView({ block: 'end' })
  }, [open, logs.length])

  const recent = friendly.at(-1)

  return (
    <div className="pointer-events-none absolute inset-x-4 bottom-3 z-[45]">
      <div className={cn(
        'pointer-events-auto mx-auto w-full max-w-[980px] overflow-hidden',
        'rounded-[22px] border border-white/70 bg-white/88 shadow-[0_22px_55px_rgba(15,23,42,.18)]',
        'backdrop-blur-2xl transition-all duration-300 dark:border-white/10 dark:bg-[#111824]/92',
        open ? 'h-[330px]' : 'h-[48px]')}
      >
        <div className="flex h-12 items-center gap-2 px-2.5">
          <button onClick={() => setOpen(open === 'activity' ? null : 'activity')}
                  className={cn('flex h-8 items-center gap-2 rounded-xl px-3 text-[11.5px] font-semibold transition-all',
                    open === 'activity' ? 'bg-accent text-white shadow-sm' : 'text-ink hover:bg-black/[.045] dark:hover:bg-white/[.06]')}>
            <Activity className="size-3.5" /> Activity
          </button>
          <button onClick={() => setOpen(open === 'terminal' ? null : 'terminal')}
                  className={cn('flex h-8 items-center gap-2 rounded-xl px-3 text-[11.5px] font-semibold transition-all',
                    open === 'terminal' ? 'bg-[#171c27] text-white shadow-sm dark:bg-white/12' : 'text-ink hover:bg-black/[.045] dark:hover:bg-white/[.06]')}>
            <SquareTerminal className="size-3.5" /> Terminal
          </button>

          {/* Ask sits with the other two on purpose: this bar is already the
              place you look when the preview is doing something you did not
              expect, and asking about it was a trip back up to the header. */}
          <button onClick={() => setAskOpen(true)} disabled={!project || busy}
                  title={project ? 'Describe a change, report a bug, or ask a question'
                                 : 'Open a project first'}
                  className="flex h-8 items-center gap-2 rounded-xl px-3 text-[11.5px] font-semibold text-ink transition-all hover:bg-black/[.045] disabled:pointer-events-none disabled:opacity-40 dark:hover:bg-white/[.06]">
            <MessageSquarePlus className="size-3.5" /> Ask
          </button>

          {!open && recent && (
            <div className="ml-1 min-w-0 flex-1 truncate text-[11px] text-muted">
              <span className="font-medium text-ink">{recent.title}</span>
              {busy && <span className="ml-2 text-accent">working…</span>}
            </div>
          )}
          {!open && !recent && (
            <div className="ml-1 min-w-0 flex-1 truncate text-[11px] text-muted">
              Activity appears here while AgentForge works.
            </div>
          )}

          <span className="flex-1" />
          <button onClick={() => setOpen(open ? null : 'activity')}
                  className={cn('grid size-8 place-items-center rounded-xl text-muted transition hover:bg-black/[.045] hover:text-ink dark:hover:bg-white/[.06]', open && 'rotate-180')}>
            <ChevronUp className="size-4 transition-transform" />
          </button>
        </div>

        {open === 'activity' && (
          <div className="h-[282px] overflow-y-auto border-t border-line/70 px-4 py-4">
            <div className="mx-auto max-w-[780px]">
              <div className="mb-4 flex items-end gap-3">
                <div>
                  <p className="text-[15px] font-semibold text-ink">What AgentForge has done</p>
                  <p className="mt-0.5 text-[11px] text-muted">Plain-English progress. Technical paths stay in Terminal.</p>
                </div>
                <span className="flex-1" />
                {busy && <span className="rounded-full bg-accent/10 px-2.5 py-1 text-[10px] font-semibold text-accent">Live</span>}
              </div>

              <div className="relative pl-7">
                <span className="absolute bottom-2 left-[9px] top-2 w-px bg-line2" />
                {friendly.map((row, i) => {
                  const Icon = ICONS[row.kind] || Sparkles
                  return (
                    <div key={`${row.at}-${i}`} className="relative mb-3 last:mb-0">
                      <span className={cn('absolute -left-7 top-0 grid size-[19px] place-items-center rounded-full ring-4 ring-white dark:ring-[#111824]',
                        row.kind === 'warn' ? 'bg-warn-tint text-warn' : row.kind === 'done' ? 'bg-ok-tint text-ok' : 'bg-tint text-accent')}>
                        <Icon className="size-3" />
                      </span>
                      <p className="text-[12px] font-semibold text-ink">{row.title}</p>
                      <p className="mt-0.5 text-[11px] leading-relaxed text-muted">{row.detail}</p>
                    </div>
                  )
                })}
                {!friendly.length && (
                  <p className="py-8 text-center text-[11.5px] text-muted">No build activity yet.</p>
                )}
                <div ref={end} />
              </div>
            </div>
          </div>
        )}

        {open === 'terminal' && (
          <div className="h-[282px] overflow-y-auto border-t border-white/8 bg-[#0d1119] px-4 py-3 font-mono text-[10.5px] leading-[1.65] text-[#b7c0d2]">
            {logs.slice(-260).map((row, i) => (
              <div key={`${row.at}-${i}`} className={cn(
                row.level === 'ERROR' ? 'text-[#ff8a98]' : row.level === 'WARN' ? 'text-[#efc36d]' : row.level === 'SUCCESS' ? 'text-[#75d9ac]' : '')}>
                {row.text}
              </div>
            ))}
            {!logs.length && <div className="text-[#667085]">Terminal output will appear here.</div>}
            <div ref={end} />
          </div>
        )}
      </div>
    </div>
  )
}

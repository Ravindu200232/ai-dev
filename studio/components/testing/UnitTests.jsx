'use client'

import { useMemo, useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronRight, CheckCircle2 } from 'lucide-react'
import { Empty } from '../ui'
import { Summary, Stat } from './TestingResult'
import { cn } from '@/lib/utils'
import { deriveVitestCounts, suiteRows } from '@/lib/test-counts'

export default function UnitTests({ qa, live }) {
  const [open, setOpen] = useState(() => new Set())
  const v = qa?.vitest
  if (!v) return <Empty>No unit-test report yet — the suite has not run for this project.</Empty>

  const counts = deriveVitestCounts(v)
  const suites = useMemo(() => suiteRows(v).sort((a, b) => {
    const af = a.cases.some(c => c.status === 'failed')
    const bf = b.cases.some(c => c.status === 'failed')
    return (bf - af) || a.file.localeCompare(b.file)
  }), [v])

  return (
    <div>
      <Summary>
        <Stat n={counts.passed} label="passing" tone="text-ok" />
        <Stat n={counts.failed} label="failing" tone={counts.failed ? 'text-bad' : undefined} />
        <Stat n={counts.skipped} label="not run" tone={counts.skipped ? 'text-warn' : undefined} />
        <Stat n={counts.total} label="total cases" />
        <Stat n={counts.files} label="test files" />
      </Summary>

      {live?.running && (
        <div className="mb-4 rounded-2xl border border-accent/20 bg-accent/[.05] px-3.5 py-3 text-[11.5px] text-muted">
          The latest feature or repair is testing only its affected area. The rows below are the <b className="text-ink">complete project suite</b>; targeted results are merged back into these same files when they finish.
        </div>
      )}

      {counts.skipped > 0 && (
        <p className="mb-4 flex items-center gap-2.5 rounded-2xl border border-warn/20 bg-warn/[.06] px-3.5 py-3 text-[11.5px] text-warn">
          <AlertTriangle className="size-[13px] shrink-0" />
          {counts.skipped} case(s) are skipped/pending/todo and are not counted as passing.
        </p>
      )}

      {qa?.report?.unit?.deleted ? (
        <p className="mb-4 rounded-2xl border border-bad/20 bg-bad/[.06] px-3.5 py-3 text-[11.5px] text-bad">
          {qa.report.unit.deleted} case(s) that existed when the stage started are no longer in the suite.
        </p>
      ) : null}

      <div className="space-y-2.5">
        {suites.map(s => {
          const failed = s.cases.filter(c => c.status === 'failed').length
          const passed = s.cases.filter(c => c.status === 'passed').length
          const isOpen = open.has(s.file) || failed > 0
          return (
            <section key={s.file} className="overflow-hidden rounded-[18px] border border-line/80 bg-white/58 shadow-sm dark:bg-white/[.025]">
              <button onClick={() => {
                        const next = new Set(open)
                        next.has(s.file) ? next.delete(s.file) : next.add(s.file)
                        setOpen(next)
                      }}
                      className={cn('grid w-full grid-cols-[18px_22px_1fr_auto] items-center gap-2.5 px-3.5 py-3 text-left transition-colors hover:bg-white/65 dark:hover:bg-white/[.04]',
                        failed && 'bg-bad/[.045]')}>
                {isOpen ? <ChevronDown className="size-3.5 text-muted" /> : <ChevronRight className="size-3.5 text-muted" />}
                <span className={cn('grid size-5 place-items-center rounded-full', failed ? 'bg-bad/12 text-bad' : 'bg-ok/12 text-ok')}>
                  <CheckCircle2 className="size-3" />
                </span>
                <span className="min-w-0 truncate font-mono text-[11.5px] text-ink">{s.file}</span>
                <span className={cn('rounded-full px-2.5 py-1 font-mono text-[10px]', failed ? 'bg-bad/10 text-bad' : 'bg-ok/10 text-ok')}>
                  {passed}/{s.cases.length}
                </span>
              </button>
              {isOpen && (
                <ul className="border-t border-line/70">
                  {s.cases.map((c, i) => (
                    <li key={i} className="flex flex-wrap items-center gap-3 border-b border-line/60 px-4 py-2.5 text-[12px] last:border-b-0">
                      <span className="min-w-0 flex-1 text-ink">{c.title || c.fullName}</span>
                      {c.duration != null && <span className="font-mono text-[10px] text-muted2">{Math.round(c.duration)}ms</span>}
                      <span className={cn('rounded-full px-2 py-0.5 text-[9px] font-semibold uppercase tracking-[.08em]',
                        tone(c.status))}>{label(c.status)}</span>
                      {c.status === 'failed' && (c.failureMessages || []).length > 0 && (
                        <pre className="mt-1.5 w-full overflow-x-auto whitespace-pre-wrap rounded-xl border border-bad/20 bg-bad/[.05] p-3 font-mono text-[10.5px] leading-relaxed text-bad">
                          {String(c.failureMessages[0] || '').split('\n').slice(0, 6).join('\n')}
                        </pre>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )
        })}
      </div>
    </div>
  )
}

function label(status) {
  return ({ passed: 'passed', failed: 'failed', skipped: 'not run', pending: 'not run', todo: 'todo' })[status] || status
}
function tone(status) {
  if (status === 'passed') return 'bg-ok/10 text-ok'
  if (status === 'failed') return 'bg-bad/10 text-bad'
  return 'bg-warn/10 text-warn'
}

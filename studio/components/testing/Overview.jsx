'use client'

import { Empty } from '../ui'
import { Stat } from './TestingResult'
import { cn } from '@/lib/utils'
import { deriveVitestCounts } from '@/lib/test-counts'

export default function Overview({ qa, live }) {
  const last = (qa?.history || []).slice(-1)[0]
  const r = qa?.report
  const v = qa?.vitest
  if (!last && !r && !v) return <Empty>Nothing has been recorded for this project yet.</Empty>

  const counts = deriveVitestCounts(v)
  // Where the suite ended up, not where it started. `last` is the first-pass
  // record kept in history; it is worth a footnote, not the headline — a run
  // that repaired its way to 51/51 was reading as 82%.
  const fullRate = counts.total ? Math.round((counts.passed / counts.total) * 100) : 0
  const repaired = last && counts.total ? Math.max(0, counts.passed - (last.passed || 0)) : 0
  const perf = qa?.performance?.scores || {}
  const sec = r?.security?.findings || []
  const unresolved = r?.suite?.unresolved || []

  return (
    <div>
      {v && (
        <div className="mb-5 rounded-[22px] border border-line/80 bg-white/60 p-4 shadow-sm dark:bg-white/[.025]">
          <div className="flex flex-wrap items-center gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-[10px] font-semibold uppercase tracking-[.18em] text-label">Current complete suite</p>
              <h3 className="mt-1 text-[18px] font-semibold text-ink">
                {counts.failed === 0 && counts.skipped === 0 ? 'All recorded tests are green' : 'Some checks still need attention'}
              </h3>
              <p className="mt-1 text-[11.5px] text-muted">
                Counts are recalculated from every assertion in vitest.json, not trusted from a stale summary counter.
              </p>
            </div>
            {live?.running && (
              <span className="rounded-full bg-accent/10 px-3 py-1.5 text-[10.5px] font-medium text-accent">focused run in progress</span>
            )}
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <Stat n={counts.passed} label="passing" tone="text-ok" />
            <Stat n={counts.failed} label="failing" tone={counts.failed ? 'text-bad' : undefined} />
            <Stat n={counts.skipped} label="not run" tone={counts.skipped ? 'text-warn' : undefined} />
            <Stat n={counts.total} label="total cases" />
            <Stat n={counts.files} label="files" />
          </div>
        </div>
      )}

      <div className="grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(280px,1fr))]">
        <Card title="Full pass" hint="where the suite finished, after repairs">
          {v ? (
            <>
              <div className={cn('text-[34px] font-semibold leading-none', counts.failed ? 'text-bad' : 'text-ok')}>{fullRate}%</div>
              <div className="mt-2 text-[11px] text-muted">
                {counts.passed}/{counts.total} passing
                {counts.skipped > 0 && <> · {counts.skipped} not run</>}
                {last?.floor != null && <> · quality floor {last.floor}%</>}
              </div>
              {repaired > 0 && (
                <p className="mt-3 text-[11px] text-muted2">
                  {repaired} case(s) repaired to get here — first pass was {last.rate}%
                </p>
              )}
            </>
          ) : <Empty>No suite recorded.</Empty>}
        </Card>

        <Card title="Unresolved" hint="cases the repair loop could not close">
          {r ? unresolved.length ? (
            <ul className="space-y-2 text-[11px] text-muted">
              {unresolved.slice(0, 6).map((u, i) => (
                <li key={i} className="rounded-xl bg-bad/[.045] px-2.5 py-2">
                  <code className="font-mono text-ink">{shortFile(u.file)}</code>
                  <span> — {u.case}</span>
                </li>
              ))}
            </ul>
          ) : <p className="text-[11.5px] text-ok">None. Nothing was hidden or set aside.</p> : <Empty>No report.</Empty>}
        </Card>

        <Card title="Browser flows" hint="real Playwright journeys through the app">
          {r?.e2e ? (
            <>
              <div className={cn('text-[26px] font-semibold', r.e2e.failed ? 'text-bad' : 'text-ok')}>
                {r.e2e.failed ? `${r.e2e.failed} failing` : 'Passing'}
              </div>
              <p className="mt-2 text-[11px] text-muted">{(r.e2e.flows || []).length} journey(s) recorded · {r.e2e.fixed || 0} repair round(s)</p>
            </>
          ) : <Empty>No E2E record yet.</Empty>}
        </Card>

        <Card title="Security" hint="source and dependency checks">
          {r?.security ? sec.length ? (
            <ul className="space-y-2 text-[11px]">
              {sec.slice(0, 6).map((f, i) => (
                <li key={i} className="flex items-center gap-2 rounded-xl bg-warn/[.05] px-2.5 py-2">
                  <span className="rounded-full bg-warn/10 px-2 py-0.5 text-[9px] font-semibold uppercase text-warn">{f.severity}</span>
                  <code className="min-w-0 truncate font-mono text-ink">{f.path}</code>
                </li>
              ))}
            </ul>
          ) : <p className="text-[11.5px] text-ok">Nothing found.</p> : <Empty>No security record.</Empty>}
        </Card>

        <Card title="Performance" hint="Lighthouse snapshot">
          {qa?.performance?.runtimeError ? (
            <p className="rounded-xl bg-bad/[.05] p-2.5 text-[11px] text-bad">Lighthouse could not measure the app: {qa.performance.runtimeError}</p>
          ) : Object.keys(perf).length ? (
            <div className="space-y-2.5">
              {Object.entries(perf).map(([k, n]) => (
                <div key={k}>
                  <div className="flex items-center justify-between text-[11px]"><span className="capitalize text-muted">{k.replace(/-/g, ' ')}</span><b className={n >= 90 ? 'text-ok' : n >= 50 ? 'text-warn' : 'text-bad'}>{n}</b></div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-panel2"><span className={cn('block h-full rounded-full', n >= 90 ? 'bg-ok' : n >= 50 ? 'bg-warn' : 'bg-bad')} style={{ width: `${Math.max(2, Math.min(100, n))}%` }} /></div>
                </div>
              ))}
            </div>
          ) : <Empty>{perfSkipReason(r)}</Empty>}
        </Card>

        <Card title="Runtime" hint="errors seen by the browser probe">
          {r ? (r.runtime || []).length ? (
            <ul className="space-y-2 text-[11px] text-bad">{r.runtime.slice(0, 6).map((e, i) => <li key={i} className="rounded-xl bg-bad/[.045] px-2.5 py-2">{String(e).split('\n')[0]}</li>)}</ul>
          ) : <p className="text-[11.5px] text-ok">No runtime errors.</p> : <Empty>No record.</Empty>}
        </Card>
      </div>
    </div>
  )
}

const Card = ({ title, hint, children }) => (
  <section className="rounded-[22px] border border-line/80 bg-white/58 p-4 shadow-sm dark:bg-white/[.025]">
    <div className="mb-3">
      <h3 className="text-[12px] font-semibold text-ink">{title}</h3>
      {hint && <p className="mt-0.5 text-[10.5px] text-muted2">{hint}</p>}
    </div>
    {children}
  </section>
)
const shortFile = (p) => String(p || '').split('/').pop()

/**
 * Why the Lighthouse panel is empty.
 *
 * The stage no longer waits for a clean run — it measures the page the server
 * returns, and a journey failing three steps in says nothing about how that
 * page loads. The build is the only precondition left, because without it
 * there is no app to open. A bare "Lighthouse has not run" made an empty
 * panel look broken; name what is actually missing instead.
 */
function perfSkipReason(r) {
  if (!r) return 'Lighthouse has not run.'
  if (r?.build && r.build.passed === false) {
    return 'Skipped — the production build is not green, so there was no app to measure.'
  }
  return 'Lighthouse has not run for this project yet. It runs at the end of a '
       + 'build, whether or not the browser journeys passed.'
}

'use client'

import { AlertTriangle } from 'lucide-react'
import { Empty, Table, TR, TH, TD } from '../ui'
import { cn } from '@/lib/utils'

/**
 * What Lighthouse's own failure codes mean, in words.
 *
 * The stage records the code and nothing else, and a code on its own reads as
 * "it did not run" — which is the opposite of what happened. Every one of
 * these means Lighthouse started, opened Chrome, and could not get a
 * measurable page out of the dev server.
 */
const WHY = {
  CHROME_INTERSTITIAL_ERROR: [
    'Chrome could not open the app at all — it landed on an error page instead.',
    'The dev server on :5173 was not answering when the check ran. It usually '
    + 'means the app had crashed, was still compiling its first route, or had '
    + 'been stopped between the build finishing and this stage starting.',
  ],
  FAILED_DOCUMENT_REQUEST: [
    'The request for the page failed.',
    'Nothing was listening on the port, or the connection was refused.',
  ],
  ERRORED_DOCUMENT_REQUEST: [
    'The server answered with an error status.',
    'The route returned a 4xx or 5xx instead of a page — check the Bug Reports '
    + 'tab for a runtime error on the same run.',
  ],
  NO_FCP: [
    'The page loaded but never painted anything.',
    'Usually a component that throws on first render, so Chrome had nothing to '
    + 'measure.',
  ],
  PAGE_HUNG: [
    'The page stopped responding while it was being measured.',
    'Normally an infinite loop or a render that never settles.',
  ],
  NO_NAVSTART: [
    'Chrome never started the navigation.',
    'The browser was closed or killed before the run got going.',
  ],
}

export default function Performance({ qa }) {
  const p = qa?.performance

  // Order matters. A failed run has BOTH an empty `scores` and a code, so the
  // empty check has to come second — reading it first reported "has not run"
  // over a run that did happen and said exactly why it stopped.
  if (p?.runtimeError) {
    const [what, why] = WHY[p.runtimeError] || [
      'Lighthouse could not measure this app.', '',
    ]
    return (
      <div className="max-w-[640px] border border-line2 border-l-[3px]
                      border-l-bad bg-tint p-4">
        <div className="flex items-center gap-2.5 border-b border-line2 pb-2">
          <AlertTriangle className="size-4 shrink-0 text-bad" />
          <span className="label-xs text-ink">Lighthouse ran and failed</span>
          <span className="flex-1" />
          <code className="shrink-0 font-mono text-[10.5px] text-deep">
            {p.runtimeError}
          </code>
        </div>
        <p className="mt-3 text-[12.5px] leading-relaxed text-ink">{what}</p>
        {why && (
          <p className="mt-1.5 text-[11.5px] leading-relaxed text-muted">{why}</p>
        )}
        <p className="mt-3 border-t border-line pt-2.5 text-[11px] text-muted">
          Open the Preview tab and check the app actually loads, then build or
          repair again — the performance stage runs at the end of every run and
          will measure it next time.
          {p.fetchTime && (
            <span className="mt-1 block font-mono text-[10.5px] text-muted2">
              attempted {new Date(p.fetchTime).toLocaleString()}
            </span>
          )}
        </p>
      </div>
    )
  }

  if (!p || !Object.keys(p.scores || {}).length) {
    return <Empty>Lighthouse has not run for this project.</Empty>
  }
  return (
    <div>
      {/* Each score is a ruled cell with a bar under it — the figure and the
          measure in the same block, flush left, no dials. */}
      <div className="mb-[18px] grid border-l border-t border-line2
                      [grid-template-columns:repeat(auto-fit,minmax(150px,1fr))]">
        {Object.entries(p.scores).map(([k, n]) => (
          <div key={k} className="border-b border-r border-line2 bg-panel px-4 py-3">
            <div className="label-2xs text-label">{k.replace(/-/g, ' ')}</div>
            <div className={cn('mt-1.5 font-display text-[26px] font-extrabold',
              'leading-none tracking-[-.02em]',
              n >= 90 ? 'text-ok' : n >= 50 ? 'text-warn' : 'text-bad')}>
              {n}
            </div>
            <div className="mt-2 flex h-[6px] border border-line2 bg-panel2">
              <span className={n >= 90 ? 'bg-ok' : n >= 50 ? 'bg-warn' : 'bg-bad'}
                    style={{ width: `${Math.max(2, Math.min(100, n))}%` }} />
            </div>
          </div>
        ))}
      </div>

      {Object.keys(p.metrics || {}).length > 0 && (
        <Table>
          <thead><TR><TH>metric</TH><TH>value</TH></TR></thead>
          <tbody>
            {Object.entries(p.metrics).map(([k, v]) => (
              <TR key={k}>
                <TD className="capitalize text-muted">{k.replace(/-/g, ' ')}</TD>
                <TD><b className="font-mono text-ink">{v}</b></TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      <p className="mt-3 text-[10.5px] text-muted2">
        Measured against the development server, which compiles each route on
        first request. The performance score is not a production figure.
        {p.fetchTime && ` Run at ${new Date(p.fetchTime).toLocaleString()}.`}
      </p>
    </div>
  )
}

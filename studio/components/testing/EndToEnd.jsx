'use client'

import { Empty, Tag } from '../ui'
import { journeyStatus, journeySummary } from '@/lib/e2e-rate'

export default function EndToEnd({ qa }) {
  const e2e = qa?.report?.e2e
  if (!e2e || !Object.keys(e2e).length) {
    return <Empty>The end-to-end stage has no record for this project.</Empty>
  }
  const failures = e2e.failures || []
  const testIssues = e2e.test_issues || []
  // One entry per journey the stage walked, passing ones included.
  const journeys = e2e.flows || []
  const summary = journeySummary(e2e)

  return (
    <div>
      <div className="mb-[18px] border-b-2 border-line2 pb-3">
        <div className="flex flex-wrap items-center gap-3">
          <Tag tone={summary.tone === 'muted' ? 'mute' : summary.tone}>{summary.label}</Tag>
          <span className="text-[12px] text-ink">{summary.detail}</span>
          {e2e.fixed ? <Tag>{e2e.fixed} repair round(s)</Tag> : null}
        </div>
        {summary.walked > 0 && (
          <div className="mt-2 flex h-1.5 overflow-hidden rounded-full bg-black/[.06] dark:bg-white/[.08]">
            {summary.passed > 0 && (
              <div className="h-full bg-emerald-500"
                   style={{ width: `${(summary.passed / summary.total) * 100}%` }} />
            )}
            {summary.failed > 0 && (
              <div className="h-full bg-rose-500"
                   style={{ width: `${(summary.failed / summary.total) * 100}%` }} />
            )}
            {(summary.blocked + summary.skipped) > 0 && (
              <div className="h-full bg-black/15 dark:bg-white/20"
                   style={{ width: `${((summary.blocked + summary.skipped) / summary.total) * 100}%` }} />
            )}
          </div>
        )}
        {summary.blocked > 0 && (
          <p className="mt-2 text-[11.5px] leading-relaxed text-muted">
            {summary.blocked} journey{summary.blocked === 1 ? '' : 's'} never
            started because the model was busy. That is not a problem with the
            app — run the check again when it is free.
          </p>
        )}
        {testIssues.length > 0 && (
          <p className="mt-2 text-[11.5px] leading-relaxed text-amber-700 dark:text-amber-300">
            {testIssues.length} E2E issue{testIssues.length === 1 ? '' : 's'} remained
            {' '}after the bounded two-round repair. Generation continued and the details remain visible below.
          </p>
        )}
      </div>

      {!e2e.ran && <p className="mb-3 text-[11.5px] text-muted">The stage did not run.</p>}

      {failures.length > 0 && (
        <ul className="border border-line2">
          {failures.map((f, i) => (
            <li key={i} className="border-b border-line border-l-[3px] border-l-accent
                                   bg-tint px-3 py-2 text-[11.5px] last:border-b-0">
              <code className="font-mono text-ink">{f.target || f.file}</code>
              <span className="text-deep"> — {f.case || f.message}</span>
            </li>
          ))}
        </ul>
      )}

      {testIssues.length > 0 && (
        <ul className="mt-3 border border-amber-300/70 dark:border-amber-400/20">
          {testIssues.map((f, i) => (
            <li key={i} className="border-b border-amber-200/70 border-l-[3px] border-l-amber-500
                                   bg-amber-50/70 px-3 py-2 text-[11.5px] last:border-b-0
                                   dark:border-amber-400/10 dark:bg-amber-400/[.06]">
              <code className="font-mono text-ink">{f.target || f.file || 'E2E harness'}</code>
              <span className="text-deep"> — {f.case || f.message}</span>
            </li>
          ))}
        </ul>
      )}

      {journeys.length > 0 && (
        <div className="mt-[18px]">
          <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wide
                         text-muted">Journeys walked</h4>
          <ul className="border border-line2">
            {journeys.map((j, i) => {
              const status = journeyStatus(j)
              return (
                <li key={i} className={`flex flex-wrap items-baseline gap-2
                                        border-b border-line px-3 py-2
                                        text-[11.5px] last:border-b-0
                                        ${status.tone === 'bad' ? 'bg-tint' : ''}`}>
                  <Tag tone={status.tone === 'muted' ? undefined : status.tone}>
                    {status.label}
                  </Tag>
                  <span className="text-ink">{j.title}</span>
                  {j.role && <span className="text-muted">as {j.role}</span>}
                  {j.fixed ? (
                    <span className="text-muted">
                      after {j.fixed} repair round(s)
                    </span>
                  ) : null}
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </div>
  )
}

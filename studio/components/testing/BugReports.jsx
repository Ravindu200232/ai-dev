'use client'

import { Empty, Tag } from '../ui'

export default function BugReports({ qa }) {
  const r = qa?.report
  if (!r) {
    return <Empty>No report — this project was built before results were kept.</Empty>
  }
  const unresolved = r.suite?.unresolved || []
  const suspects = r.suite?.suspects || []
  const quarantined = r.suite?.quarantined || []
  const failures = r.suite?.failures || []

  if (!unresolved.length && !suspects.length && !quarantined.length && !failures.length) {
    return <p className="text-[12px] text-ok">Nothing open. The suite ended clean.</p>
  }

  return (
    <div className="space-y-5">
      {unresolved.length > 0 && (
        <section>
          <h3 className="label-xs border-b-2 border-line2 pb-1.5 text-ink">
            Left unresolved
          </h3>
          <div className="mt-2.5 space-y-2">
            {unresolved.map((u, i) => (
              <div key={i} className="border border-line2 border-l-[3px]
                                      border-l-accent bg-tint p-3.5">
                <div className="mb-1.5 flex flex-wrap items-center gap-2">
                  <Tag tone="solid">unresolved</Tag>
                  <b className="text-[12px] text-ink">{u.case}</b>
                  {u.diagnosis && <Tag>{u.diagnosis}</Tag>}
                </div>
                <p className="mb-1 font-mono text-[10.5px] text-muted">
                  {u.file}{u.target ? ` → ${u.target}` : ''}
                </p>
                <p className="text-[11.5px] text-deep">{u.message}</p>
                {u.why && <p className="mt-1 text-[10.5px] text-muted">{u.why}</p>}
              </div>
            ))}
          </div>
        </section>
      )}

      {suspects.length > 0 && (
        <section>
          <h3 className="label-xs border-b-2 border-line2 pb-1.5 text-ink">
            Suspected bugs in the app
          </h3>
          <p className="mt-2 text-[10.5px] text-muted2">
            Left by the test author: the code looked wrong, but a test has to
            describe what the code does.
          </p>
          <ul className="mt-2 text-[11.5px] text-muted">
            {suspects.map((s, i) => (
              <li key={i} className="border-b border-line py-1.5 last:border-0">
                <code className="font-mono text-ink">{s.test}</code> — {s.note}
              </li>
            ))}
          </ul>
        </section>
      )}

      {quarantined.length > 0 && (
        <section>
          <h3 className="label-xs border-b-2 border-line2 pb-1.5 text-ink">
            Set aside
          </h3>
          <ul className="mt-2 font-mono text-[11px]">
            {quarantined.map((q, i) => (
              <li key={i} className="border-b border-line border-l-[3px]
                                     border-l-accent bg-tint px-2 py-1.5 text-deep
                                     last:border-b-0">
                {q}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

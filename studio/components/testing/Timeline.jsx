'use client'

import { Empty, Table, TR, TH, TD } from '../ui'
import { cn } from '@/lib/utils'


export default function Timeline({ qa }) {
  const rows = qa?.history || []
  if (!rows.length) {
    return <Empty>No history yet — it starts at the next build.</Empty>
  }
  const floor = rows[rows.length - 1].floor ?? 90

  return (
    <div>
      <p className="mb-3 text-[11px] text-muted">
        Each bar is one build&apos;s round-one pass rate. The dashed line is the
        {' '}{floor}% floor.
      </p>

      {/* Bars in a ruled frame, square-topped and flush to the baseline. A
          build under the floor is the only one that takes the accent. */}
      <div className="relative mb-4 flex h-[150px] items-end gap-1.5 border-b-2
                      border-line2 bg-panel2 px-2 pt-2">
        <div className="pointer-events-none absolute inset-x-0 border-t border-dashed
                        border-accent"
             style={{ top: `${100 - floor}%` }} />
        {rows.map((r, i) => (
          <div key={i} title={`${r.passed}/${r.cases} — ${r.at}`}
               className="flex h-full max-w-[46px] flex-1 flex-col items-center
                          justify-end">
            <span className="mb-1 font-mono text-[9px] text-muted2">{r.rate}%</span>
            <div className={cn('w-full', r.rate < floor ? 'bg-bad' : 'bg-ok')}
                 style={{ height: `${r.rate}%`, minHeight: 2 }} />
          </div>
        ))}
      </div>

      <Table>
        <thead>
          <TR>
            <TH>when</TH><TH>cases</TH><TH>passed</TH><TH>failed</TH>
            <TH>rate</TH><TH>largest class</TH>
          </TR>
        </thead>
        <tbody>
          {rows.slice().reverse().map((r, i) => (
            <TR key={i} className={r.rate < (r.floor ?? floor)
                                     ? 'bg-tint text-deep' : ''}>
              <TD className="font-mono text-muted">
                {String(r.at || '').replace('T', ' ').replace('+00:00', '')}
              </TD>
              <TD className="font-mono">{r.cases}</TD>
              <TD className="font-mono text-ok">{r.passed}</TD>
              <TD className={cn('font-mono', r.failed ? 'text-bad' : 'text-muted2')}>
                {r.failed}
              </TD>
              <TD><b className="font-mono text-ink">{r.rate}%</b></TD>
              <TD className="text-muted">
                {(r.top || [])[0] ? `${r.top[0].count} × ${r.top[0].class}` : '—'}
              </TD>
            </TR>
          ))}
        </tbody>
      </Table>
    </div>
  )
}

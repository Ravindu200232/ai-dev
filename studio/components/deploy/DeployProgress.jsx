'use client'

import { AlertTriangle, Check, Loader2, Rocket } from 'lucide-react'
import { PIPELINE, STATE_TEXT, progressOf } from '@/lib/deploy-constants'
import { SectionLabel, Tag } from '../ui'
import { cn } from '@/lib/utils'

export default function DeployProgress({ run }) {
  if (!run) return null
  const stages = PIPELINE[run.target] || PIPELINE.vercel
  const { rows, pct } = progressOf(run.events, stages)
  const [label, tone] = STATE_TEXT[run.state] || [run.state || 'Working', 'run']

  return (
    <div className="border border-line2 p-4">
      <SectionLabel className="border-b-2 border-line2 pb-1.5"
                    right={<Tag tone={{ pass: 'ok', fail: 'bad',
                                        run: 'accent' }[tone] || 'mute'}>
                      {label}
                    </Tag>}>
        Deploying to {run.target === 'vercel' ? 'Vercel' : 'AWS EC2'}
      </SectionLabel>

      <div className="mt-3.5 flex h-2 border border-line2 bg-canvas">
        <span className={cn('transition-all duration-500',
                            tone === 'fail' ? 'bg-bad' : tone === 'pass' ? 'bg-ok' : 'bg-accent')}
              style={{ width: `${pct}%` }} />
      </div>
      <p className="mt-2 flex items-center gap-2.5 text-[11.5px] text-muted">
        <span className="font-mono text-[12px] text-accent">{pct}%</span>
        {run.message}
      </p>

      <ol className="mt-4 border-t-2 border-line2">
        {rows.map(r => (
          <li key={r.id}
              className={cn('flex items-start gap-2.5 border-b border-line',
                'border-l-[3px] py-2 pl-2.5 pr-1',
                r.status === 'active' ? 'border-l-accent bg-panel2'
                  : r.status === 'error' ? 'border-l-bad bg-tint'
                  : r.status === 'done' ? 'border-l-ok'
                  : 'border-l-transparent')}>
            <span className="mt-[2px] grid size-3.5 shrink-0 place-items-center">
              {r.status === 'done'
                ? <Check className="size-3.5 text-ok" />
                : r.status === 'error'
                ? <AlertTriangle className="size-3.5 text-bad" />
                : r.status === 'active'
                ? <Loader2 className="size-3 animate-spin text-accent" />
                : <span className="size-1.5 bg-faint" />}
            </span>
            <span className="min-w-0 flex-1">
              <span className={cn('block text-[12px]',
                r.status === 'pending' ? 'text-muted2'
                  : r.status === 'error' ? 'font-semibold text-deep'
                  : r.status === 'active' ? 'font-extrabold text-ink' : 'text-ink')}>
                {r.title}
              </span>
              <span className="block font-mono text-[10.5px] leading-snug text-muted2">
                {r.message || r.detail}
              </span>
            </span>
          </li>
        ))}
      </ol>

      {run.error && (
        <p className="mt-3 flex items-start gap-2.5 border border-line2
                      border-l-[3px] border-l-accent bg-tint px-3 py-2.5
                      text-[11.5px] text-deep">
          <AlertTriangle className="mt-px size-3.5 shrink-0 text-accent" />
          <span className="min-w-0">{run.error}</span>
        </p>
      )}

      {run.url && (
        <a href={run.url} target="_blank" rel="noreferrer"
           className="mt-3 flex items-center gap-2.5 bg-accent px-3 py-2.5
                      font-mono text-[12px] text-bg transition-colors hover:bg-press">
          <Rocket className="size-3.5 shrink-0" />
          <span className="min-w-0 truncate">{run.url}</span>
        </a>
      )}
    </div>
  )
}

'use client'

import { Camera, Check, Download, ExternalLink, FileArchive, Clock } from 'lucide-react'
import { API } from '@/lib/api'
import { Badge, Button, Panel, SectionLabel } from '../ui'
import { ago } from './MonitorViews'
import { cn } from '@/lib/utils'

export function Evidence({ evidence, runId, busy }) {
  const items = evidence || []
  const verified = items.filter(e => e.verified).length

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat Icon={Camera} label="Captured" value={items.length} />
        <Stat Icon={Check} label="Verified" value={verified}
              tone={items.length && verified === items.length ? 'ok' : undefined} />
        <Stat Icon={Clock} label="Newest"
              value={items.length ? ago(items[items.length - 1].created_at) : '—'} />
      </div>

      <Panel className="p-4">
        <SectionLabel className="border-b-2 border-line2 pb-1.5" right={
          runId ? (
            <div className="flex gap-1">
              <a href={`${API}/deploy/runs/${runId}/export.zip`}
                 target="_blank" rel="noreferrer">
                <Button size="sm" variant="outline">
                  <FileArchive className="size-3" /> Evidence .zip
                </Button>
              </a>
              <a href={`${API}/deploy/runs/${runId}/report.pdf`}
                 target="_blank" rel="noreferrer">
                <Button size="sm" variant="outline">
                  <Download className="size-3" /> Report .pdf
                </Button>
              </a>
            </div>
          ) : null
        }>
          Deployment evidence
        </SectionLabel>

        {items.length === 0
          ? <p className="mt-2 text-[11.5px] text-muted">
              {busy
                ? 'Reading the evidence list…'
                : 'Nothing captured yet. The exporter runs at the end of a ' +
                  'deployment, so a run that stopped earlier has none — the ' +
                  'two downloads above still work and package what does exist.'}
            </p>
          : <ul className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
              {items.map(e => (
                <li key={e.id}
                    className=" border border-line bg-bg p-2.5">
                  <div className="flex items-start gap-2">
                    <Camera className="mt-px size-3.5 shrink-0 text-muted2" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[11.5px] text-ink" title={e.name}>
                        {e.name}
                      </p>
                      <p className="truncate font-mono text-[10px] text-muted2"
                         title={e.path}>
                        {String(e.path || '').split(/[\\/]/).pop()}
                      </p>
                    </div>
                    <Badge tone={e.verified ? 'ok' : 'mute'} className="shrink-0">
                      {e.verified ? 'verified' : 'captured'}
                    </Badge>
                  </div>
                  <div className="mt-2 flex items-center justify-between">
                    <span className="text-[10px] text-muted2">{ago(e.created_at)}</span>
                    <a href={`${API}/deploy/runs/${runId}/evidence/file?id=${e.id}`}
                       target="_blank" rel="noreferrer"
                       className="inline-flex items-center gap-1 text-[10.5px]
                                  text-accent hover:underline">
                      open <ExternalLink className="size-2.5" />
                    </a>
                  </div>
                </li>
              ))}
            </ul>}
      </Panel>
    </div>
  )
}

function Stat({ Icon, label, value, tone }) {
  return (
    <Panel className="p-3">
      <span className="flex items-center gap-1.5 text-[9px] font-semibold uppercase
                       tracking-[1px] text-muted2">
        <Icon className="size-3" /> {label}
      </span>
      <span className={cn('mt-1 block text-[18px] font-semibold leading-none',
                          tone === 'ok' ? 'text-ok' : 'text-ink')}>
        {value}
      </span>
    </Panel>
  )
}

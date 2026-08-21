'use client'

import { AlertTriangle, Check, FileText, Loader2, X } from 'lucide-react'
import { Badge, Empty, Panel, SectionLabel } from '../ui'
import { artifactsByKind, duration, pipelineStages, size } from '@/lib/use-run-data'
import { cn } from '@/lib/utils'


const WHAT = {
  analysis: 'Read the project and decide what it is',
  intake: 'Stage the source and detect services',
  planner: 'Choose the topology, sizing and environment contract',
  runtime: 'Write .env.example and the runtime assets',
  cicd: 'Generate the GitHub Actions workflows',
  aws: 'Generate the CloudFormation template and release script',
  generator: 'Collect every artifact for review',
  security: 'Scan the artifacts for committed secrets',
  build: 'Run the production build',
  repair: 'Apply compatibility patches to the source',
  review: 'Hold for approval before anything is applied',
  bootstrap: 'Create or update the AWS stack',
  secrets: 'Store the runtime secret in Secrets Manager',
  apply: 'Write the reviewed files into the repository',
  github: 'Push and let GitHub Actions deploy',
  deploy: 'Wait for the deployment workflow to finish',
  validation: 'Probe the homepage and the health endpoint',
  evidence: 'Mask and package the deployment evidence',
}


const KIND_STAGE = {
  environment: 'runtime',
  cicd: 'cicd',
  aws: 'aws',
  patch: 'repair',
  source: 'repair',
  report: 'evidence',
}

export function Pipeline({ events, artifacts, busy }) {
  const stages = pipelineStages(events || [])
  if (!stages.length) {
    return busy
      ? <Empty>Reading the event stream…</Empty>
      : <Empty>This run recorded no pipeline events.</Empty>
  }

  const done = stages.filter(s => s.done).length
  const failed = stages.filter(s => s.failed).length

  const open = stages.length - done - failed
  const total = stages.reduce((sum, s) => sum + (s.ms || 0), 0)

  const byStage = new Map()
  for (const a of artifacts || []) {
    const stage = KIND_STAGE[String(a.kind || '')]
    if (!stage) continue
    if (!byStage.has(stage)) byStage.set(stage, [])
    byStage.get(stage).push(a)
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_260px]">
      <Panel className="p-4">
        <SectionLabel className="border-b-2 border-line2 pb-1.5" right={
          <Badge tone={failed ? 'bad' : open ? 'warn' : 'ok'}>
            {failed ? `${failed} failed` : open ? `${open} open` : 'Complete'}
          </Badge>
        }>
          Deployment pipeline
        </SectionLabel>
        <p className="mt-1 text-[10.5px] text-muted2">
          {stages.length} stages recorded by the agent for this run
        </p>

        <ol className="mt-3">
          {stages.map((s, i) => (
            <Step key={s.stage} n={i + 1} step={s}
                  last={i === stages.length - 1}
                  artifacts={byStage.get(s.stage) || []} />
          ))}
        </ol>
      </Panel>

      <div className="space-y-4">
        <Panel className="p-4">
          <SectionLabel className="border-b-2 border-line2 pb-1.5">Pipeline summary</SectionLabel>
          <dl className="mt-2 space-y-1.5">
            <Row label="Total stages" value={stages.length} />
            <Row label="Completed" value={done} tone={done === stages.length ? 'ok' : undefined} />
            <Row label="Open" value={open} tone={open ? 'warn' : undefined} />
            <Row label="Failures" value={failed} tone={failed ? 'bad' : undefined} />
            <Row label="Recorded time" value={duration(total)} />
          </dl>
          <p className="mt-2 text-[10px] leading-relaxed text-muted2">
            Recorded time is the sum of each stage’s own span, not wall-clock —
            stages overlap and the stream carries no single start.
          </p>
        </Panel>

        <Panel className="p-4">
          <SectionLabel className="border-b-2 border-line2 pb-1.5" right={<span className="text-[10px] text-muted2">
                                 {(artifacts || []).length}
                               </span>}>
            Key artifacts
          </SectionLabel>
          {(artifacts || []).length === 0
            ? <p className="mt-2 text-[11.5px] text-muted">None recorded.</p>
            : <div className="mt-2 space-y-2.5">
                {artifactsByKind(artifacts).map(({ kind, items }) => (
                  <div key={kind}>
                    <p className="text-[9px] font-semibold uppercase tracking-[1px] text-muted2">
                      {kind}
                    </p>
                    <ul className="mt-1 space-y-0.5">
                      {items.map(a => (
                        <li key={a.path}
                            className="flex items-baseline gap-1.5 font-mono text-[10px]">
                          <FileText className="size-2.5 shrink-0 translate-y-0.5 text-muted2" />
                          <span className="min-w-0 flex-1 truncate text-muted" title={a.path}>
                            {a.path}
                          </span>
                          <span className="shrink-0 text-muted2">{size(a.size)}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>}
        </Panel>
      </div>
    </div>
  )
}

function Step({ n, step, last, artifacts }) {
  const Icon = step.failed ? X : step.done ? Check : Loader2
  const tone = step.failed ? 'text-bg border-bad bg-bad'
             : step.done ? 'text-bg border-ok bg-ok'
             : 'text-accent border-accent bg-panel'
  return (
    <li className="relative flex gap-3 pb-3 last:pb-0">
      {!last && <span className="absolute left-[11px] top-6 h-[calc(100%-1.5rem)] w-px bg-line2" />}
      <span className={cn('relative z-10 flex size-[22px] shrink-0 items-center justify-center',
                          'border', tone)}>
        <Icon className={cn('size-3', !step.done && !step.failed && 'animate-spin')} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-[11.5px] font-extrabold text-ink">
            <span className="font-mono font-normal text-faint">
              {String(n).padStart(2, '0')}
            </span>{' '}
            {step.stage}
          </span>
          <span className="flex-1" />
          <span className="shrink-0 font-mono text-[10px] text-muted2">
            {duration(step.ms)}
          </span>
        </div>
        {WHAT[step.stage] && (
          <p className="text-[10.5px] text-muted2">{WHAT[step.stage]}</p>
        )}
        {step.message && (
          <p className="mt-0.5 text-[11px] text-muted">{step.message}</p>
        )}
        {!step.done && !step.failed && (
          <p className="mt-0.5 flex items-center gap-1 text-[10.5px] text-warn">
            <AlertTriangle className="size-2.5" /> never reported complete
          </p>
        )}
        {artifacts.length > 0 && (
          <ul className="mt-1 space-y-0.5">
            {artifacts.map(a => (
              <li key={a.path}
                  className="flex items-baseline gap-1.5 font-mono text-[10px] text-accent">
                <FileText className="size-2.5 shrink-0 translate-y-0.5" />
                <span className="min-w-0 truncate" title={a.path}>{a.path}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  )
}

function Row({ label, value, tone }) {
  return (
    <div className="flex items-baseline justify-between gap-2 text-[11.5px]">
      <dt className="text-muted2">{label}</dt>
      <dd className={cn('font-mono text-[11px]',
        tone === 'ok' ? 'text-ok' : tone === 'bad' ? 'text-bad'
        : tone === 'warn' ? 'text-warn' : 'text-ink')}>
        {value}
      </dd>
    </div>
  )
}

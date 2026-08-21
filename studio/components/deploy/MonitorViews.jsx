'use client'

import { AlertTriangle, ExternalLink, Server } from 'lucide-react'
import { Badge, Empty, Panel, SectionLabel, Table, Tag, TD, TH, TR } from '../ui'
import { cn } from '@/lib/utils'


export function StatusBar({ snap, state }) {
  if (!snap) return null
  const score = Number(snap?.readiness?.score || 0)
  const g = snap?.github || {}
  const aws = snap?.aws || {}
  const vercel = snap?.vercel || {}
  const probes = snap?.api || []

  const bits = []
  if (score) bits.push(['Readiness', `${score}/100`, score >= 90 ? 'ok' : score >= 60 ? 'warn' : 'bad'])
  if (probes.length) {
    const ok = probes.filter(p => p.passed).length
    bits.push(['Endpoints', `${ok}/${probes.length}`, ok === probes.length ? 'ok' : 'bad'])
  }
  if (g.repository) bits.push(['Workflows', `${g.successful || 0} passed`, g.failed ? 'warn' : 'ok'])
  if (aws.region) bits.push(['Region', aws.region, 'mute'])
  if (aws.ecs_cluster) bits.push(['Cluster', aws.ecs_cluster, 'mute'])
  else if ((aws.instances || []).length) bits.push(['Instances', String(aws.instances.length), 'mute'])
  if (vercel.project_name) bits.push(['Project', vercel.project_name, 'mute'])

  const live = String(state || '').toUpperCase() === 'LIVE'
  return (
    // One ruled band across the pane: the state at the left in the heading
    // face, then every fact as a label-and-figure pair. Nothing is boxed.
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 border-b-2
                    border-line2 bg-panel2 px-4 py-[11px]">
      <span className="flex items-center gap-2 font-display text-[12.5px]
                       font-extrabold text-ink">
        <span className={cn('size-[7px]', live ? 'bg-ok' : 'bg-faint')} />
        {live ? 'Deployment complete — the site is live' : (state || 'Deployment')}
      </span>
      {bits.map(([label, value, tone]) => (
        <span key={label} className="flex items-center gap-[7px] text-[11px]">
          <span className="text-label">{label}</span>
          <span className={cn('font-mono text-[10.5px]',
            tone === 'bad' ? 'text-bad' : tone === 'warn' ? 'text-warn'
            : tone === 'ok' ? 'text-ok' : 'text-muted')}>
            {value}
          </span>
        </span>
      ))}
    </div>
  )
}


const WEIGHTS = [
  ['build', 15, 'Production build passed'],
  ['cicd', 20, 'CI and deploy workflows'],
  ['provider', 25, 'Infrastructure or project'],
  ['security', 20, 'Secrets and artifacts'],
  ['monitoring', 10, 'Log lines seen'],
  ['api', 10, 'Live endpoint probes'],
]

export function Overview({ snap }) {
  const r = snap?.readiness || {}
  const cats = r.categories || {}
  const score = Number(r.score || 0)
  const probes = snap?.api || []
  const errors = snap?.errors || []

  return (
    <div className="space-y-4">
      {/* The score is the largest thing on the page, set in the heading face
          and coloured only when it is short of the bar. The bar itself is a
          ruled trough with the agent's own mark struck through it at 90. */}
      <div className="border border-line2 p-5">
        <div className="flex items-baseline gap-3.5">
          <span className={cn('font-display text-[64px] font-extrabold leading-[.9]',
                              'tracking-[-.04em]',
                              score >= 90 ? 'text-ok'
                                : score >= 60 ? 'text-warn' : 'text-bad')}>
            {score}
          </span>
          <div className="min-w-0">
            <div className="font-display text-[13px] font-extrabold text-ink">
              out of 100
            </div>
            <div className="mt-0.5 text-[11.5px] text-muted">
              {score >= 90
                ? 'at or over the mark — the agent will promote this run'
                : 'below the mark — the agent will not promote this run'}
            </div>
          </div>
          <span className="flex-1" />
          <Tag tone={score >= 90 ? 'ok' : score >= 60 ? 'warn' : 'bad'}>
            {score >= 90 ? 'ready' : 'needs work'}
          </Tag>
        </div>

        <div className="relative mt-4 flex h-3 border border-line2 bg-canvas">
          <span className={cn('transition-all',
                              score >= 90 ? 'bg-ok'
                                : score >= 60 ? 'bg-warn' : 'bg-bad')}
                style={{ width: `${Math.min(100, score)}%` }} />
          <span className="absolute -bottom-[5px] -top-[5px] w-[2px] bg-ink"
                style={{ left: '90%' }} />
        </div>
        <div className="mt-1.5 flex justify-between">
          <span className="font-mono text-[10px] text-faint">0</span>
          <span className="text-[10.5px] text-muted">
            the mark is 90 — the agent’s own bar
          </span>
          <span className="font-mono text-[10px] text-faint">100</span>
        </div>

        <div className="mt-5 border-t-2 border-line2">
          {WEIGHTS.map(([key, weight, label]) => {
            const got = Number(cats[key] ?? 0)
            const full = got >= weight
            return (
              <div key={key}
                   className="grid grid-cols-[88px_1fr_60px] items-center gap-3.5
                              border-b border-line py-2.5
                              sm:grid-cols-[88px_1fr_60px_1fr]">
                <span className={cn('font-mono text-[11px]',
                                    full ? 'text-muted' : 'text-deep')}>
                  {key}
                </span>
                <span className="flex h-2 bg-canvas">
                  <span className={full ? 'bg-ok' : got > 0 ? 'bg-warn' : 'bg-bad'}
                        style={{ width: `${(got / weight) * 100}%` }} />
                </span>
                <span className={cn('text-right font-mono text-[10.5px]',
                                    full ? 'text-ok' : 'text-warn')}>
                  {got}/{weight}
                </span>
                <span className="hidden min-w-0 truncate text-[11px] text-label sm:block">
                  {label}
                </span>
              </div>
            )
          })}
        </div>
      </div>

      {probes.length > 0 && (
        <div className="border border-line2 p-4">
          <SectionLabel className="border-b-2 border-line2 pb-1.5"
                        right={<Badge tone={probes.every(p => p.passed) ? 'ok' : 'bad'}>
                          {probes.filter(p => p.passed).length}/{probes.length}
                        </Badge>}>
            Live checks
          </SectionLabel>
          <p className="mt-2.5 text-[11.5px] text-muted">
            {probes.every(p => p.passed)
              ? 'Every endpoint the agent probed answered as expected.'
              : `${probes.filter(p => !p.passed).length} endpoint(s) did not answer as expected.`}
            {' '}See API Validation for each one.
          </p>
        </div>
      )}

      {errors.length > 0 && (
        <div className="border border-line2 p-4">
          <SectionLabel className="border-b-2 border-line2 pb-1.5"
                        right={<Badge tone="bad">{errors.length}</Badge>}>
            Errors seen
          </SectionLabel>
          <ul className="mt-1">
            {errors.map((e, i) => (
              <li key={i} className="flex items-start gap-2.5 border-b border-line
                                     border-l-[3px] border-l-accent bg-tint px-2.5
                                     py-1.5 text-[11.5px] text-deep last:border-b-0">
                <AlertTriangle className="mt-px size-3.5 shrink-0 text-accent" />
                <span className="min-w-0">
                  {typeof e === 'string' ? e : (e.message || JSON.stringify(e))}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}


export function Infrastructure({ snap }) {
  const aws = snap?.aws || {}
  const vercel = snap?.vercel || {}
  if (Object.keys(aws).length) return <Aws aws={aws} />
  if (Object.keys(vercel).length) return <Vercel vercel={vercel} />
  return <Empty>No provider information in this snapshot.</Empty>
}

function Aws({ aws }) {
  return (
    <div className="space-y-4">
      <Panel className="p-4">
        <SectionLabel className="border-b-2 border-line2 pb-1.5" right={<span className="font-mono text-[10px] text-muted2">
                               {aws.region}
                             </span>}>
          Instances
        </SectionLabel>
        {(aws.instances || []).length === 0
          ? <p className="mt-2 text-[11.5px] text-muted">None reported.</p>
          : <Table className="mt-2">
              <thead><TR><TH>Instance</TH><TH>Type</TH><TH>State</TH><TH>Address</TH></TR></thead>
              <tbody>
                {aws.instances.map(i => (
                  <TR key={i.instance_id || i.id}>
                    <TD className="font-mono text-[10.5px]">{i.instance_id || i.id}</TD>
                    <TD>{i.instance_type || i.type}</TD>
                    <TD><Badge tone={(i.state === 'running') ? 'ok' : 'warn'}>{i.state}</Badge></TD>
                    <TD className="font-mono text-[10.5px] text-muted">{i.public_ip || i.ip || '—'}</TD>
                  </TR>
                ))}
              </tbody>
            </Table>}
      </Panel>

      {(aws.stacks || []).length > 0 && (
        <Panel className="p-4">
          <SectionLabel className="border-b-2 border-line2 pb-1.5">CloudFormation</SectionLabel>
          <Table className="mt-2">
            <tbody>
              {aws.stacks.map(s => (
                <TR key={s.name}>
                  <TD className="font-mono text-[10.5px]">{s.name}</TD>
                  <TD className="text-right">
                    <Badge tone={String(s.status || '').includes('COMPLETE') ? 'ok' : 'warn'}>
                      {s.status}
                    </Badge>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Panel>
      )}

      {(aws.releases || []).length > 0 && (
        <Panel className="p-4">
          <SectionLabel className="border-b-2 border-line2 pb-1.5">Releases over SSM</SectionLabel>
          <Table className="mt-2">
            <tbody>
              {aws.releases.slice(0, 8).map((r, i) => (
                <TR key={r.command_id || i}>
                  <TD className="font-mono text-[10.5px] text-muted">
                    {String(r.command_id || '').slice(0, 12)}
                  </TD>
                  <TD><Badge tone={r.status === 'Success' ? 'ok' : 'bad'}>{r.status}</Badge></TD>
                  <TD className="text-right text-[10.5px] text-muted2">{ago(r.requested_at)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Panel>
      )}
    </div>
  )
}

function Vercel({ vercel }) {
  return (
    <div className="space-y-4">
      <Panel className="p-4">
        <SectionLabel className="border-b-2 border-line2 pb-1.5" right={vercel.application_url && (
          <a href={vercel.application_url} target="_blank" rel="noreferrer"
             className="inline-flex items-center gap-1 text-[10px] text-accent hover:underline">
            open <ExternalLink className="size-2.5" />
          </a>
        )}>
          {vercel.project_name || 'Vercel project'}
        </SectionLabel>
        {(vercel.deployments || []).length === 0
          ? <p className="mt-2 text-[11.5px] text-muted">No deployments reported.</p>
          : <Table className="mt-2">
              <thead><TR><TH>Deployment</TH><TH>State</TH><TH>Commit</TH><TH>When</TH></TR></thead>
              <tbody>
                {vercel.deployments.slice(0, 10).map((d, i) => (
                  <TR key={d.uid || i}>
                    <TD className="font-mono text-[10.5px]">{String(d.uid || '').slice(0, 12)}</TD>
                    <TD>
                      <Badge tone={d.ready_state === 'READY' ? 'ok'
                                 : d.ready_state === 'ERROR' ? 'bad' : 'warn'}>
                        {d.ready_state}
                      </Badge>
                    </TD>
                    <TD className="font-mono text-[10px] text-muted2">
                      {String(d.sha || '').slice(0, 7)}
                    </TD>
                    <TD className="text-[10.5px] text-muted2">{ago(d.created_at)}</TD>
                  </TR>
                ))}
              </tbody>
            </Table>}
      </Panel>

      {(vercel.domains || []).length > 0 && (
        <Panel className="p-4">
          <SectionLabel className="border-b-2 border-line2 pb-1.5">Domains</SectionLabel>
          <ul className="mt-2 space-y-1">
            {vercel.domains.map((d, i) => (
              <li key={i} className="flex items-center gap-2 text-[11.5px]">
                <Server className="size-3 text-muted2" />
                <span className="font-mono text-[10.5px]">{d.name || d}</span>
                {d.verified === false && <Badge tone="warn">unverified</Badge>}
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  )
}


export function Logs({ snap }) {
  const lines = snap?.logs || []
  if (!lines.length) {
    return (
      <Panel className="p-4">
        <SectionLabel className="border-b-2 border-line2 pb-1.5">Logs</SectionLabel>
        <p className="mt-2 text-[11.5px] text-muted">
          No log lines yet. That is the 10 “monitoring” points the readiness
          score is missing — the agent has not seen the application write
          anything it can read back.
        </p>
      </Panel>
    )
  }

  const capped = lines.length >= 100
  const stamps = lines.map(l => Number(l.timestamp)).filter(Number.isFinite)
  const span = stamps.length > 1 ? Math.max(...stamps) - Math.min(...stamps) : 0

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px]">
      <Panel className="p-4">
        <SectionLabel className="border-b-2 border-line2 pb-1.5" right={<span className="text-[10px] text-muted2">
                               {lines.length} lines{capped ? ' (capped)' : ''}
                             </span>}>
          Application logs
        </SectionLabel>
        <pre className="mt-2 max-h-[420px] overflow-auto border border-line
                        bg-bg p-2.5 font-mono text-[10.5px] leading-relaxed text-muted">
          {lines.map((l, i) => (
            <div key={i}>
              <span className="text-muted2">{shortTime(l.timestamp)} </span>
              {l.message ?? String(l)}
            </div>
          ))}
        </pre>
      </Panel>

      <Panel className="h-fit p-4">
        <SectionLabel className="border-b-2 border-line2 pb-1.5">Monitoring</SectionLabel>
        <dl className="mt-2 space-y-1.5 text-[11.5px]">
          <div className="flex items-baseline justify-between gap-2">
            <dt className="text-muted2">Lines</dt>
            <dd className="font-mono text-[11px] text-ink">{lines.length}</dd>
          </div>
          <div className="flex items-baseline justify-between gap-2">
            <dt className="text-muted2">Span</dt>
            <dd className="font-mono text-[11px] text-ink">
              {span ? `${Math.round(span / 1000)}s` : '—'}
            </dd>
          </div>
          <div className="flex items-baseline justify-between gap-2">
            <dt className="text-muted2">Readiness</dt>
            <dd className="font-mono text-[11px] text-ok">
              {snap?.readiness?.categories?.monitoring ?? 0}/10
            </dd>
          </div>
        </dl>
        {capped && (
          <p className="mt-2 text-[10px] leading-relaxed text-muted2">
            The collector keeps the newest 100 lines. Older ones are in
            CloudWatch, not here.
          </p>
        )}
      </Panel>
    </div>
  )
}


function ago(value) {
  if (!value) return '—'
  const t = typeof value === 'number' ? value : Date.parse(value)
  if (!t || Number.isNaN(t)) return '—'
  const s = Math.max(0, Math.round((Date.now() - t) / 1000))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

function shortTime(value) {
  if (!value) return ''
  const t = typeof value === 'number' ? value : Date.parse(value)
  if (!t || Number.isNaN(t)) return ''
  return new Date(t).toLocaleTimeString()
}

export { ago }

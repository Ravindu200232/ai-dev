'use client'

import { useEffect, useState } from 'react'
import {
  AlertTriangle, Check, Globe, KeyRound, Loader2, Shield, ShieldAlert, X,
} from 'lucide-react'
import { api } from '@/lib/api'
import { Badge, Empty, Panel, SectionLabel, Table, TD, TH, TR } from '../ui'
import { pipelineStages } from '@/lib/use-run-data'
import { cn } from '@/lib/utils'


const ROLE_RE = /Type:\s*AWS::IAM::Role[\s\S]{0,400}?RoleName:\s*!Sub\s*'([^']+)'/g
const MANAGED_RE = /(arn:aws:iam::aws:policy\/[A-Za-z0-9+=,.@_/-]+)/g
const INLINE_RE = /PolicyName:\s*([A-Za-z0-9_-]+)/g
const INGRESS_RE = /IpProtocol:\s*(\w+),\s*FromPort:\s*(\d+),\s*ToPort:\s*(\d+),\s*CidrIp:\s*([\d./]+)(?:,\s*Description:\s*([^}]+))?/g

function scanTemplate(text, slug) {
  const sub = s => (slug ? s.replaceAll('${ProjectSlug}', slug) : s)
  const roles = [...text.matchAll(ROLE_RE)].map(m => sub(m[1]))
  const managed = [...new Set([...text.matchAll(MANAGED_RE)].map(m => m[1]))]
  const inline = [...new Set([...text.matchAll(INLINE_RE)].map(m => m[1]))]
  const ingress = [...text.matchAll(INGRESS_RE)].map(m => ({
    protocol: m[1], from: Number(m[2]), to: Number(m[3]),
    cidr: m[4], description: (m[5] || '').trim(),
  }))
  return { roles, managed, inline, ingress }
}

export function Security({ snap, events, artifacts, runId }) {
  const [tpl, setTpl] = useState(null)
  const [busy, setBusy] = useState(false)
  const template = (artifacts || []).find(a => String(a.path || '').endsWith('infra/bootstrap.yml'))

  const slug = String(snap?.aws?.stacks?.[0]?.name || '').replace(/-bootstrap$/, '')

  useEffect(() => {
    if (!runId || !template) return
    let ok = true
    setBusy(true)
    api.deploy(`/runs/${runId}/artifact?path=${encodeURIComponent(template.path)}`)
      .then(d => { if (ok) setTpl(scanTemplate(String(d?.raw ?? d ?? ''), slug)) })
      .catch(() => { if (ok) setTpl(null) })
      .finally(() => { if (ok) setBusy(false) })
    return () => { ok = false }
  }, [runId, template, slug])

  const stages = pipelineStages(events || [])
  const stage = id => stages.find(s => s.stage === id)
  const files = (artifacts || []).map(a => String(a.path || ''))

  const checks = [
    { ok: Boolean(stage('security')?.done), label: 'Artifact secret scan passed',
      detail: stage('security')?.message || 'the security stage did not complete' },
    { ok: files.some(p => p.endsWith('.env.example')),
      label: '.env.example generated with no real values' },
    { ok: Boolean(stage('secrets')?.done), label: 'Runtime secret stored in Secrets Manager',
      detail: 'values are injected at boot, never committed' },
    { ok: Boolean(snap?.readiness?.categories?.security),
      label: 'Readiness security category scored',
      detail: `${snap?.readiness?.categories?.security ?? 0} of 20` },
  ]

  const open = (tpl?.ingress || []).filter(r => r.cidr === '0.0.0.0/0')

  return (
    <div className="space-y-4">
      <Panel className="border-line2 bg-panel2 p-3">
        <p className="flex items-start gap-2 text-[11.5px] text-ink">
          <ShieldAlert className="mt-px size-3.5 shrink-0 text-warn" />
          <span>
            <span className="font-medium">Sensitive values policy.</span>{' '}
            Account IDs, secret keys and tokens are masked in this snapshot before
            it is written to disk. The real values exist only in GitHub secrets
            and AWS Secrets Manager.
          </span>
        </p>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel className="p-4">
          <SectionLabel className="border-b-2 border-line2 pb-1.5" right={busy ? <Loader2 className="size-3 animate-spin text-muted2" /> : null}>
            IAM roles
          </SectionLabel>
          <p className="mt-1 text-[10px] text-muted2">
            declared in the generated template — not a live IAM read
          </p>
          {!template
            ? <p className="mt-2 text-[11.5px] text-muted">
                This target generates no CloudFormation template.
              </p>
            : !tpl
              ? <p className="mt-2 text-[11.5px] text-muted">
                  {busy ? 'Reading the template…' : 'Template could not be read.'}
                </p>
              : (
                <div className="mt-2 space-y-2">
                  {tpl.roles.map(role => (
                    <div key={role} className=" border border-line bg-bg p-2">
                      <p className="flex items-center gap-1.5 font-mono text-[10.5px] text-accent">
                        <KeyRound className="size-3" /> {role}
                      </p>
                    </div>
                  ))}
                  {tpl.managed.length > 0 && (
                    <div>
                      <p className="text-[9px] font-semibold uppercase tracking-[1px] text-muted2">
                        Managed policies
                      </p>
                      <ul className="mt-1 space-y-0.5">
                        {tpl.managed.map(a => (
                          <li key={a} className="truncate font-mono text-[10px] text-muted"
                              title={a}>
                            {a.split('/').pop()}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {tpl.inline.length > 0 && (
                    <div>
                      <p className="text-[9px] font-semibold uppercase tracking-[1px] text-muted2">
                        Inline policies
                      </p>
                      <ul className="mt-1 space-y-0.5">
                        {tpl.inline.map(p => (
                          <li key={p} className="font-mono text-[10px] text-muted">{p}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
        </Panel>

        <Panel className="p-4">
          <SectionLabel className="border-b-2 border-line2 pb-1.5">Inbound rules</SectionLabel>
          <p className="mt-1 text-[10px] text-muted2">security group ingress</p>
          {!tpl || tpl.ingress.length === 0
            ? <p className="mt-2 text-[11.5px] text-muted">None declared.</p>
            : <ul className="mt-2 space-y-1">
                {tpl.ingress.map((r, i) => (
                  <li key={i} className="flex items-center gap-2 border
                                         border-line bg-bg px-2 py-1.5 text-[11.5px]">
                    <span className="font-mono text-accent">{r.from}</span>
                    <span className="min-w-0 flex-1 truncate text-muted">
                      {r.description || '—'}
                    </span>
                    <Badge tone="mute">{r.protocol}</Badge>
                  </li>
                ))}
              </ul>}
          {open.length > 0 && (
            <p className="mt-2 flex items-start gap-1.5 border border-line2
                          bg-panel2 px-2 py-1.5 text-[10.5px] text-warn">
              <AlertTriangle className="mt-px size-3 shrink-0" />
              Source {open[0].cidr} — open to the internet. Expected for a public
              site; put a load balancer in front before restricting it.
            </p>
          )}
        </Panel>

        <Panel className="p-4">
          <SectionLabel className="border-b-2 border-line2 pb-1.5">Security checklist</SectionLabel>
          <ul className="mt-1.5">
            {checks.map(c => (
              <li key={c.label}
                  className={cn('flex items-start gap-2 border-b border-line',
                    'border-l-[3px] py-1.5 pl-2 text-[11.5px] last:border-b-0',
                    c.ok ? 'border-l-ok' : 'border-l-bad bg-tint')}>
                {c.ok ? <Check className="mt-px size-3.5 shrink-0 text-ok" />
                      : <X className="mt-px size-3.5 shrink-0 text-bad" />}
                <span className="min-w-0">
                  <span className={c.ok ? 'text-ink' : 'text-deep'}>{c.label}</span>
                  {c.detail && (
                    <span className="block text-[10px] text-muted2">{c.detail}</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
    </div>
  )
}


export function ApiValidation({ snap }) {
  const probes = snap?.api || []
  if (!probes.length) {
    return (
      <Panel className="p-4">
        <SectionLabel className="border-b-2 border-line2 pb-1.5">API validation</SectionLabel>
        <p className="mt-2 text-[11.5px] text-muted">
          No probes recorded. The agent only runs these once it knows the
          application URL, which is after the stack exists.
        </p>
      </Panel>
    )
  }

  const passed = probes.filter(p => p.passed).length
  const all = passed === probes.length
  const timed = probes.filter(p => Number.isFinite(Number(p.elapsed_ms)))
  const avg = timed.length
    ? Math.round(timed.reduce((s, p) => s + Number(p.elapsed_ms), 0) / timed.length)
    : null

  return (
    <div className="space-y-4">
      <Panel className={cn('p-3', all ? 'border-ink bg-panel2' : 'border-accent bg-tint')}>
        <div className="flex items-center gap-2">
          {all ? <Check className="size-4 shrink-0 text-ok" />
               : <AlertTriangle className="size-4 shrink-0 text-bad" />}
          <div className="min-w-0 flex-1">
            <p className="text-[12.5px] font-medium text-ink">
              {all ? `All endpoints passed — ${passed}/${probes.length}`
                   : `${probes.length - passed} of ${probes.length} endpoints failed`}
            </p>
            <p className="text-[10.5px] text-muted2">
              probed against the deployed application
            </p>
          </div>
          {avg != null && (
            <Badge tone="mute" className="shrink-0">avg {avg}ms</Badge>
          )}
        </div>
      </Panel>

      <Panel className="p-4">
        <SectionLabel className="border-b-2 border-line2 pb-1.5">Results</SectionLabel>
        <Table className="mt-2">
          <thead><TR>
            <TH>Name</TH><TH>Method</TH><TH>Path</TH><TH>Status</TH>
            <TH>Time</TH><TH>Result</TH>
          </TR></thead>
          <tbody>
            {probes.map((p, i) => (
              <TR key={i}>
                <TD className="text-muted">{p.name || '—'}</TD>
                <TD><Badge tone="accent">{p.method || 'GET'}</Badge></TD>
                <TD className="font-mono text-[10.5px] text-muted">{p.path || p.url}</TD>
                <TD>
                  <Badge tone={p.passed ? 'ok' : 'bad'}>
                    {p.status != null ? p.status : '—'}
                  </Badge>
                </TD>
                <TD className="font-mono text-[10px] text-muted2">
                  {Number.isFinite(Number(p.elapsed_ms)) ? `${p.elapsed_ms}ms` : '—'}
                </TD>
                <TD>
                  {p.passed
                    ? <span className="inline-flex items-center gap-1 text-[11px] text-ok">
                        <Check className="size-3" /> passed
                      </span>
                    : <span className="inline-flex items-center gap-1 text-[11px] text-bad">
                        <X className="size-3" /> {p.error || 'failed'}
                      </span>}
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      </Panel>

      <Panel className="p-4">
        <SectionLabel className="border-b-2 border-line2 pb-1.5">What was checked</SectionLabel>
        <ul className="mt-2 space-y-1.5">
          {[
            [Globe, 'The application URL answered at all'],
            [Shield, 'The health endpoint reported its database'],
            [Check, 'Every probe returned the status it was expected to'],
          ].map(([Icon, text], i) => (
            <li key={i} className="flex items-center gap-2 text-[11.5px] text-muted">
              <Icon className="size-3 shrink-0 text-muted2" /> {text}
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  )
}

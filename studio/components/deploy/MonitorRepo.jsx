'use client'

import { useEffect, useState } from 'react'
import {
  Check, ChevronRight, ExternalLink, FileCode, FileText, GitBranch,
  FolderGit2, Loader2, X,
} from 'lucide-react'
import { api } from '@/lib/api'
import { Badge, Button, Empty, Panel, SectionLabel, Table, TD, TH, TR } from '../ui'
import { ago } from './MonitorViews'
import { size } from '@/lib/use-run-data'
import { cn } from '@/lib/utils'


const REQUIRED = [
  { label: '.github/workflows exists', test: p => p.startsWith('.github/workflows/') },
  { label: 'CI workflow', test: p => p === '.github/workflows/ci.yml' },
  { label: 'Deploy workflow', test: p => p === '.github/workflows/deploy.yml' },
  { label: '.env.example generated', test: p => p.endsWith('.env.example') },
  { label: 'Deployment manifest', test: p => p === 'deployment-manifest.json' },
  { label: 'Readiness score', test: p => p === 'readiness-score.json' },
]

export function Repository({ snap, artifacts, busy }) {
  const g = snap?.github || {}
  const runs = g.runs || []
  const files = (artifacts || []).map(a => String(a.path || ''))

  if (!g.repository && !files.length) {
    return busy
      ? <Empty>Reading the artifact list…</Empty>
      : <Empty>No repository information in this snapshot.</Empty>
  }

  const newest = runs[0] || {}
  const url = g.repository ? `https://github.com/${g.repository}` : ''

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Card Icon={FolderGit2} label="Repository"
              value={g.repository || '—'} href={url} mono />
        <Card Icon={GitBranch} label="Workflow runs" value={runs.length} />
        <Card Icon={Check} label="Passed"
              value={`${g.successful || 0} of ${(g.successful || 0) + (g.failed || 0)}`} />
        <Card Icon={FileText} label="Artifacts written" value={files.length} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
        <Panel className="p-4">
          <SectionLabel className="border-b-2 border-line2 pb-1.5">Repository checklist</SectionLabel>
          <p className="mt-1 text-[10.5px] text-muted2">
            Required deployment files
          </p>
          <ul className="mt-1.5">
            {REQUIRED.map(({ label, test }) => {
              const ok = files.some(test)
              return (
                <li key={label}
                    className={cn('flex items-start gap-2 border-b border-line',
                      'border-l-[3px] py-1.5 pl-2 text-[11.5px] last:border-b-0',
                      ok ? 'border-l-ok' : 'border-l-bad bg-tint')}>
                  {ok ? <Check className="mt-px size-3.5 shrink-0 text-ok" />
                      : <X className="mt-px size-3.5 shrink-0 text-bad" />}
                  <span className={ok ? 'text-ink' : 'text-deep'}>{label}</span>
                </li>
              )
            })}
          </ul>
        </Panel>

        <Panel className="p-4">
          <SectionLabel className="border-b-2 border-line2 pb-1.5" right={<span className="text-[10px] text-muted2">
                                 {files.length} files
                               </span>}>
            Files the agent wrote
          </SectionLabel>
          {files.length === 0
            ? <p className="mt-2 text-[11.5px] text-muted">None recorded.</p>
            : <Tree artifacts={artifacts} />}
          {newest.updatedAt && (
            <p className="mt-3 text-[10.5px] text-muted2">
              Last workflow run {ago(newest.updatedAt || newest.createdAt)}
            </p>
          )}
        </Panel>
      </div>
    </div>
  )
}


function Tree({ artifacts }) {
  const groups = new Map()
  for (const a of artifacts) {
    const path = String(a.path || '')
    const cut = path.lastIndexOf('/')
    const dir = cut === -1 ? '' : path.slice(0, cut)
    if (!groups.has(dir)) groups.set(dir, [])
    groups.get(dir).push({ ...a, name: cut === -1 ? path : path.slice(cut + 1) })
  }
  const dirs = [...groups.keys()].sort((a, b) => (a === '' ? -1 : b === '' ? 1 : a.localeCompare(b)))
  return (
    <div className="mt-2 space-y-2">
      {dirs.map(dir => (
        <div key={dir || '/'}>
          <p className="flex items-center gap-1 font-mono text-[10px] text-muted2">
            <ChevronRight className="size-2.5" />
            {dir || '(project root)'}
          </p>
          <ul className="mt-0.5 space-y-0.5 pl-4">
            {groups.get(dir).map(a => (
              <li key={a.path} className="flex items-baseline gap-1.5 font-mono text-[10.5px]">
                <FileCode className="size-2.5 shrink-0 translate-y-0.5 text-muted2" />
                <span className="min-w-0 flex-1 truncate text-muted">{a.name}</span>
                <span className="shrink-0 text-[10px] text-muted2">{size(a.size)}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

function Card({ Icon, label, value, href, mono }) {
  const body = (
    <>
      <span className="flex items-center gap-1.5 text-[9px] font-semibold uppercase
                       tracking-[1px] text-muted2">
        <Icon className="size-3" /> {label}
      </span>
      <span className={cn('mt-1 block truncate text-[12.5px] text-ink',
                          mono && 'font-mono text-[11.5px]')}
            title={String(value)}>
        {value}
      </span>
    </>
  )
  return (
    <Panel className="p-3">
      {href
        ? <a href={href} target="_blank" rel="noreferrer" className="block hover:opacity-80">
            {body}
          </a>
        : body}
    </Panel>
  )
}


export function CiCd({ snap, artifacts, runId }) {
  const g = snap?.github || {}
  const runs = g.runs || []
  const here = snap?.workflow || {}
  const workflows = (artifacts || [])
    .filter(a => String(a.path || '').startsWith('.github/workflows/'))

  if (!runs.length && !workflows.length) {
    return <Empty>No workflow information in this snapshot.</Empty>
  }

  return (
    <div className="space-y-4">
      <Panel className="p-4">
        <SectionLabel className="border-b-2 border-line2 pb-1.5" right={<span className="text-[10px] text-muted2">
                               {g.successful || 0} passed · {g.failed || 0} failed
                             </span>}>
          GitHub Actions
        </SectionLabel>
        {g.error && <p className="mt-2 text-[11px] text-bad">{g.error}</p>}
        {runs.length === 0
          ? <p className="mt-2 text-[11.5px] text-muted">
              No runs reported yet for {g.repository || 'this repository'}.
            </p>
          : <Table className="mt-2">
              <thead><TR>
                <TH>Workflow</TH><TH>Status</TH><TH>Commit</TH><TH>Last run</TH>
              </TR></thead>
              <tbody>
                {runs.slice(0, 12).map(r => {
                  const mine = here.databaseId && r.databaseId === here.databaseId
                  const ok = r.conclusion === 'success'
                  return (
                    <TR key={r.databaseId} className={mine ? 'bg-panel2' : ''}>
                      <TD>
                        {r.url
                          ? <a href={r.url} target="_blank" rel="noreferrer"
                               className="inline-flex items-center gap-1 text-accent hover:underline">
                              {r.name} <ExternalLink className="size-2.5" />
                            </a>
                          : r.name}
                        {mine && <Badge tone="accent" className="ml-1.5">this deployment</Badge>}
                      </TD>
                      <TD>
                        <Badge tone={ok ? 'ok' : r.conclusion ? 'bad' : 'warn'}>
                          {r.conclusion || r.status || '—'}
                        </Badge>
                      </TD>
                      <TD className="font-mono text-[10px] text-muted2">
                        {String(r.headSha || '').slice(0, 7)}
                      </TD>
                      <TD className="text-[10.5px] text-muted2">
                        {ago(r.updatedAt || r.createdAt)}
                      </TD>
                    </TR>
                  )
                })}
              </tbody>
            </Table>}
      </Panel>

      {workflows.length > 0 && (
        <WorkflowSource runId={runId} workflows={workflows} />
      )}
    </div>
  )
}


function WorkflowSource({ runId, workflows }) {
  const [pick, setPick] = useState(workflows[0]?.path || '')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!runId || !pick) return
    let ok = true
    setBusy(true)
    setError('')
    setText('')
    api.deploy(`/runs/${runId}/artifact?path=${encodeURIComponent(pick)}`)
      .then(d => {
        if (!ok) return

        setText(typeof d === 'string' ? d : String(d?.raw ?? ''))
      })
      .catch(e => { if (ok) setError(String(e.message || e)) })
      .finally(() => { if (ok) setBusy(false) })
    return () => { ok = false }
  }, [runId, pick])

  return (
    <Panel className="p-4">
      <SectionLabel className="border-b-2 border-line2 pb-1.5" right={
        <div className="flex gap-1">
          {workflows.map(w => {
            const name = w.path.split('/').pop()
            return (
              <Button key={w.path} size="sm"
                      variant={pick === w.path ? 'subtle' : 'ghost'}
                      onClick={() => setPick(w.path)}>
                {name}
              </Button>
            )
          })}
        </div>
      }>
        Generated workflow
      </SectionLabel>
      {error && <p className="mt-2 text-[11px] text-bad">{error}</p>}
      {busy
        ? <p className="mt-3 flex items-center gap-1.5 text-[11.5px] text-muted">
            <Loader2 className="size-3 animate-spin" /> reading {pick}
          </p>
        : text
          ? <pre className="mt-2 max-h-[360px] overflow-auto border border-line
                            bg-bg p-2.5 font-mono text-[10.5px] leading-relaxed text-muted">
              {text}
            </pre>
          : !error && <p className="mt-2 text-[11.5px] text-muted">Nothing to show.</p>}
    </Panel>
  )
}

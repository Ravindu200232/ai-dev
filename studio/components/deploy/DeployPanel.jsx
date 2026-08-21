'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Activity, Camera, Check, FolderGit2, GitBranch, Globe,
         Loader2, RefreshCw, Rocket, ScrollText, Server, Settings2, Shield,
         Trash2, Workflow, X } from 'lucide-react'
import { useStore } from '@/lib/store'
import { api } from '@/lib/api'
import { TARGETS, TERMINAL } from '@/lib/deploy-constants'
import { useMonitor, providerOf } from '@/lib/use-monitor'
import { Button, Empty, SectionLabel, SubTab, SubTabs } from '../ui'
import { cn } from '@/lib/utils'
import DeployProgress from './DeployProgress'
import DeployResult from './DeployResult'
import { Infrastructure, Logs, Overview, StatusBar, ago } from './MonitorViews'
import { Pipeline } from './MonitorPipeline'
import { CiCd, Repository } from './MonitorRepo'
import { ApiValidation, Security } from './MonitorSecurity'
import { Evidence } from './MonitorEvidence'
import { MonitorConsole } from './MonitorConsole'
import { DeployDanger } from './DeployDanger'
import { useRunData } from '@/lib/use-run-data'


export default function DeployPanel({ onSettings }) {
  const project = useStore(s => s.project)
  const models = useStore(s => s.models)
  const tests = useStore(s => s.tests)
  const qa = useStore(s => s.qaReport)
  const setQa = useStore(s => s.setQaReport)
  const addLog = useStore(s => s.addLog)

  const [data, setData] = useState(null)
  const [probe, setProbe] = useState(null)
  const [target, setTarget] = useState('vercel')
  const [validateBuild, setValidateBuild] = useState(true)
  const [override, setOverride] = useState(false)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const [view, setView] = useState('deploy')
  const alive = useRef(true)

  const mine = data?.project === project ? data : null
  const live = mine?.live
  const running = Boolean(live && !TERMINAL.has(live.state) && !live.error)

  const runId = live?.run_id || mine?.last?.run_id || ''
  const monitor = useMonitor(runId, {
    target: live?.target || mine?.last?.target || 'vercel',
    state: live?.state || mine?.last?.state || '',
    active: true,
    frozen: mine?.last?.monitor,
  })

  const run = useRunData(runId)

  const refresh = useCallback(async () => {
    if (!project) return
    try {
      const d = await api.deployResults(project)
      if (alive.current) setData(d)
    } catch (e) {
      if (alive.current) setError(e.message)
    }
  }, [project])

  useEffect(() => {
    alive.current = true
    refresh()
    return () => { alive.current = false }
  }, [refresh])

  // `deployResults` carries the run's state, and it was read once on mount.
  // A teardown is asynchronous — the API accepts it and CloudFormation spends
  // minutes actually deleting — so the refresh fired by the delete button ran
  // while the run was still LIVE, and nothing read it again. The panel went on
  // showing "LIVE 100%" over a green pipeline for a stack that no longer
  // existed, with the site's old address still offered as a link.
  //
  // The monitor already polls on its own cadence; this rides along rather than
  // starting a second timer, and stops when the run reaches a terminal state.
  // LIVE is not one of the states to stop on, even though TERMINAL lists it:
  // a live deployment is exactly the one that can still be torn down, and
  // treating it as finished is what left the panel describing a stack that
  // had been deleted. Only the states nothing can follow end the polling.
  const FINISHED = ['DESTROYED', 'CANCELLED', 'FAILED', 'ROLLED_BACK']
  const snapAt = monitor.at
  useEffect(() => {
    if (!snapAt) return
    const state = live?.state || mine?.last?.state || ''
    if (state && FINISHED.includes(state)) return
    refresh()
  }, [snapAt])

  useEffect(() => {
    if (!project || qa?.project === project) return
    let ok = true
    api.qa(project).then(d => { if (ok) setQa(d) }).catch(() => { })
    return () => { ok = false }
  }, [project, qa, setQa])

  useEffect(() => {
    if (view === 'deploy') return
    if (!viewsFor(monitor.snap).some(v => v.id === view)) setView('deploy')
  }, [view, monitor.snap])

  useEffect(() => {
    if (!running) return
    const t = setInterval(refresh, 1400)
    return () => clearInterval(t)
  }, [running, refresh])

  const reloadRun = run.reload
  useEffect(() => {
    if (!running) return
    const t = setInterval(reloadRun, 2500)
    return () => clearInterval(t)
  }, [running, reloadRun])

  useEffect(() => {
    let ok = true
    Promise.all([
      api.deployRead('/onboarding/status').catch(() => ({ error: true })),
      api.deploy('/aws/vercel/status', { token: '' }).catch(() => ({})),
    ]).then(([status, vercel]) => {
      if (ok) setProbe({
        ...status,
        vercel_connected: Boolean(vercel?.connected),
        vercel_source: vercel?.source || '',
        vercel_user: vercel?.username || vercel?.email || vercel?.name || '',
      })
    })
    return () => { ok = false }
  }, [])

  const s = mine?.settings || {}
  const unit = qa?.project === project ? qa.vitest : null

  const failures = tests.rows.length ? tests.fail
    : unit ? (unit.numFailedTests || 0) : null
  const tested = tests.rows.length > 0 || Boolean(unit)
  const green = tested && failures === 0

  const needs = [
    { id: 'github', label:'GitHub',
      ok: Boolean(probe?.github_authenticated),
      unknown: !probe,
      hint: probe?.github_account ? `signed in as ${probe.github_account}`
                                  : 'sign in from Settings — the deploy pushes a repo' },
    target === 'vercel'
      ? { id: 'vercel', label:'Vercel',
          ok: Boolean(s.vercel_token_set) || Boolean(probe?.vercel_connected),
          unknown: !probe && !s.vercel_token_set,

          hint: probe?.vercel_connected
            ? `signed in as ${probe.vercel_user || 'the Vercel CLI'}`
              + (probe.vercel_source ? ` (${probe.vercel_source})` : '')
            : s.vercel_token_set ? `token saved (${s.vercel_token_hint})`
                                 : 'add a token in Settings' }
      : { id: 'aws', label:'AWS',
          ok: Boolean(s.aws_profile),
          unknown: false,
          hint: s.aws_profile ? `profile ${s.aws_profile} · ${s.aws_region || 'ap-south-1'}`
                              : 'sign in to AWS from Settings' },
    { id: 'mongo', label:'MongoDB',
      ok: Boolean(s.mongodb_uri_set),
      unknown: false,
      hint: s.mongodb_uri_set ? `saved (${s.mongodb_uri_hint})`
                              : 'the deployed app needs a database it can reach'
                                + 'from the internet — set one in Settings' },
  ]
  const ready = needs.every(n => n.ok) && (green || override)

  async function deploy() {
    setStarting(true)
    setError('')
    try {

      await api.saveSettings({ deploy_model: models.deploy || models.agent || '' })
        .catch(() => { })
      await api.deployStart({ project, target, validate_container: validateBuild })
      addLog('INFO', `Deploying ${project} to ${target === 'vercel' ? 'Vercel' : 'AWS EC2'}`)
      await refresh()
    } catch (e) {
      setError(e.message)
    }
    setStarting(false)
  }

  if (!project) {
    return <div className="min-h-0 flex-1 overflow-auto p-5">
             <Empty>Open a project first.</Empty>
           </div>
  }

  const agent = mine?.agent
  if (agent && !agent.listening) {
    return (
      <div className="min-h-0 flex-1 overflow-auto p-5">
        <div className="rounded-[20px] border border-accent/20 bg-accent/[.055] p-4 shadow-sm">
          <p className="flex items-start gap-2.5 text-[12.5px] text-deep">
            <AlertTriangle className="mt-px size-4 shrink-0 text-accent" />
            <span>
              The deployment agent is not running — {agent.error || agent.state}.
              <span className="mt-1 block text-[11px] text-muted">
                Everything else in AgentForge still works; only this tab needs it.
              </span>
            </span>
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto bg-[radial-gradient(circle_at_top_right,rgba(93,106,251,.08),transparent_30%)]">
      <MonitorBar view={view} setView={setView} monitor={monitor}
                  hasSnapshot={Boolean(monitor.snap)}
                  runId={runId} running={running}
                  state={live?.state || mine?.last?.state || ''}
                  onDone={() => { refresh(); run.reload() }} />

      {view !== 'deploy' && monitor.snap && (
        <StatusBar snap={monitor.snap}
                   state={live?.state || mine?.last?.state || ''} />
      )}

      <div className="mx-auto max-w-[1180px] space-y-4 p-5">
      {view !== 'deploy' ? (
        <MonitorPane view={view} monitor={monitor} runId={runId} run={run}
                     state={live?.state || mine?.last?.state || ''} />
      ) : (<>
      {mine?.deleted && !live && (
        <div className="rounded-[20px] border border-line/80 bg-white/50 p-4 shadow-sm dark:bg-white/[.025]">
          <div className="flex items-start gap-2.5">
            <Trash2 className="mt-px size-4 shrink-0 text-muted2" />
            <div className="min-w-0">
              <p className="text-[12.5px] font-extrabold text-ink">
                This deployment was deleted
              </p>
              <p className="mt-0.5 text-[11.5px] text-muted">
                Its cloud resources were destroyed{mine.deleted.deleted_at
                  ? ` ${ago(mine.deleted.deleted_at)}` : ''}. The record and its
                evidence were kept in the project, under{' '}
                <span className="font-mono text-[10.5px]">
                  .agentforge/deploy-archive/{mine.deleted.archive || ''}
                </span>.
              </p>
            </div>
          </div>
        </div>
      )}

      {running || live ? <DeployProgress run={live} /> : null}

      {!running && (
        <div className="rounded-[22px] border border-line/80 bg-white/58 p-5 shadow-sm dark:bg-white/[.025]">
          <SectionLabel>Where should it go?</SectionLabel>
          <p className="mt-1 text-[11px] text-muted">Choose the cloud destination for this reviewed build.</p>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {TARGETS.map(t => (
              <button key={t.id} onClick={() => setTarget(t.id)}
                      className={cn('rounded-[18px] border p-4 text-left shadow-sm transition-all',
                        target === t.id
                          ? 'border-accent/35 bg-accent/[.07] ring-2 ring-accent/10'
                          : 'border-line/80 bg-white/55 hover:-translate-y-px hover:bg-white dark:bg-white/[.025]')}>
                <span className="flex items-center gap-2 text-[12.5px] font-extrabold text-ink">
                  <span className={cn('grid size-4 place-items-center rounded-full border',
                    target === t.id ? 'border-accent bg-accent' : 'border-line2 bg-white/70 dark:bg-white/5')}>
                    {target === t.id && <Check className="size-2.5 text-white" />}
                  </span>
                  {t.label}
                </span>
                <span className="mt-1 block text-[10.5px] leading-snug text-muted">
                  {t.blurb}
                </span>
              </button>
            ))}
          </div>

          <SectionLabel className="mt-6"
                        right={onSettings && (
                          <Button variant="outline" size="sm" onClick={onSettings}>
                            <Settings2 className="size-3" /> Settings
                          </Button>
                        )}>
            Accounts
          </SectionLabel>
          <ul className="mt-3 grid gap-2 sm:grid-cols-2">
            {needs.map(n => (
              <li key={n.id} className={cn('flex items-start gap-2.5 rounded-2xl border px-3 py-3',
                n.unknown ? 'border-line bg-white/45 dark:bg-white/[.02]'
                  : n.ok ? 'border-ok/20 bg-ok/[.055]' : 'border-bad/20 bg-bad/[.045]')}>
                <span className="mt-[2px] grid size-3.5 shrink-0 place-items-center">
                  {n.unknown
                    ? <Loader2 className="size-3 animate-spin text-muted2" />
                    : n.ok ? <Check className="size-3.5 text-ok" />
                           : <X className="size-3.5 text-bad" />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="text-[12px] font-semibold text-ink">{n.label}</span>
                  <span className={cn('ml-2 text-[10.5px]',
                                      n.unknown || n.ok ? 'text-muted' : 'text-deep')}>
                    {n.hint}
                  </span>
                </span>
              </li>
            ))}
          </ul>

          {!green && (
            <label className={cn('mt-4 flex cursor-pointer items-start gap-2.5 rounded-2xl border px-3 py-3 text-[11.5px]',
              override ? 'border-accent/25 bg-accent/[.055] text-deep'
                       : 'border-line bg-panel2/70 text-muted')}>
              <input type="checkbox" checked={override} className="mt-0.5"
                     onChange={e => setOverride(e.target.checked)} />
              <span>
                {!tested
                  ? 'This project has not been tested in this session. Deploy anyway.'
                  : `${failures} unit test${failures === 1 ? '' : 's'} failing. Deploy anyway.`}
              </span>
            </label>
          )}
          {green && (
            <p className="mt-4 flex items-center gap-2.5 rounded-2xl border border-ok/20 bg-ok/[.055] px-3 py-3 text-[11.5px] text-ok">
              <Check className="size-3.5" />
              Every unit test passes.
            </p>
          )}

          <label className="mt-3 flex cursor-pointer items-start gap-2.5 text-[11.5px] text-muted">
            <input type="checkbox" checked={validateBuild} className="mt-0.5"
                   onChange={e => setValidateBuild(e.target.checked)} />
            <span>
              Run a production build first.
              <span className="ml-1 text-muted2">
                Slower, and it catches what only fails in a real build.
                {' '}Turning it off also stops the agent from ever scoring the
                deployment as healthy.
              </span>
            </span>
          </label>

          <footer className="mt-5 flex items-center gap-3 border-t border-line/70 pt-4">
            <Button variant="solid" size="lg" disabled={!ready || starting}
                    onClick={deploy}>
              {starting ? <Loader2 className="size-3.5 animate-spin" />
                        : <Rocket className="size-3.5" />}
              Deploy to {target === 'vercel' ? 'Vercel' : 'AWS EC2'}
            </Button>
            {!ready && !starting && (
              <span className="text-[11px] text-muted2">
                {needs.find(n => !n.ok)
                  ? `${needs.find(n => !n.ok).label} is not connected yet`
                  : 'confirm you want to deploy a failing build'}
              </span>
            )}
          </footer>

          {error && (
            <p className="mt-3 flex items-start gap-2.5 rounded-2xl border border-bad/20 bg-bad/[.045] px-3 py-3 text-[11.5px] text-bad">
              <AlertTriangle className="mt-px size-3.5 shrink-0 text-accent" /> {error}
            </p>
          )}
        </div>
      )}

      <DeployResult data={mine} />
      </>)}
      </div>

      {run.events.length > 0 && (
        <MonitorConsole events={run.events} snapErrors={monitor.snap?.errors} />
      )}
    </div>
  )
}


const MONITOR_VIEWS = [
  { id: 'deploy', label:'Deploy', Icon: Rocket },
  { id: 'overview', label:'Overview', Icon: Activity },
  { id: 'pipeline', label:'Pipeline', Icon: Workflow },
  { id: 'repository', label:'Repository', Icon: FolderGit2 },
  { id: 'cicd', label:'CI/CD', Icon: GitBranch },
  { id: 'infra', label:'Infrastructure', Icon: Server },
  { id: 'security', label:'IAM & Security', Icon: Shield, aws: true },
  { id: 'logs', label:'Logs', Icon: ScrollText },
  { id: 'api', label:'API Validation', Icon: Globe },
  { id: 'evidence', label:'Evidence', Icon: Camera },
]


function viewsFor(snap) {
  const isAws = Boolean(snap && Object.keys(snap.aws || {}).length)
  return MONITOR_VIEWS.filter(v => !v.aws || isAws)
}

function MonitorBar({ view, setView, monitor, hasSnapshot, runId, running,
                     state, onDone }) {
  const shown = hasSnapshot ? viewsFor(monitor.snap) : MONITOR_VIEWS.slice(0, 1)

  if (shown.length === 1 && !running) return null
  return (
    <SubTabs className="sticky top-0 z-10">
      {shown.map(({ id, label, Icon }) => (
        <SubTab key={id} on={view === id} onClick={() => setView(id)}>
          <Icon className="size-3" /> {label}
        </SubTab>
      ))}
      <span className="flex-1" />
      <span className="hidden shrink-0 items-center px-3 font-mono text-[10px]
                       text-muted2 sm:flex">
        {monitor.live ? (monitor.at ? `updated ${ago(monitor.at)}` : 'live')
                      : 'from the last deployment'}
      </span>
      <span className="flex shrink-0 items-center gap-2 px-3">
        <Button variant="outline" size="sm"
                disabled={monitor.busy || !monitor.canRefresh}
                onClick={monitor.refresh}
                title="Ask the deployment agent for a fresh snapshot — this takes around 25 seconds">
          {monitor.busy ? <Loader2 className="size-3 animate-spin" />
                        : <RefreshCw className="size-3" />}
          Refresh
        </Button>
        <DeployDanger runId={runId} state={state} running={running} onDone={onDone} />
      </span>
    </SubTabs>
  )
}

function MonitorPane({ view, monitor, runId, state, run }) {
  const snap = monitor.snap
  if (!snap) return <Empty>Nothing to show until this project has been deployed.</Empty>
  return (
    <div className="space-y-4">
      {monitor.error && (
        <p className="flex items-start gap-2.5 rounded-2xl border border-bad/20 bg-bad/[.045] px-3 py-3 text-[11.5px] text-bad">
          <AlertTriangle className="mt-px size-3.5 shrink-0 text-accent" /> {monitor.error}
        </p>
      )}
      {view === 'overview' && <Overview snap={snap} />}
      {view === 'pipeline' && (
        <Pipeline events={run.events} artifacts={run.artifacts} busy={run.busy} />
      )}
      {view === 'repository' && (
        <Repository snap={snap} artifacts={run.artifacts} busy={run.busy} />
      )}
      {view === 'cicd' && (
        <CiCd snap={snap} artifacts={run.artifacts} runId={runId} />
      )}
      {view === 'infra' && <Infrastructure snap={snap} />}
      {view === 'security' && (
        <Security snap={snap} events={run.events} artifacts={run.artifacts} runId={runId} />
      )}
      {view === 'logs' && <Logs snap={snap} />}
      {view === 'api' && <ApiValidation snap={snap} />}
      {view === 'evidence' && (
        <Evidence evidence={run.evidence} runId={runId} busy={run.busy} />
      )}
    </div>
  )
}

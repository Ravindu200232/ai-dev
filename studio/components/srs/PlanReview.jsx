'use client'

import { useEffect, useMemo, useState } from 'react'
import {
  ArrowLeft, Check, ChevronDown, Loader2, MessageCircleMore, PencilLine,
  Route, ShieldCheck, Sparkles, UsersRound, Workflow,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useStore } from '@/lib/store'
import { Button, TextArea } from '../ui'
import { Waiting } from './Interview'
import { cn } from '@/lib/utils'

export default function PlanReview({ projectId, onGenerated, onCancel }) {
  const addLog = useStore(s => s.addLog)
  const [state, setState] = useState(null)
  const [phase, setPhase] = useState('loading')
  const [revision, setRevision] = useState('')
  const [error, setError] = useState('')
  const [waited, setWaited] = useState(0)
  const [full, setFull] = useState(false)
  const [hasSrs, setHasSrs] = useState(false)
  const [replies, setReplies] = useState({})
  const [dirty, setDirty] = useState(false)

  async function generate(text) {
    setPhase(text ? 'revising' : 'loading')
    setError('')
    try {
      setState(await api.srs(`/projects/${projectId}/plan`, { revision: text || '' }))
      setRevision('')
      setReplies({})
      if (text) setDirty(true)
      setPhase('ready')
    } catch (e) {
      setError(e.message)
      setPhase('ready')
    }
  }

  useEffect(() => {
    let live = true
    ;(async () => {
      setPhase('loading')
      try {
        const existing = await api.srs(`/projects/${projectId}/plan`)
        if (!live) return
        if (existing?.plan) {
          setState(existing)
          setPhase('ready')
          return
        }
      } catch { }
      if (live) await generate('')
    })()
    return () => { live = false }
  }, [projectId])

  useEffect(() => {
    if (phase !== 'generating') return
    const id = setInterval(() => setWaited(v => v + 1), 1000)
    return () => clearInterval(id)
  }, [phase])

  useEffect(() => {
    let live = true
    api.srs(`/projects/${projectId}`).then(d => { if (live) setHasSrs(Boolean(d?.srs)) }).catch(() => { })
    return () => { live = false }
  }, [projectId])

  async function accept() {
    if (hasSrs && !dirty) return onGenerated?.(projectId)
    setPhase('generating')
    setWaited(0)
    setError('')
    try {
      await api.srs(`/projects/${projectId}/plan/approve`, {})
      await api.srs(`/projects/${projectId}/generate-srs`, {})
      addLog('INFO', 'SRS written — read it before anything is built')
      onGenerated?.(projectId)
    } catch (e) {
      setError(e.message)
      setPhase('ready')
    }
  }

  if (phase === 'loading' && !state) return <Waiting sub="Turning the interview into a clear plan you can approve.">Writing the plan…</Waiting>
  if (phase === 'generating') return <Waiting sub={`Writing the specification and diagrams — ${waited}s so far.`}>Building the SRS…</Waiting>

  const body = state?.plan || {}
  const open = (body.open_questions || []).filter(q => String(q?.question || '').trim())
  const blocking = open.filter(q => q.required)
  const answeredCount = open.filter(q => (replies[q.question] || '').trim()).length
  const screens = body.screens || []
  const users = body.users || []
  const records = body.records || []
  const workflows = body.workflows || []
  const features = body.features || []
  const assumptions = body.assumptions || []
  const settled = state?.approved || state?.can_approve
  const why = state?.reason || ''

  const stat = [
    { label: 'screens', value: screens.length },
    { label: 'roles', value: users.length },
    { label: 'workflows', value: workflows.length },
    { label: 'features', value: features.length },
  ]

  return (
    <div className="mx-auto w-full max-w-[1040px] pb-8">
      <div className="mb-6 flex items-center gap-3">
        <button onClick={onCancel} className="grid size-9 place-items-center rounded-full text-muted transition hover:bg-black/[.05] hover:text-ink dark:hover:bg-white/[.06]"><ArrowLeft className="size-4" /></button>
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[.18em] text-accent">Plan review</p>
          <p className="mt-0.5 text-[13px] text-muted">Check the structure before the specification is written.</p>
        </div>
        <span className="flex-1" />
        {state?.version != null && <span className="rounded-full bg-black/[.045] px-3 py-1.5 text-[10px] font-medium text-muted dark:bg-white/[.06]">Version {state.version}{(state.versions || []).length > 1 ? ` of ${state.versions.length}` : ''}</span>}
      </div>

      <section className="overflow-hidden rounded-[32px] bg-[linear-gradient(135deg,#161d2b_0%,#252d45_58%,#5e6af7_145%)] px-7 py-7 text-white shadow-[0_28px_70px_rgba(15,23,42,.2)]">
        <div className="flex flex-wrap items-start gap-6">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[.18em] text-white/45"><Sparkles className="size-3.5" /> Product blueprint</div>
            <h2 className="mt-3 text-[32px] font-semibold tracking-[-.04em]">{body.app_name || 'Your app'}</h2>
            <p className="mt-2 max-w-[680px] text-[13px] leading-relaxed text-white/64">{body.product_intent || 'Your interview has been turned into an implementation plan.'}</p>
            {body.customer_notes && <p className="mt-4 max-w-[680px] text-[12px] italic leading-relaxed text-white/48">“{body.customer_notes}”</p>}
          </div>
          <div className="grid grid-cols-2 gap-x-7 gap-y-4 rounded-[24px] bg-white/[.07] px-5 py-4 ring-1 ring-white/10 backdrop-blur-xl">
            {stat.map(x => <div key={x.label}><div className="text-[22px] font-semibold tabular-nums">{x.value}</div><div className="text-[9.5px] uppercase tracking-[.14em] text-white/40">{x.label}</div></div>)}
          </div>
        </div>
      </section>

      <div className="mt-5 grid gap-4 lg:grid-cols-2">
        {users.length > 0 && <Section icon={UsersRound} title="Who uses it" subtitle="Roles and what each person can do">
          <div className="space-y-2.5">{users.map((u, i) => <div key={i} className="rounded-[18px] bg-black/[.025] px-4 py-3 dark:bg-white/[.035]"><p className="text-[12px] font-semibold text-ink">{u.role || u.name || String(u)}</p>{(u.can_do || []).length > 0 && <p className="mt-1 text-[11px] leading-relaxed text-muted">{u.can_do.join(' · ')}</p>}</div>)}</div>
        </Section>}

        {screens.length > 0 && <Section icon={Route} title="App spaces" subtitle="Pages and who can reach them">
          <div className="space-y-2">{screens.map((sc, i) => <div key={i} className="flex items-start gap-3 rounded-[18px] px-2 py-2.5 transition hover:bg-black/[.025] dark:hover:bg-white/[.035]"><span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-accent/10 text-[10px] font-semibold text-accent">{i + 1}</span><div className="min-w-0"><p className="text-[12px] font-semibold text-ink">{sc.name}</p><p className="mt-0.5 text-[10.8px] leading-relaxed text-muted">{sc.purpose || 'App page'}{(sc.who || []).length ? ` · ${(sc.who || []).join(', ')}` : ''}</p></div></div>)}</div>
        </Section>}

        {workflows.length > 0 && <Section icon={Workflow} title="User journeys" subtitle="How the important work moves through the app">
          <div className="space-y-3">{workflows.map((w, i) => <div key={i} className="rounded-[18px] bg-black/[.025] px-4 py-3 dark:bg-white/[.035]"><p className="text-[12px] font-semibold text-ink">{w.name}</p><ol className="mt-2 space-y-1.5 text-[10.8px] leading-relaxed text-muted">{(w.steps || []).map((step, j) => <li key={j} className="flex gap-2"><span className="text-accent">{j + 1}.</span><span>{step}</span></li>)}</ol></div>)}</div>
        </Section>}

        {features.length > 0 && <Section icon={ShieldCheck} title="Feature promise" subtitle="Everything the finished app must actually do">
          <div className="space-y-2">{features.map((f, i) => <div key={i} className="flex gap-3 rounded-[16px] px-2 py-2.5"><span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-ok-tint text-ok"><Check className="size-3" /></span><p className="text-[11.5px] leading-relaxed text-ink">{f}</p></div>)}</div>
        </Section>}
      </div>

      {(records.length > 0 || body.look_and_feel || assumptions.length > 0) && (
        <section className="mt-4 rounded-[28px] bg-white/62 p-5 shadow-[0_14px_40px_rgba(30,41,59,.06)] ring-1 ring-line/70 backdrop-blur-xl dark:bg-white/[.03]">
          <div className="grid gap-5 md:grid-cols-3">
            {records.length > 0 && <Mini title="Data"><p className="space-y-1 text-[11px] leading-relaxed text-muted">{records.map((r, i) => <span key={i} className="block"><b className="font-semibold text-ink">{r.name}</b>{(r.keeps || []).length ? ` — ${r.keeps.join(', ')}` : ''}</span>)}</p></Mini>}
            {body.look_and_feel && <Mini title="Look & feel"><p className="text-[11px] leading-relaxed text-muted">{body.look_and_feel}</p></Mini>}
            {assumptions.length > 0 && <Mini title="Assumptions"><ul className="space-y-1 text-[11px] leading-relaxed text-muted">{assumptions.map((a, i) => <li key={i}>• {a}</li>)}</ul></Mini>}
          </div>
        </section>
      )}

      {state?.markdown && (
        <div className="mt-4 rounded-[22px] bg-black/[.028] p-1 dark:bg-white/[.025]">
          <button onClick={() => setFull(v => !v)} className="flex w-full items-center gap-2 rounded-[18px] px-4 py-3 text-[11px] font-semibold text-muted transition hover:bg-white/70 hover:text-ink dark:hover:bg-white/[.04]"><ChevronDown className={cn('size-3.5 transition-transform', !full && '-rotate-90')} /> Full technical plan</button>
          {full && <pre className="max-h-[380px] overflow-auto whitespace-pre-wrap break-words px-4 pb-4 font-mono text-[10.5px] leading-[1.7] text-ink">{state.markdown}</pre>}
        </div>
      )}

      {open.length > 0 && (
        <section className="mt-5 rounded-[30px] bg-[linear-gradient(180deg,rgba(93,106,251,.075),rgba(255,255,255,.5))] p-5 ring-1 ring-accent/10 dark:bg-white/[.03]">
          <div className="flex items-start gap-3"><span className="grid size-9 place-items-center rounded-full bg-accent text-white"><MessageCircleMore className="size-4" /></span><div><p className="text-[14px] font-semibold text-ink">{blocking.length ? `${blocking.length} details still matter` : 'A few optional details'}</p><p className="mt-0.5 text-[11px] text-muted">Answer them like a conversation. The planner rewrites only the relevant part.</p></div></div>
          <div className="mx-auto mt-5 max-w-[760px] space-y-5">
            {open.map((q, i) => <div key={q.question || i}>
              <div className="flex items-end gap-2.5"><span className="grid size-7 place-items-center rounded-full bg-accent/10 text-[9px] font-bold text-accent">AF</span><div className="max-w-[78%] rounded-[20px] rounded-bl-[7px] bg-white/85 px-4 py-3 text-[12px] leading-relaxed text-ink shadow-sm ring-1 ring-line/60 dark:bg-white/[.045]">{q.question}</div></div>
              {(q.options || []).length > 0 && <div className="ml-10 mt-2 flex flex-wrap gap-2">{q.options.map(opt => { const on = (replies[q.question] || '').trim() === opt; return <button key={opt} disabled={phase === 'revising'} onClick={() => setReplies(r => ({ ...r, [q.question]: on ? '' : opt }))} className={cn('rounded-full px-3.5 py-1.5 text-[11px] font-medium ring-1 transition', on ? 'bg-accent text-white ring-accent' : 'bg-white/75 text-ink ring-line hover:ring-accent/30 dark:bg-white/[.04]')}>{opt}</button> })}</div>}
              <div className="ml-auto mt-2 max-w-[72%] rounded-[20px] rounded-br-[7px] bg-white/75 px-3 py-2 shadow-sm ring-1 ring-line/70 dark:bg-white/[.04]"><TextArea rows={2} value={replies[q.question] || ''} placeholder="Your answer…" disabled={phase === 'revising'} onChange={e => setReplies(r => ({ ...r, [q.question]: e.target.value }))} className="w-full resize-none bg-transparent text-[11.5px] leading-relaxed text-ink outline-none placeholder:text-muted2" /></div>
            </div>)}
          </div>
          <div className="mt-5 flex justify-end"><Button variant="solid" className="rounded-full" disabled={!answeredCount || phase === 'revising'} onClick={() => generate(composeAnswers(open, replies, revision))}>{phase === 'revising' ? <><Loader2 className="size-3 animate-spin" /> Updating…</> : <><Check className="size-3" /> Apply {answeredCount} answer{answeredCount === 1 ? '' : 's'}</>}</Button></div>
        </section>
      )}

      <section className="mt-5 rounded-[28px] bg-white/68 p-4 shadow-[0_14px_36px_rgba(30,41,59,.06)] ring-1 ring-line/70 dark:bg-white/[.03]">
        <div className="flex items-center gap-2"><PencilLine className="size-4 text-accent" /><p className="text-[12px] font-semibold text-ink">Change anything in the plan</p></div>
        <TextArea value={revision} rows={3} placeholder="For example: add a wishlist, rename the admin area, or change the checkout flow…" onChange={e => setRevision(e.target.value)} className="mt-3 w-full resize-none rounded-[20px] bg-black/[.025] px-4 py-3 text-[12px] leading-relaxed text-ink outline-none ring-1 ring-line/70 placeholder:text-muted2 focus:ring-accent/35 dark:bg-white/[.035]" />
        <div className="mt-3 flex justify-end"><Button variant="outline" className="rounded-full" disabled={!revision.trim() || phase === 'revising'} onClick={() => generate(revision.trim())}>{phase === 'revising' ? <><Loader2 className="size-3 animate-spin" /> Updating…</> : 'Update plan'}</Button></div>
      </section>

      {error && <p className="mt-4 rounded-[18px] bg-warn-tint px-4 py-3 text-[11.5px] text-warn">{error}</p>}

      <div className="sticky bottom-4 mt-6 rounded-[24px] bg-white/88 p-3 shadow-[0_18px_50px_rgba(15,23,42,.16)] ring-1 ring-white/80 backdrop-blur-2xl dark:bg-[#141b27]/92 dark:ring-white/[.06]">
        <div className="flex flex-wrap items-center gap-3"><div className="min-w-0 flex-1"><p className="text-[12px] font-semibold text-ink">{settled ? 'This plan is ready for your approval.' : (why || 'A required detail is still missing.')}</p><p className="mt-0.5 text-[10.5px] text-muted">Nothing is built from this plan until you approve it.</p></div><button disabled={!settled || phase === 'revising'} onClick={accept} className="inline-flex h-11 items-center gap-2 rounded-full bg-accent px-5 text-[12px] font-semibold text-white shadow-[0_10px_24px_rgba(93,106,251,.28)] transition hover:bg-press disabled:opacity-40"><Check className="size-3.5" />{hasSrs && !dirty ? 'Open specification' : hasSrs ? 'Rewrite specification' : 'Approve & write SRS'}</button></div>
      </div>
    </div>
  )
}

function Section({ icon: Icon, title, subtitle, children }) {
  return <section className="rounded-[28px] bg-white/64 p-5 shadow-[0_16px_40px_rgba(30,41,59,.065)] ring-1 ring-line/65 backdrop-blur-xl dark:bg-white/[.03]"><div className="mb-4 flex items-start gap-3"><span className="grid size-9 place-items-center rounded-full bg-accent/10 text-accent"><Icon className="size-4" /></span><div><p className="text-[13px] font-semibold text-ink">{title}</p><p className="mt-0.5 text-[10.5px] text-muted">{subtitle}</p></div></div>{children}</section>
}

function Mini({ title, children }) { return <div><p className="mb-2 text-[10px] font-semibold uppercase tracking-[.14em] text-label">{title}</p>{children}</div> }

function composeAnswers(open, replies, extra) {
  const pairs = open.map(q => [q.question, (replies[q.question] || '').trim()]).filter(([, a]) => a).map(([q, a]) => `- ${q}\n  ANSWER: ${a}`)
  const parts = []
  if (pairs.length) {
    parts.push('Answers to the open questions in the plan:', pairs.join('\n'))
    parts.push('Fold each answer into the plan and drop that question from open_questions. Leave everything else as it stands.')
  }
  const rest = String(extra || '').trim()
  if (rest) parts.push('Also change this:', rest)
  return parts.join('\n\n')
}

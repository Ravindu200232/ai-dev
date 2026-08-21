'use client'

import { useEffect, useRef, useState } from 'react'
import { ArrowRight, Loader2, PencilLine, Sparkles } from 'lucide-react'
import { useStore } from '@/lib/store'
import { send } from '@/lib/ws'
import { api } from '@/lib/api'
import { TextArea } from './ui'
import { cn } from '@/lib/utils'
import { useAttachments } from '@/lib/use-attachments'
import { AttachButtons, AttachList } from './srs/Attachments'
import LogoPanel from './LogoPanel'
import ThemePicker from './ThemePicker'
import Interview from './srs/Interview'
import PlanReview from './srs/PlanReview'
import SrsReview from './srs/SrsReview'


const EXAMPLES = [
  { label: 'Darkroom co-op',
    blurb: 'Three roles — member, technician, manager. Benches, chemicals, money.',
    text: 'A community darkroom with three roles. MEMBER: browses sessions with '
        + 'photos, filters by film type, books a bench for a session on a date, '
        + 'and sees their own bookings with any balance owed. TECHNICIAN: sees '
        + 'only their own bench — today\u2019s jobs in time order, marks one done '
        + 'with a form, and records the chemicals used. MANAGER: the only role '
        + 'that sees money and stock. Each screen is its own page under its '
        + 'role\u2019s section. Seed enough data that every screen has something on '
        + 'it, and one demo user of each role.' },
  { label: 'Bike workshop',
    blurb: 'Rider bookings, a mechanic’s day, dues and the parts bin.',
    text: 'A neighbourhood bicycle workshop. RIDER: browses repair slots, books '
        + 'one, and sees their bookings. MECHANIC: today\u2019s repairs in time '
        + 'order, marks a repair done, records the parts used, and has a page '
        + 'for warranty claims. TREASURER: dues paid and unpaid this month, and '
        + 'the parts bin with reorder levels. Seed real data and a demo user for '
        + 'each role.' },
  { label: 'Small hotel',
    blurb: 'Rooms with photos, availability by date, an admin who sees every booking.',
    text: 'A boutique hotel site. A guest browses rooms with photos, checks '
        + 'availability for a date range and books one. An admin sees every '
        + 'booking, can change a room\u2019s price and mark a booking paid. Seed a '
        + 'handful of rooms and bookings, and a demo user of each role.' },
]

export default function Home({ onStarted }) {
  const s = useStore()
  const { agentMode, images, think, models, srsId, srsPhase } = s
  const [prompt, setPrompt] = useState('')
  const [logoFor, setLogoFor] = useState(null)
  const [themeFor, setThemeFor] = useState(null)
  const [srsError, setSrsError] = useState('')
  const box = useRef(null)
  const attach = useAttachments()

  function begin(p, srs = '') {
    if (!p) return
    // The look is settled first: it is the cheapest thing to change and the
    // one the rest of the build is written against.
    if (agentMode) return setThemeFor({ idea: p, srs })
    startBuild(p, '', srs)
  }

  function afterTheme(p, srs, theme) {
    setThemeFor(null)
    if (agentMode && images) return setLogoFor({ idea: p, srs, theme })
    startBuild(p, '', srs, theme)
  }

  function submit() {
    begin(prompt.trim())
  }

  function startBuild(p, logo, srs = '', theme = null, uploads = null) {
    setLogoFor(null)
    setThemeFor(null)
    s.reset(null)
    s.setBusy(true)
    s.setProgress('Starting…', 0)
    onStarted?.()
    if (agentMode) {
      s.addLog('INFO', `Agent mode — ${models.agent}`)
      if (logo) s.addLog('INFO', 'Building around the logo you accepted')
      if (srs) s.addLog('INFO', 'Building from the SRS you approved')
      if (theme) s.addLog('INFO', `Building to the ${theme.id} design you chose`)
      send({ type: 'agent_build', prompt: p, model: models.agent,
             think, qa_model: models.qa, logo, srs_id: srs || '',
             uploads: uploads && Object.keys(uploads).length ? uploads : undefined,
             theme: theme?.id || '', theme_html: theme?.html || '',
             theme_page: theme?.page || '', theme_dir: theme?.dir || '' })
    } else {
      send({ type: 'build', prompt: p,
             refine_model: models.refine, build_model: models.build })
    }
  }

  async function planFirst() {
    const idea = prompt.trim()
    const files = attach.items.length
    if (!idea && !files) return box.current?.focus()
    setSrsError('')
    s.setSrs({ srsPhase: 'planning', srsBusy: 'Reading your idea…' })
    try {

      await api.saveSettings({ srs_model: models.srs || models.agent || '' })
        .catch(() => { })
      // A project has to exist before anything can be attached to it, and the
      // idea is required — so somebody who uploaded a spec and typed nothing
      // still gets a project, with the document as the thing that describes it.
      const created = await api.srs('/projects', { idea: idea || 'See the attached files.' })
      const id = created.project.id

      if (files) {
        // Before `analyze`, not after: the questions are generated from the
        // brief, and the brief is the idea plus every source. Attaching the
        // price list after the questions exist means being asked what is on it.
        s.setSrs({ srsId: id, srsBusy: `Reading your ${files === 1 ? 'attachment' : `${files} attachments`}…` })
        const { ids, failed } = await attach.upload(id)

        // Nothing typed and nothing readable is nothing to plan from, so say so
        // rather than interview them about an idea that does not exist. With an
        // idea typed, a failed attachment is a warning: the rest still works.
        if (failed && !ids.length && !idea) {
          throw new Error(files === 1
            ? 'that file could not be read, and there is nothing typed to go on'
            : 'none of those files could be read, and there is nothing typed to go on')
        }
        if (failed) {
          s.addLog('WARN', `${failed} of ${files} attachments could not be read — `
                         + 'carrying on with the rest.')
        }
      }

      s.setSrs({ srsId: id, srsBusy: 'Working out what to ask you…' })
      await api.srs(`/projects/${id}/analyze`, {})
      s.setSrs({ srsPhase: 'interview', srsBusy: '' })
    } catch (e) {
      setSrsError(e.message)
      s.resetSrs()
    }
  }

  function acceptSrs(handoffPrompt, id) {
    s.setSrs({ srsPhase: 'idle', srsBusy: '' })
    setPrompt(handoffPrompt)
    setTimeout(() => begin(handoffPrompt, id), 900)
  }

  if (srsPhase === 'review' && srsId) {
    return (
      <SrsReview projectId={srsId}
                 onApproved={acceptSrs}
                 onBack={() => s.setSrs({ srsPhase: 'plan' })} />
    )
  }

  // The interview gets the whole pane, the way the review already does. It is
  // three columns — what has been asked, the question, and the record being
  // built — and none of that fits in the 860px well the intake is set in.
  if (srsPhase === 'interview' && srsId) {
    return (
      <Interview projectId={srsId}
                 onDone={() => s.setSrs({ srsPhase: 'plan' })}
                 onCancel={() => s.resetSrs()} />
    )
  }

  return (
    // Flush left and unadorned. Nothing is centred, nothing glows behind it:
    // the page is held together by the mark's edge, the rule under the
    // lockup, and the column the copy is set in.
    // `my-auto` on the block rather than `justify-center` on the scroller:
    // centring a flex column taller than its box pushes the top out of reach,
    // and the lockup is the first thing that goes.
    <div className="relative flex min-h-0 flex-1 flex-col overflow-auto bg-[radial-gradient(circle_at_30%_10%,rgba(93,106,251,.12),transparent_34%),radial-gradient(circle_at_85%_80%,rgba(80,180,255,.10),transparent_32%)] px-6">
      <div className="relative mx-auto my-auto w-full max-w-[980px] py-8">
        {/* The mark set against the wordmark in type, rather than the drawn
            lockup. The wordmark is the heading face at its largest — it is
            the one place in the studio the type is allowed to be the artwork
            — and the mark prints square, ruled, and in black and white. */}
        <div className="glass-panel grid grid-cols-[auto_1fr] items-center gap-5 rounded-[28px] p-6">
          <img src="/__agentforge/agentforge-mark.png"
               alt="AgentForge Studio — AI-powered full stack app builder"
               className="size-[84px] shrink-0 select-none rounded-[22px] border border-white/60 object-cover shadow-lg" />
          <div className="min-w-0">
            <div className="flex items-center gap-2.5">
              <span className="h-[3px] w-[26px] bg-accent" />
              <span className="text-[10px] font-semibold uppercase
                               tracking-[.24em] text-accent">
                Describe · Generate · Deploy
              </span>
            </div>
            <h2 className="mt-2 font-display text-[46px] font-semibold leading-[.94] tracking-[-.04em] text-ink">
              AGENTFORGE<br />STUDIO
            </h2>
          </div>
        </div>

        {/* What the studio does, and what it will do it with — two cells of
            one band, divided by a rule. */}
        <div className="mt-5 grid grid-cols-[1fr_320px] gap-5">
          <p className="soft-card m-0 p-4 text-[14px] leading-[1.55] text-ink">
            Describe an app. It gets planned, written, tested, repaired and
            served — and you watch every step.
          </p>
          <div className="soft-card p-4">
            <div className="label-2xs text-label">Model</div>
            <div className="mt-1 truncate font-mono text-[12px] text-ink">
              {agentMode ? (models.agent || 'no model chosen')
                         : `${models.refine} → ${models.build}`}
            </div>
            <div className="mt-0.5 text-[11px] text-muted2">
              {agentMode ? 'agent mode' : 'two-model mode'}
              {think ? ' · thinking pass' : ' · no thinking pass'}
              {images ? ' · images' : ''}
            </div>
          </div>
        </div>

        {srsPhase === 'plan' && srsId && (
          <PlanReview projectId={srsId}
                      onGenerated={() => s.setSrs({ srsPhase: 'review' })}
                      onCancel={() => s.setSrs({ srsPhase: 'interview' })} />
        )}
        {srsPhase === 'planning' && (
          <div className="glass-panel mt-6 flex items-center gap-3 rounded-[22px] p-6">
            <Loader2 className="size-5 shrink-0 animate-spin text-accent" />
            <p className="text-[13px] text-ink">{s.srsBusy || 'Working…'}</p>
          </div>
        )}

        {srsPhase === 'idle' && (<>
        {/* Main product brief. */}
        <div className="glass-panel mt-6 overflow-hidden rounded-[26px] transition-all focus-within:border-accent/50 focus-within:shadow-[0_18px_50px_rgba(93,106,251,.12)]">
          <div className="flex items-center gap-2.5 border-b border-line/70 bg-white/35 px-4 py-3 dark:bg-white/[.02]">
            <PencilLine className="size-[13px] shrink-0 text-accent" />
            <span className="label-xs text-ink">The brief</span>
            <span className="flex-1" />
            <span className="font-mono text-[10px] text-muted2">
              {prompt.length} / ∞
            </span>
          </div>

          <TextArea value={prompt} autoFocus rows={5} ref={box}
                    placeholder="A community darkroom with three roles: members book sessions, technicians run the bench, a manager sees the money…"
                    onChange={e => setPrompt(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit()
                    }}
                    className="min-h-[148px] w-full resize-none bg-transparent p-5 text-[15px] leading-[1.6] text-ink caret-accent outline-none placeholder:text-muted2" />
          <AttachList attach={attach} className="mx-4 mb-2" />

          <div className="flex items-stretch border-t border-line/70 bg-white/28 dark:bg-white/[.018]">
            <AttachButtons attach={attach} cell />
            <span className="flex-1" />
            <button onClick={planFirst}
                    title="Answer a few questions first, and get a written spec before anything is built"
                    className="m-2 inline-flex items-center gap-2 rounded-xl border border-line bg-white/70 px-4 font-display text-[12px] font-semibold text-ink shadow-sm transition-all hover:bg-white dark:bg-white/5">
              <Sparkles className="size-[13px]" /> Plan it first
            </button>
            <button disabled={!prompt.trim()} onClick={submit}
                    className="m-2 ml-0 inline-flex items-center gap-2 rounded-xl bg-accent px-5 py-3 font-display text-[12px] font-semibold text-white shadow-[0_8px_20px_rgba(93,106,251,.25)] transition-all hover:bg-press disabled:pointer-events-none disabled:opacity-45">
              Build it <ArrowRight className="size-3.5" />
            </button>
          </div>
        </div>

        {attach.items.length > 0 && (
          <p className="mt-2 text-[11.5px] text-muted">
            Attachments are read into the specification — press{' '}
            <b className="font-semibold text-ink">Plan it first</b>. Building
            straight from the box uses only what you typed.
          </p>
        )}

        {srsError && srsPhase === 'idle' && (
          <p className="mt-2 border-l-[3px] border-accent bg-tint px-2.5 py-1.5
                        text-[11.5px] text-deep">
            The SRS could not start — {srsError}
          </p>
        )}

        {/* Starter briefs. */}
        <div className="mt-6">
          <div className="label-xs py-2.5 text-label">Or start from one of these</div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            {EXAMPLES.map((e, i) => (
              <button key={e.label} onClick={() => setPrompt(e.text)}
                      className={cn('py-3.5 text-left transition-colors',
                        'rounded-[18px] border border-line bg-white/50 p-4 shadow-sm hover:bg-white dark:bg-white/[.025] dark:hover:bg-white/[.05]',
                        i === 0 ? 'pr-4' : i === EXAMPLES.length - 1 ? 'pl-4' : 'px-4')}>
                <div className="font-display text-[14px] font-extrabold text-ink">
                  {e.label}
                </div>
                <div className="mt-[3px] text-[11.5px] leading-[1.45] text-label">
                  {e.blurb}
                </div>
              </button>
            ))}
          </div>
        </div>
        </>)}
      </div>

      {themeFor && (
        <ThemePicker idea={themeFor.idea} srsId={themeFor.srs}
                     model={models.agent}
                     onPick={(theme) => afterTheme(themeFor.idea, themeFor.srs, theme)}
                     onSkip={() => afterTheme(themeFor.idea, themeFor.srs, null)} />
      )}

      {logoFor && (
        <LogoPanel idea={logoFor.idea} model={models.agent}
                   onAccept={(file, uploads) => startBuild(logoFor.idea, file,
                                                  logoFor.srs, logoFor.theme, uploads)}
                   onSkip={(uploads) => startBuild(logoFor.idea, '', logoFor.srs,
                                            logoFor.theme, uploads)} />
      )}
    </div>
  )
}

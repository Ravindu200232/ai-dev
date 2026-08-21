'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  BrainCircuit, Bug, Check, FileCode2, FlaskConical, ImageIcon, Loader2,
  MousePointerSquareDashed, Pencil, Puzzle, Sparkles, Square, Wrench,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useStore } from '@/lib/store'
import { activityEvent, liveFileActivity } from '@/lib/activity'
import { cn } from '@/lib/utils'
import { displayPct, stillFor } from '@/lib/progress-model'
import { stageIndex, stagesFor } from '@/lib/work-stages'

/**
 * Stop the run, and say what stopping costs before it happens.
 *
 * Cancelling deletes the half-built project and the specification it was
 * built from, which is what was asked for and is also not something to do on
 * one stray click at the top of a screen someone is watching. So the button
 * asks once. The second press is the irreversible one and looks like it.
 */
function CancelBuild() {
  const [asking, setAsking] = useState(false)
  const [sending, setSending] = useState(false)
  const addLog = useStore(s => s.addLog)

  async function stop() {
    setSending(true)
    try {
      await api.cancelBuild()
      // The run reports its own end over the socket — `cancelled` clears busy
      // and empties the pane. Nothing to do here but wait for it.
    } catch (e) {
      addLog('WARN', `could not cancel — ${e.message}`)
      setSending(false)
      setAsking(false)
    }
  }

  if (!asking) {
    return (
      <button onClick={() => setAsking(true)}
              className="inline-flex items-center gap-1.5 rounded-full border border-line
                         px-3 py-1.5 text-[11px] font-medium text-muted transition
                         hover:border-bad/40 hover:bg-bad/[.06] hover:text-bad">
        <Square className="size-3" /> Cancel
      </button>
    )
  }

  return (
    <div className="inline-flex items-center gap-1.5">
      <span className="text-[10.5px] text-muted">Stop and delete?</span>
      <button onClick={stop} disabled={sending}
              className="inline-flex items-center gap-1.5 rounded-full bg-bad px-3 py-1.5
                         text-[11px] font-semibold text-white transition
                         hover:brightness-110 disabled:opacity-60">
        {sending ? <Loader2 className="size-3 animate-spin" /> : <Square className="size-3" />}
        {sending ? 'Stopping' : 'Yes'}
      </button>
      <button onClick={() => setAsking(false)} disabled={sending}
              className="rounded-full border border-line px-3 py-1.5 text-[11px]
                         font-medium text-muted transition hover:bg-ink/[.05]">
        Keep going
      </button>
    </div>
  )
}

// Five rows of the activity feed, and the same for the task list beside it so
// the two panels stay level. A row is a title line, a detail line and the gap
// under it.
const PANEL_ROWS = 'h-[292px]'

/**
 * What each kind of work is, and how much of the overlay it deserves.
 *
 * A first build has four stages and a plan of tasks; a pencil edit has one
 * file and a few seconds. Showing the four-stage rail for both said the same
 * thing about two jobs that share nothing, and the stage lamps for a pencil
 * edit were always going to read as stalled — there is no plan stage in an
 * edit to a single element.
 *
 * `rail` is what separates them: with it the build shape, without it a single
 * focused panel that says what is being changed and shows the log.
 */
const WORK = {
  build:   { rail: true,  Icon: FileCode2, eyebrow: 'Building',
             title: 'Building the app' },
  feature: { rail: false, Icon: Puzzle, eyebrow: 'Adding a feature',
             title: 'Writing the feature into the app',
             detail: 'The feature is written, tested against the pages it touches, and rolled back whole if it does not hold.' },
  repair:  { rail: false, Icon: Wrench, eyebrow: 'Repairing', title: 'Fixing what was reported',
             detail: 'The change is scoped to the files the problem points at, and verified before it is kept.' },
  select:  { rail: false, Icon: MousePointerSquareDashed, eyebrow: 'Editing the element',
             title: 'Changing the element you picked',
             detail: 'Only the element you selected and the file it lives in are touched.' },
  pencil:  { rail: false, Icon: Pencil, eyebrow: 'Pencil edit', title: 'Applying your drawing',
             detail: 'The region you marked is read from the page and turned into a change to its source.' },
  image:   { rail: false, Icon: ImageIcon, eyebrow: 'Pictures', title: 'Putting the picture in place',
             detail: 'The picture is written into the project and the page that shows it is updated.' },
}

const STAGES = [
  { id: 'plan', label: 'Plan', Icon: BrainCircuit },
  { id: 'build', label: 'Build', Icon: FileCode2 },
  { id: 'test', label: 'Check', Icon: FlaskConical },
  { id: 'ready', label: 'Ready', Icon: Sparkles },
]

// The lamps a smaller job lights, drawn by the same rail as a build so the
// two read as the same product doing different amounts of work.
const STAGE_ICONS = {
  brain: BrainCircuit, file: FileCode2, flask: FlaskConical,
  sparkles: Sparkles, bug: Bug, wrench: Wrench, pencil: Pencil,
  image: ImageIcon, pointer: MousePointerSquareDashed,
}

function railFor(workKind, work, progress) {
  if (work.rail) return { stages: STAGES, index: -1 }
  const own = stagesFor(workKind)
  if (!own) return { stages: null, index: -1 }
  return {
    stages: own.map(s => ({ ...s, Icon: STAGE_ICONS[s.icon] || FileCode2 })),
    index: stageIndex(workKind, progress.raw),
  }
}


// The longest a step can be quiet before it is worth mentioning.
const PATIENT_AFTER = 45
const SLOW_AFTER = 120

/**
 * One line telling a non-technical person that the run is alive.
 * It never says "stuck": a long step here is normal, and saying otherwise
 * teaches people to cancel healthy builds.
 */
function reassure(step, still, events) {
  const doing = String(step || '').replace(/[.…]+$/, '').trim()
  const latest = events && events.length ? events[events.length - 1] : null
  if (still >= SLOW_AFTER) {
    return `${doing || 'This step'} is taking a while. That is normal for a big page — the app is still being written, nothing has failed.`
  }
  if (still >= PATIENT_AFTER) {
    return latest?.title
      ? `Still working — last finished: ${latest.title}`
      : `${doing || 'Working'} — larger steps can take a minute or two.`
  }
  if (latest?.title) return `Just finished: ${latest.title}`
  return doing ? `${doing} — you can keep this tab open, it saves as it goes.` : 'Getting started…'
}

export default function BuildOverlay() {
  const busy = useStore(s => s.busy)
  const workKind = useStore(s => s.workKind)
  const logs = useStore(s => s.logs)
  const progress = useStore(s => s.progress)
  const liveFile = useStore(s => s.liveFile)
  const phases = useStore(s => s.phases)
  const tests = useStore(s => s.tests)
  const e2eLive = useStore(s => s.e2eLive)
  const opening = useStore(s => s.opening)

  const feedEnd = useRef(null)
  const startedAt = useRef(0)
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!busy) return
    startedAt.current = Date.now()
    setNow(Date.now())
    const id = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(id)
  }, [busy])

  const events = useMemo(() => {
    const rows = []
    const seen = new Set()
    for (const row of logs.slice(-220)) {
      const event = activityEvent(row.text, row.level)
      if (!event) continue
      const key = `${event.kind}:${event.title}`
      if (seen.has(key)) continue
      seen.add(key)
      rows.push({ ...event, at: row.at })
    }
    // The panel scrolls now, so the feed keeps its history instead of being
    // trimmed to whatever fitted without one.
    return rows.slice(-60)
  }, [logs])

  useEffect(() => {
    feedEnd.current?.scrollIntoView({ block: 'end', behavior: 'smooth' })
  }, [events.length])

  if (!busy || (tests.running && e2eLive)) return null

  // Opening a stored project is not a build. It used to raise the whole
  // Plan → Build → Check → Ready rail, so clicking a finished project in the
  // sidebar read as though it had started generating one from scratch.
  if (opening) {
    return (
      <div className="absolute inset-0 z-[20] grid place-items-center bg-[linear-gradient(180deg,#f8faff_0%,#eef3fb_100%)] dark:bg-[linear-gradient(180deg,#161c28_0%,#0f141d_100%)]">
        <div className="flex flex-col items-center gap-3 px-8 text-center">
          <Loader2 className="size-6 animate-spin text-accent" />
          <p className="text-[14px] font-semibold text-ink">
            {progress.step || 'Opening the project…'}
          </p>
          <p className="max-w-[380px] text-[12px] leading-relaxed text-muted">
            Reading the files and starting the preview. Nothing is being rebuilt.
          </p>
        </div>
      </div>
    )
  }

  const work = WORK[workKind] || WORK.build
  const state = workKind && !work.rail
    // A one-job run has no stages to resolve, and inferring them from log text
    // produced headings about testing and repair that belonged to a build.
    ? { stage: '', eyebrow: work.eyebrow, title: work.title, detail: work.detail }
    : resolveState({ liveFile, tests, e2eLive, phases, logs, progress })
  const rail = railFor(workKind, work, progress)
  const pct = displayPct(progress, now)
  const still = stillFor(progress, now)
  const elapsed = Math.max(0, Math.floor((now - startedAt.current) / 1000))
  const reassurance = reassure(progress.step, still, events)
  const parallel = Boolean(liveFile && (tests.running || recentTestActivity(logs)))
  const activeIndex = work.rail
    ? STAGES.findIndex(s => s.id === state.stage)
    : rail.index
  const Icon = work.Icon

  return (
    // The overlay scrolls. It used to be `overflow-hidden` over a `h-full`
    // column, so on a short window — or once the stage rail and the header had
    // taken their share — the Progress feed and the task list were cut off at
    // the bottom edge with no way to reach the rest.
    <div className="absolute inset-0 z-[20] overflow-y-auto bg-[radial-gradient(circle_at_30%_0%,rgba(105,117,255,.18),transparent_36%),linear-gradient(180deg,#f8faff_0%,#eef3fb_100%)] pb-16 dark:bg-[radial-gradient(circle_at_30%_0%,rgba(105,117,255,.18),transparent_36%),linear-gradient(180deg,#161c28_0%,#0f141d_100%)]">
      <div className="mx-auto flex min-h-full w-full max-w-[1120px] flex-col px-7 py-8">
        <div className="flex items-center gap-4">
          <div className="grid size-12 shrink-0 place-items-center rounded-[18px] bg-accent text-white shadow-[0_14px_28px_rgba(93,106,251,.28)]">
            {!work.rail ? <Icon className="size-5" />
              : state.stage === 'test' ? <FlaskConical className="size-5" />
              : state.stage === 'fix' ? <Bug className="size-5" />
              : state.stage === 'plan' ? <BrainCircuit className="size-5" />
              : state.stage === 'ready' ? <Sparkles className="size-5" />
              : <FileCode2 className="size-5" />}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[.2em] text-accent">
              <span>{state.eyebrow}</span>
              {parallel && <span className="rounded-full bg-accent/10 px-2 py-0.5 tracking-normal normal-case">tests are running in parallel</span>}
            </div>
            <h2 className="mt-1 truncate text-[28px] font-semibold tracking-[-.035em] text-ink">{state.title}</h2>
            <p className="mt-1 max-w-[720px] text-[12.5px] leading-relaxed text-muted">{state.detail}</p>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-2">
            <div className="text-right">
              <div className="text-[21px] font-semibold tabular-nums text-ink">{pct ? `${Math.round(pct)}%` : '•••'}</div>
              <div className="text-[10px] text-muted">{formatTime(elapsed)}</div>
            </div>
            <CancelBuild />
          </div>
        </div>

        <div className="mt-6 h-1.5 overflow-hidden rounded-full bg-black/[.055] dark:bg-white/[.07]">
          <div className="h-full rounded-full bg-[linear-gradient(90deg,#6875ff,#8f79ff)] transition-[width] duration-1000 ease-linear"
               style={{ width: `${pct ? Math.max(5, pct) : 11}%` }} />
        </div>

        {/* Nobody watching a bar wants to wonder whether it has died. This
            says what is happening, in the words someone who did not write
            the backend would use. */}
        <p className="mt-3 flex items-center gap-2 text-[12px] leading-relaxed text-muted">
          <span className={cn('size-1.5 shrink-0 rounded-full',
            still > 90 ? 'bg-amber-500' : 'bg-emerald-500 animate-pulse')} />
          <span className="min-w-0 flex-1 truncate">{reassurance}</span>
        </p>

        {rail.stages && (
        <div className="mt-6 flex items-center gap-0 px-2">
          {rail.stages.map((stage, i) => {
            const active = i === activeIndex || (parallel && stage.id === 'test')
            const done = i < activeIndex && !(parallel && stage.id === 'test')
            return (
              <div key={stage.id} className="relative flex min-w-0 flex-1 items-center">
                {i > 0 && <span className={cn('absolute right-1/2 top-4 h-px w-full', done || active ? 'bg-accent/35' : 'bg-line2')} />}
                <div className="relative z-10 mx-auto flex flex-col items-center gap-1.5">
                  <span className={cn('grid size-8 place-items-center rounded-full border transition-all',
                    active ? 'border-accent bg-accent text-white shadow-[0_7px_18px_rgba(93,106,251,.22)]'
                      : done ? 'border-ok/30 bg-ok-tint text-ok'
                      : 'border-line bg-white/75 text-muted2 dark:bg-white/[.035]')}>
                    {active ? <Loader2 className="size-3.5 animate-spin" /> : done ? <Check className="size-3.5" /> : <stage.Icon className="size-3.5" />}
                  </span>
                  <span className={cn('text-[10px] font-medium', active ? 'text-accent' : done ? 'text-ink' : 'text-muted2')}>{stage.label}</span>
                </div>
              </div>
            )
          })}
        </div>
        )}

        {/* Both panels are a fixed height rather than a share of whatever is
            left. Sized by `flex-1` they grew with the run: a long build pushed
            the whole overlay down the page and the preview under it went with
            them. Five rows is what fits without either panel becoming the
            page, and the rest is reached by scrolling inside it. */}
        <div className={cn('mt-8 grid gap-8',
                          rail.stages && 'lg:grid-cols-[1fr_360px]')}>
          <div className="flex min-h-0 flex-col overflow-hidden rounded-[28px] bg-white/72 p-6 shadow-[0_18px_55px_rgba(30,41,59,.08)] ring-1 ring-white/80 backdrop-blur-xl dark:bg-white/[.035] dark:ring-white/[.05]">
            <div className="flex shrink-0 items-center gap-3">
              <div>
                <p className="text-[15px] font-semibold text-ink">Progress</p>
                <p className="mt-0.5 text-[11px] text-muted">Only useful, human-readable updates appear here.</p>
              </div>
              <span className="flex-1" />
              {parallel && (
                <div className="flex items-center gap-2 rounded-full bg-[#eef5ff] px-3 py-1.5 text-[10.5px] font-medium text-[#4c66d9] dark:bg-white/[.055] dark:text-[#9ca8ff]">
                  <span className="flex gap-1">
                    <i className="size-1.5 animate-pulse rounded-full bg-current" />
                    <i className="size-1.5 animate-pulse rounded-full bg-current [animation-delay:160ms]" />
                    <i className="size-1.5 animate-pulse rounded-full bg-current [animation-delay:320ms]" />
                  </span>
                  Parallel testing
                </div>
              )}
            </div>

            {/* Scrolls, and follows. Five updates are visible and the newest
                is always one of them — the list used to be capped at seven in
                a box that could not scroll, so on a long build the stage that
                had just happened was the one you could not see. */}
            <div className={cn('relative mt-6 overflow-y-auto pl-8 pr-1', PANEL_ROWS)}>
              <span className="absolute bottom-1 left-[10px] top-1 w-px bg-line2" />
              {events.map((event, i) => (
                <div key={`${event.at}-${i}`} className="relative mb-5 last:mb-0">
                  <span className={cn('absolute -left-8 top-[1px] grid size-[21px] place-items-center rounded-full ring-[5px] ring-white dark:ring-[#171d28]',
                    event.kind === 'warn' ? 'bg-warn-tint text-warn'
                      : event.kind === 'done' ? 'bg-ok-tint text-ok'
                      : 'bg-tint text-accent')}>
                    {event.kind === 'done' ? <Check className="size-3" /> : <span className="size-1.5 rounded-full bg-current" />}
                  </span>
                  <p className="text-[12.5px] font-semibold text-ink">{event.title}</p>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-muted">{event.detail}</p>
                </div>
              ))}
              {!events.length && (
                <div className="py-12 text-center">
                  <Loader2 className="mx-auto size-5 animate-spin text-accent" />
                  <p className="mt-3 text-[12px] text-muted">The first useful update will appear here shortly.</p>
                </div>
              )}
              <div ref={feedEnd} />
            </div>
          </div>

          {!work.rail && rail.stages ? (
            /* A one-job run has no plan of tasks, so the column shows the
               steps of this job instead. It is the same panel the build
               uses for its phases, which is the point: a pencil edit is a
               smaller run of the same machine, not a different screen. */
            <aside className="flex min-h-0 flex-col overflow-hidden rounded-[28px] bg-white/72 p-6 shadow-[0_18px_55px_rgba(30,41,59,.08)] ring-1 ring-white/80 backdrop-blur-xl dark:bg-white/[.035] dark:ring-white/[.05]">
              <p className="text-[15px] font-semibold text-ink">{work.eyebrow}</p>
              <p className="mt-0.5 text-[11px] leading-relaxed text-muted">{work.detail}</p>
              <div className={cn('mt-6 overflow-y-auto pr-1', PANEL_ROWS)}>
                {rail.stages.map((stage, i) => {
                  const active = i === rail.index
                  const done = i < rail.index
                  return (
                    <div key={stage.id} className="mb-4 flex items-start gap-3 last:mb-0">
                      <span className={cn('mt-[1px] grid size-[21px] shrink-0 place-items-center rounded-full',
                        active ? 'bg-accent text-white'
                          : done ? 'bg-ok-tint text-ok'
                          : 'bg-tint text-muted2')}>
                        {active ? <Loader2 className="size-3 animate-spin" />
                          : done ? <Check className="size-3" />
                          : <stage.Icon className="size-3" />}
                      </span>
                      <div className="min-w-0">
                        <p className={cn('text-[12.5px] font-semibold',
                          active ? 'text-accent' : done ? 'text-ink' : 'text-muted2')}>
                          {stage.label}
                        </p>
                        {active && progress.step && (
                          <p className="mt-0.5 text-[11px] leading-relaxed text-muted">
                            {progress.step}
                          </p>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </aside>
          ) : !work.rail ? null : phases.length > 0 ? (
            /* The plan has tasks, so show them. A first build is the only run
               that produces phases, and watching them tick over says more than
               a restatement of the headline. Repairs and edits have no phases
               and keep the panel below. */
            <TaskTable phases={phases} tests={tests} elapsed={elapsed} />
          ) : (
          <div className="flex min-h-0 flex-col justify-between rounded-[28px] bg-[#151b27] p-6 text-white shadow-[0_22px_58px_rgba(15,23,42,.2)] dark:bg-[#0b0f16]">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[.18em] text-white/45">Current work</p>
              <p className="mt-3 text-[22px] font-semibold leading-tight tracking-[-.025em]">{state.short}</p>
              <p className="mt-2 text-[11.5px] leading-relaxed text-white/55">{state.sideDetail}</p>
            </div>

            {parallel ? (
              <div className="mt-8">
                <p className="text-[10px] font-semibold uppercase tracking-[.16em] text-white/40">Working together</p>
                <div className="mt-3 space-y-2.5">
                  <Lane label="Builder" text="Writing the next part" delay="0ms" />
                  <Lane label="QA" text="Preparing and running checks" delay="180ms" />
                  <Lane label="Verifier" text="Waiting for safe checkpoints" delay="360ms" />
                </div>
              </div>
            ) : (
              <div className="mt-8 rounded-[20px] bg-white/[.055] p-4">
                <p className="text-[10px] font-semibold uppercase tracking-[.16em] text-white/40">Next</p>
                <p className="mt-2 text-[12px] leading-relaxed text-white/70">{state.next}</p>
              </div>
            )}

            <div className="mt-8 flex items-center justify-between border-t border-white/10 pt-4 text-[10.5px] text-white/45">
              <span>{tests.pass ? `${tests.pass} checks passed` : 'Checks will appear when ready'}</span>
              <span>{formatTime(elapsed)}</span>
            </div>
          </div>
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * The plan's tasks, ticking over as the build works down them.
 *
 * Shown in place of the dark "current work" card whenever the run has phases,
 * which in practice means a first build: a repair or an edit never produces
 * them and keeps the card. The styling follows the Progress panel beside it
 * rather than the old slab — two cards of the same material read as one
 * surface, where a black box beside a white one read as two.
 */
function TaskTable({ phases, tests, elapsed }) {
  const done = phases.filter(p => p.status === 'done').length
  return (
    <div className="flex min-h-0 flex-col rounded-[28px] bg-white/72 p-6 shadow-[0_18px_55px_rgba(30,41,59,.08)] ring-1 ring-white/80 backdrop-blur-xl dark:bg-white/[.035] dark:ring-white/[.05]">
      <div className="flex items-baseline gap-2">
        <p className="text-[15px] font-semibold text-ink">Tasks</p>
        <span className="text-[11px] tabular-nums text-muted">{done}/{phases.length}</span>
      </div>
      <p className="mt-0.5 text-[11px] text-muted">The plan, in the order it is being built.</p>

      <div className={cn('mt-4 space-y-1 overflow-y-auto pr-1', PANEL_ROWS)}>
        {phases.map((p, i) => {
          const active = p.status === 'active'
          const finished = p.status === 'done'
          return (
            <div key={p.phase ?? i}
                 className={cn('flex items-center gap-2.5 rounded-[14px] px-2.5 py-2 transition-colors',
                   active ? 'bg-accent/[.08]' : 'hover:bg-black/[.025] dark:hover:bg-white/[.03]')}>
              <span className={cn('grid size-6 shrink-0 place-items-center rounded-full border text-[10px] tabular-nums',
                active ? 'border-accent bg-accent text-white'
                  : finished ? 'border-ok/30 bg-ok-tint text-ok'
                  : 'border-line bg-white/70 text-muted2 dark:bg-white/[.04]')}>
                {active ? <Loader2 className="size-3 animate-spin" />
                  : finished ? <Check className="size-3" />
                  : i + 1}
              </span>
              <span className={cn('min-w-0 flex-1 truncate text-[12px]',
                active ? 'font-semibold text-ink' : finished ? 'text-ink' : 'text-muted')}>
                {p.title || `Task ${i + 1}`}
              </span>
              {active && (
                <span className="shrink-0 text-[10px] font-medium text-accent">working</span>
              )}
            </div>
          )
        })}
      </div>

      <div className="mt-4 flex items-center justify-between border-t border-line/70 pt-3.5 text-[10.5px] text-muted">
        <span>{tests.pass ? `${tests.pass} checks passed` : 'Checks will appear when ready'}</span>
        <span className="tabular-nums">{formatTime(elapsed)}</span>
      </div>
    </div>
  )
}

function resolveState({ liveFile, tests, e2eLive, phases, logs, progress }) {
  const active = phases.filter(p => p.status === 'active').slice(-1)[0]
  const activeText = String(active?.title || progress.step || '').toLowerCase()
  const recent = logs.slice(-45).map(x => String(x.text || '').toLowerCase())
  const joined = recent.join('\n')
  const buildSeen = Boolean(liveFile) || recent.some(x => /files on disk|writing project files|\bwritten\b|📝/.test(x))
  const testSeen = tests.running || tests.rows.length > 0 || recent.some(x => /vitest|unit test|playwright|e2e|journey/.test(x))
  const railStage = testSeen ? 'test' : buildSeen ? 'build' : 'plan'

  if ((testSeen || buildSeen) && /final|finishing|serve|preparing the live preview|ready to preview/.test(activeText)) {
    return {
      stage: 'ready', eyebrow: 'Finishing', title: 'Preparing the live preview', short: 'Getting the finished app ready',
      detail: 'The latest work is complete and the preview is being refreshed for you.',
      sideDetail: 'Only the final handoff remains before the updated app is ready to use.',
      next: 'The preview refreshes automatically when the final checkpoint is safe.',
    }
  }
  if (tests.running || e2eLive) {
    return {
      stage: 'test', eyebrow: 'Testing', title: 'Checking the app in real use', short: 'Using the app like a person would',
      detail: 'Focused checks and browser flows are running. If a real app problem appears, the repair stays in this same run.',
      sideDetail: 'The visible browser can follow end-to-end steps while the rest of the checks continue in parallel.',
      next: 'Any failed behavior is traced to the affected code, repaired, and checked again.',
    }
  }
  if (/repair|root cause|bug fix|repaired|fixing/.test(activeText + '\n' + joined.slice(-2400))) {
    return {
      stage: railStage, eyebrow: 'Repairing', title: 'Fixing a problem found by verification', short: 'Repairing the affected area',
      detail: 'AgentForge is tracing the cause first, then changing only the part that needs it.',
      sideDetail: 'The milestone bar stays stable while the fixer works, then verification resumes from the same checkpoint.',
      next: 'The affected page or user flow will be checked again automatically.',
    }
  }
  if (liveFile) {
    const event = liveFileActivity(liveFile)
    return {
      stage: 'build', eyebrow: 'Building', title: event.title, short: event.title,
      detail: event.detail, sideDetail: 'Planning is complete. Builder and QA can now work in parallel without moving the milestone bar backwards.',
      next: 'The completed part will be checked before the run moves to the next feature.',
    }
  }
  if (buildSeen) {
    return {
      stage: testSeen ? 'test' : 'build', eyebrow: testSeen ? 'Checking' : 'Building',
      title: testSeen ? 'Reviewing the latest build' : 'Building the next part',
      short: testSeen ? 'Checking what was just changed' : 'Continuing the approved build',
      detail: testSeen ? 'The main build is already underway; the studio will not jump back to Planning between file updates.' : 'Files are being prepared from the approved plan.',
      sideDetail: 'The milestone bar only moves forward after the first file is written.',
      next: testSeen ? 'Any issue found here is repaired and re-checked.' : 'Focused checks start as each safe part becomes ready.',
    }
  }
  return {
    stage: 'plan', eyebrow: 'Planning', title: 'Turning the request into a build plan', short: 'Planning the app structure',
    detail: 'Roles, routes, features and dependencies are being mapped before code generation starts.',
    sideDetail: 'Once the first file is written, this milestone stays complete for the rest of the run.',
    next: 'As soon as the plan is ready, the builder begins writing the app.',
  }
}

function recentTestActivity(logs) {
  const cutoff = Date.now() - 12000
  return logs.some(l => l.at >= cutoff && /test|vitest|playwright|e2e|authored/i.test(String(l.text || '')))
}

function Lane({ label, text, delay }) {
  return (
    <div className="rounded-[16px] bg-white/[.055] px-3.5 py-3">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-semibold text-white/85">{label}</span>
        <span className="flex-1" />
        <span className="size-1.5 animate-pulse rounded-full bg-[#91a2ff]" style={{ animationDelay: delay }} />
      </div>
      <p className="mt-1 text-[10.5px] text-white/45">{text}</p>
    </div>
  )
}

function formatTime(seconds) {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return m ? `${m}m ${String(s).padStart(2, '0')}s` : `${s}s`
}

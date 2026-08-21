'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Moon, Sun, FolderUp, Settings, Download, ExternalLink, Search, Play, Trash2,
} from 'lucide-react'
import { useStore, KEYS } from '@/lib/store'
import { api } from '@/lib/api'
import { isCloud, hasVision, maxContext, roomyContext } from '@/lib/models'
import ModelPicker from './ModelPicker'
import { Badge, Button, Input, SectionLabel, Seg, SegOpt, Tag, Tip } from './ui'
import { cn } from '@/lib/utils'


export default function Sidebar({
  models: cat, projects, onOpen, onImport, onSettings, onZip, onResume, onDeleted,
}) {
  const s = useStore()
  const { models, agentMode, think, images, project, status, statusText, theme } = s
  const folderRef = useRef(null)
  const [q, setQ] = useState('')
  const [fooocus, setFooocus] = useState(null)
  const [confirming, setConfirming] = useState('')
  const [removing, setRemoving] = useState('')

  async function remove(name) {
    setRemoving(name)
    try {
      await api.deleteProject(name)
      s.addLog('SUCCESS', `Deleted ${name}`)
    } catch (e) {
      // Reported, not trusted. The request in front of this API is abandoned
      // at 30 seconds and a delete used to run past it, so "the connection
      // died" and "nothing happened" are not the same thing — the project was
      // gone and the studio went on showing it. The list below is what
      // settles it: whatever the server did, this asks it again.
      s.addLog('WARN', `Delete of ${name} did not report back — ${e.message}. `
                     + 'Checking whether it went.')
    }
    // The studio was pointing at a folder that may have stopped existing, so
    // it lets go either way; `onDeleted` re-reads the list from the server.
    if (project === name) useStore.getState().reset(null)
    onDeleted?.(name)
    setRemoving('')
    setConfirming('')
  }

  function pick(role, id) {
    useStore.setState({ models: { ...models, [role]: id } })
    s.persist(KEYS[role], id)
    if (role === 'agent' && isCloud(id) && !cat.cloudEnabled) {
      s.addLog('WARN', 'Cloud model selected but Ollama is not signed in — '
                     + 'run `ollama signin`')
    }
    if (role === 'qa') {
      const eff = id || models.agent
      s.addLog(isCloud(eff) ? 'INFO' : 'WARN', isCloud(eff)
        ? `QA runs on ${eff}`
        : `${eff} is local — QA is cloud-only and will not run.`)
    }
    if (role === 'srs') {

      const eff = id || models.agent
      api.saveSettings({ srs_model: id }).catch(() =>
        s.addLog('WARN', 'Could not save the SRS model — it will not stick.'))
      s.addLog(roomyContext(cat.all, eff) ? 'INFO' : 'WARN',
        roomyContext(cat.all, eff)
          ? `The SRS runs on ${eff}`
          : `${eff} has a small context window — the SRS will be truncated.`)
    }
    if (role === 'deploy') {

      const eff = id || models.agent
      api.saveSettings({ deploy_model: id }).catch(() =>
        s.addLog('WARN', 'Could not save the deploy model — it will not stick.'))
      s.addLog(roomyContext(cat.all, eff) ? 'INFO' : 'WARN',
        roomyContext(cat.all, eff)
          ? `Deployment plans run on ${eff}`

          : `${eff} has a small window — a truncated plan cannot be deployed.`)
    }
  }

  function toggle(key, storeKey) {
    const next = !s[key]
    useStore.setState({ [key]: next })
    s.persist(storeKey, next ? '1' : '0')
  }

  // The Images chip is the only switch here that another program has to agree
  // with. Agent and Thinking are settings this browser holds and sends with
  // the next build; images are drawn by Fooocus, which the server reaches over
  // HTTP and which the chip never told anything. So the chip was on, the
  // server's own `image_enabled` was whatever it had been left at, and the
  // build logged "image generation is off" with the switch lit.
  //
  // Two things follow from that. The chip has to write the server's setting,
  // and it has to say whether Fooocus is actually answering — turning it on is
  // exactly the moment somebody wants to know, and it is a separate program
  // that gets started and stopped by hand, so the answer has to be asked for
  // again each time rather than remembered from boot.
  async function checkFooocus() {
    setFooocus(f => ({ ...(f || {}), checking: true }))
    try {
      const r = await api.imageCheck()
      setFooocus({ ...r, checking: false })
      return r
    } catch (e) {
      setFooocus({ error: e.message, checking: false })
      return null
    }
  }

  useEffect(() => {
    api.settings()
      .then(cfg => {
        // The server's setting is the one that decides, so the chip adopts it
        // rather than the other way round — otherwise a switch left on in this
        // browser would claim a capability the build does not have. A server
        // too old to report it leaves the saved chip alone rather than
        // switching it off on the strength of a missing field.
        if (!cfg || !('image_enabled' in cfg)) return
        useStore.setState({ images: !!cfg.image_enabled })
        s.persist(KEYS.images, cfg.image_enabled ? '1' : '0')
        if (cfg.image_enabled) checkFooocus()
      })
      .catch(() => { })
  }, [])

  async function toggleImages() {
    const next = !images
    useStore.setState({ images: next })
    s.persist(KEYS.images, next ? '1' : '0')
    try {
      await api.saveSettings({ image_enabled: next })
    } catch {
      s.addLog('WARN', 'Could not save the images setting — it will not stick.')
    }
    if (!next) return setFooocus(null)

    const r = await checkFooocus()
    if (r?.available) {
      s.addLog('INFO', `Fooocus is answering at ${r.host} — pictures will be drawn.`)
    } else if (r?.can_start) {
      s.addLog('WARN', 'No Fooocus is answering. Press “start it” in the '
                     + 'sidebar, or run it yourself.')
    } else {
      s.addLog('WARN', 'No Fooocus is answering and none was found on this '
                     + 'machine — start it, or set its address in Settings.')
    }
  }

  async function startFooocus() {
    setFooocus(f => ({ ...(f || {}), checking: true }))
    try {
      const r = await api.imageStart()
      s.addLog('INFO', `Starting Fooocus — ${r.launcher}. It takes a `
                     + 'minute or two to load its model.')
    } catch (e) {
      s.addLog('WARN', `Could not start Fooocus — ${e.message}`)
      return setFooocus(f => ({ ...(f || {}), checking: false }))
    }
    // Cold start is minutes, not seconds, and the answer is what the chip
    // shows — so poll rather than ask once and report a failure that is only
    // a model still loading.
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 5000))
      const r = await api.imageCheck().catch(() => null)
      if (r?.available) {
        setFooocus({ ...r, checking: false })
        return s.addLog('INFO', `Fooocus is up at ${r.host}.`)
      }
    }
    setFooocus(f => ({ ...(f || {}), checking: false }))
    s.addLog('WARN', 'Fooocus did not come up within five minutes — check '
                   + 'its window for what it is waiting on.')
  }

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return projects
    return projects.filter(p =>
      String(p.title || p.name || p).toLowerCase().includes(needle) ||
      String(p.name || '').toLowerCase().includes(needle))
  }, [projects, q])

  const qaEffective = models.qa || models.agent
  const srsEffective = models.srs || models.agent
  const srsRoomy = roomyContext(cat.all, srsEffective)
  const deployEffective = models.deploy || models.agent
  const deployRoomy = roomyContext(cat.all, deployEffective)
  const dot = { live: 'bg-ok', busy: 'bg-warn', connecting: 'bg-muted2' }[status]
    || 'bg-bad'

  return (
    <aside className="glass-panel flex w-[var(--sidebar-w)] shrink-0 flex-col overflow-hidden rounded-[24px]">
      <header className="grid grid-cols-[auto_1fr_auto] items-center gap-3 border-b border-line/70 px-4 py-4">
        {/* The artwork, not a letter standing in for it. `basePath` is
            `/__agentforge`, so a file in `studio/public` is served from under
            it — an absolute `/agentforge-mark.png` would go to the generated
            app's dev server instead, which is a different site entirely.

            Square, ruled and printed in black and white: photography in this
            system never carries colour of its own. */}
        <img src="/__agentforge/agentforge-mark.png" alt="AgentForge"
             width={34} height={34}
             className="size-9 shrink-0 rounded-xl border border-white/60 object-cover shadow-sm" />
        <div className="min-w-0">
          <div className="font-display text-[16px] font-bold leading-none
                          tracking-[-.02em] text-ink">
            AGENTFORGE
          </div>
          <div className="mt-[5px] flex items-center gap-[6px]">
            <span className="h-[2px] w-[15px] rounded-full bg-accent" />
            <span className="text-[9.5px] font-semibold tracking-[.24em] text-label">
              STUDIO
            </span>
          </div>
        </div>
        <Tip text="Toggle theme">
          <Button variant="outline" size="icon" className="size-[28px]"
                  onClick={() => s.setTheme(theme === 'dark' ? 'light' : 'dark')}>
            <Moon className="hidden size-3.5 dark:block" />
            <Sun className="size-3.5 dark:hidden" />
          </Button>
        </Tip>
      </header>

      {/* The connection, as a ruled band rather than a chip — a square dot,
          the server's own words in the mono face, and the state as a label
          hard against the right edge. */}
      <div className="mx-3 mt-3 flex items-center gap-2 rounded-xl border border-line/70 bg-white/45 px-3 py-2 shadow-sm dark:bg-white/[.025]">
        <span className={cn('size-2 shrink-0 rounded-full shadow-sm', dot)} />
        <span className="min-w-0 truncate font-mono text-[10.5px] text-muted">
          {statusText}
        </span>
        <span className="flex-1" />
        <span className="label-2xs shrink-0 text-muted2">{status}</span>
      </div>

      <SectionLabel className="border-b border-line2 px-[14px] py-[9px]"
                    right={<span className="font-mono text-[10px] font-normal
                                            tracking-normal text-muted2">
                             {agentMode ? 4 : 2}
                           </span>}>
        Models
      </SectionLabel>
      {!agentMode ? (
        <>
          <ModelPicker label="Refine" value={models.refine} options={cat.local}
                       onChange={id => pick('refine', id)} />
          <ModelPicker label="Build" value={models.build} options={cat.local}
                       onChange={id => pick('build', id)} />
        </>
      ) : (
        <>
          <ModelPicker label="Agent" value={models.agent} options={cat.all}
                       onChange={id => pick('agent', id)}
                       hint={hasVision(cat.all, models.agent)
                         ? 'Accepts images — the pencil can send it a capture'
                         : 'No vision: a vision model is borrowed for pencil captures'} />
          <ModelPicker label="QA" value={models.qa} placeholder="same as agent"
                       options={[{ id: '', label: 'same as agent', icon: '↳',
                                   desc: 'Follow whatever the agent model is.' },
                                 ...cat.all]}
                       onChange={id => pick('qa', id)}
                       hint={isCloud(qaEffective)
                         ? `QA runs on ${qaEffective} — unit tests, their repair and the signed-in sweep.`
                         : `${qaEffective} is local and QA is cloud-only, so those stages will not run.`} />
          <ModelPicker label="SRS" value={models.srs} placeholder="same as agent"
                       options={[{ id: '', label: 'same as agent', icon: '↳',
                                   desc: 'Follow whatever the agent model is.' },
                                 ...cat.all]}
                       onChange={id => pick('srs', id)}
                       hint={srsRoomy
                         ? `The interview and the SRS run on ${srsEffective}.`
                         : `${srsEffective} has a ${maxContext(cat.all, srsEffective).toLocaleString()}-token window — the SRS carries the whole interview and will be truncated.`} />
          <ModelPicker label="Deploy" value={models.deploy} placeholder="same as agent"
                       options={[{ id: '', label: 'same as agent', icon: '↳',
                                   desc: 'Follow whatever the agent model is.' },
                                 ...cat.all]}
                       onChange={id => pick('deploy', id)}
                       hint={deployRoomy
                         ? `The deployment plan is written by ${deployEffective}.`
                         : `${deployEffective} has a ${maxContext(cat.all, deployEffective).toLocaleString()}-token window — a plan it cannot finish will not deploy.`} />
          {/* The picture generator sits with the other model choices rather
              than only behind the Images switch: the switch says whether to
              generate at all, this says what generates. */}
          <ModelPicker label="Image" value={models.image} placeholder="Fooocus"
                       options={[
                         { id: 'fooocus', label: 'Fooocus', icon: '🖼',
                           desc: 'The local generator on :7865.' },
                         { id: '', label: 'placeholders only', icon: '↳',
                           desc: 'Do not generate — the build uses drawn placeholders.' },
                       ]}
                       onChange={id => pick('image', id)}
                       hint={models.image === 'fooocus'
                         ? (fooocus?.available
                             ? 'Fooocus is up — pictures are generated for the build.'
                             : 'Fooocus is not running yet; turn Images on below to start it.')
                         : 'No generator: every picture is a drawn placeholder.'} />
        </>
      )}

      {/* Three switches as one band across the rail, spaced rather than
          ruled: the live one is the raised ground, and the band ends where
          its own padding ends. The 2px rule that used to close it off is
          gone with the hairlines inside it. */}
      <Seg block className="mb-1">
        <Tip className="flex-1" text="Run the whole build as one agent">
          <SegOpt block on={agentMode}
                  onClick={() => toggle('agentMode', KEYS.agentMode)}>
            Agent
          </SegOpt>
        </Tip>
        <Tip className="flex-1"
             text="A reasoning pass costs real time on a twenty-file build">
          <SegOpt block on={think} onClick={() => toggle('think', KEYS.think)}>
            Think
          </SegOpt>
        </Tip>
        <Tip className="flex-1" text={fooocusTip(fooocus, images)}>
          <SegOpt block on={images} onClick={toggleImages}>
            Images
            {images && (
              <span className={cn('inline-block size-[5px] align-middle',
                fooocus?.checking ? 'animate-pulse bg-bg/70'
                  : fooocus?.available ? 'bg-bg'
                  : 'bg-bg/30')} />
            )}
          </SegOpt>
        </Tip>
      </Seg>

      {images && fooocus && !fooocus.checking && !fooocus.available && (
        <p className="border-b border-line2 px-[14px] py-1.5 text-[10.5px]
                      leading-snug text-muted">
          No Fooocus is answering
          {fooocus.can_start ? (<>
            {' — '}
            <button onClick={startFooocus}
                    className="font-semibold text-accent underline
                               underline-offset-2 hover:text-ink">
              start it
            </button>
          </>) : ' — start it, or set its address in Settings'}
        </p>
      )}

      <SectionLabel className="border-b border-line2 px-[14px] py-[9px]"
                    right={<span className="font-mono text-[10px] font-normal
                                            tracking-normal text-muted2">
                             {String(projects.length).padStart(2, '0')}
                           </span>}>
        Projects
      </SectionLabel>

      {projects.length > 6 && (
        <div className="relative border-b border-line2 p-2">
          <Search className="pointer-events-none absolute left-[11px] top-1/2 size-3
                             -translate-y-1/2 text-muted2" />
          <Input value={q} onChange={e => setQ(e.target.value)} placeholder="Filter…"
                 className="pl-[26px]" />
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {shown.map((p, i) => {
          const name = p.name || p
          const on = project === name
          const asking = confirming === name
          const busyHere = removing === name
          return (
            // A row, not a button — the delete control lives inside it and a
            // <button> inside a <button> is invalid HTML that React will not
            // render as written.
            //
            // The open one is marked by a 3px accent edge on the left and a
            // filled ground, not by tinted text: the list has to stay a
            // Keep the active settings row visible.
            // A rule under every row turned the list into a table. Space
            // separates them now, the name carries a little more weight, and
            // the meta line drops the monospace — the numerals still get it,
            // where they are worth aligning.
            <div key={name}
                 className={cn('group grid w-full grid-cols-[26px_1fr_auto]',
                   'items-center gap-2.5 rounded-[14px] border-l-[3px]',
                   'py-[11px] pl-[7px] pr-[14px] transition-colors',
                   asking ? 'border-l-accent bg-tint'
                          : on ? 'border-l-accent bg-panel2'
                               : 'border-l-transparent hover:bg-panel2')}>
              <span className={cn('font-mono text-[10.5px] tabular-nums',
                                  on || asking ? 'text-accent' : 'text-faint')}>
                {String(i + 1).padStart(2, '0')}
              </span>
              <button onClick={() => onOpen(name)} disabled={asking || busyHere}
                      className="min-w-0 text-left">
                <span className={cn('block truncate text-[13.5px] leading-tight',
                                    'tracking-[-.012em] text-ink',
                                    on ? 'font-extrabold' : 'font-semibold')}>
                  {p.title || name}
                </span>
                <span className={cn('mt-[3px] block truncate text-[11px] leading-tight',
                                    asking ? 'text-deep' : 'text-muted2')}>
                  {asking ? 'delete this and its database?'
                          : `${p.stack || 'next'}${p.file_count ? ` · ${p.file_count} files` : ''}`}
                </span>
              </button>

              {asking ? (
                <span className="flex shrink-0 items-center">
                  <button onClick={() => remove(name)} disabled={busyHere}
                          className="border border-accent bg-accent px-[6px] py-px
                                     font-mono text-[10px] text-bg transition-colors
                                     hover:bg-press disabled:opacity-50">
                    {busyHere ? '…' : 'delete'}
                  </button>
                  <button onClick={() => setConfirming('')} disabled={busyHere}
                          className="px-1.5 font-mono text-[10px] text-muted
                                     hover:text-ink">
                    keep
                  </button>
                </span>
              ) : (
                <span className="flex shrink-0 items-center gap-1.5">
                  <DeployTag deployed={p.deployed} />
                  {p.unfinished ? <Badge tone="bad">{p.unfinished}</Badge> : null}
                  {/* Only on hover, and it asks before it acts. This is the one
                      control in the studio that destroys work that cannot be
                      got back, so it does not sit where a mis-click can find
                      it and it never fires on the first press. */}
                  <Tip text={`Delete ${name} from disk`}>
                    <button onClick={() => setConfirming(name)}
                            className="shrink-0 p-0.5 text-muted2 opacity-0
                                       transition-opacity hover:text-accent
                                       group-hover:opacity-100">
                      <Trash2 className="size-3" />
                    </button>
                  </Tip>
                </span>
              )}
            </div>
          )
        })}
        {!shown.length && (
          <p className="px-[14px] py-3 text-[11.5px] text-muted">
            {projects.length ? `Nothing matches “${q}”.` : 'No projects yet.'}
          </p>
        )}
      </div>

      {/* Five equal cells divided by rules — the rail's bottom edge, drawn
          strong, and the tools sitting in it rather than on it. */}
      <footer className="flex items-stretch border-t-2 border-line2">
        <input ref={folderRef} type="file" hidden
               webkitdirectory="" directory="" multiple
               onChange={e => {
                 const list = e.target.files
                 e.target.value = ''
                 if (list?.length) onImport(list)
               }} />
        <Foot icon={FolderUp} tip="Import a project folder"
              onClick={() => folderRef.current?.click()} />
        <Foot icon={Play} tip="Resume this build where it stopped"
              disabled={!project || status === 'busy'} onClick={onResume} />
        <Foot icon={Settings} tip="Settings" onClick={onSettings} />
        <Foot icon={Download} tip="Download this project as a zip"
              disabled={!project} onClick={onZip} />
        <Foot icon={ExternalLink} tip="Open the app in a new tab"
              disabled={!project} onClick={() => window.open('/', '_blank')} />
      </footer>
    </aside>
  )
}


function DeployTag({ deployed }) {
  if (!deployed) return null
  const gone = deployed.state === 'deleted'
  const where = deployed.target?.startsWith('aws') ? 'aws'
              : deployed.target === 'vercel' ? 'vercel'
              : ''
  return (
    <Tip text={gone ? 'Deployed, then deleted'
                    : `Deployed${where ? ` to ${where}` : ''}`}>
      {/* Where the work has been sent to is the one label in the rail that
          gets a solid accent fill — it is the only fact here about somewhere
          other than this machine. */}
      <Tag tone={gone ? 'mute' : 'solid'}>{gone ? 'gone' : (where || 'deployed')}</Tag>
    </Tip>
  )
}

function fooocusTip(state, on) {
  if (!on) return 'Generate pictures, and offer a logo before the build'
  if (!state || state.checking) return 'Looking for Fooocus…'
  if (state.available) return `Fooocus is answering at ${state.host}`
  if (state.error) return `Could not ask the server — ${state.error}`
  return 'No Fooocus is answering — pictures will be skipped'
}

const Foot = ({ icon: Icon, tip, ...rest }) => (
  <Tip className="flex-1 border-r border-line2 last:border-r-0" text={tip}>
    <button {...rest}
            className="grid h-[34px] w-full place-items-center text-ink
                       transition-colors hover:bg-ink/[.07]
                       disabled:pointer-events-none disabled:text-faint">
      <Icon className="size-3.5" />
    </button>
  </Tip>
)

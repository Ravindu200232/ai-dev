'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeft, ArrowRight, Check, FileText, Loader2, Paperclip, SkipForward,
  Sparkles,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useStore } from '@/lib/store'
import { TYPE_ANOTHER } from '@/lib/srs-constants'
import { useAttachments } from '@/lib/use-attachments'
import { AttachButtons, AttachList } from './Attachments'
import { Button, TextArea } from '../ui'
import { cn } from '@/lib/utils'

export default function Interview({ projectId, onDone, onCancel }) {
  const addLog = useStore(s => s.addLog)
  const [state, setState] = useState({ question: null, transcript: [], answers: [], done: false })
  const [phase, setPhase] = useState('loading')
  const [typing, setTyping] = useState(false)
  const [text, setText] = useState('')
  const [picked, setPicked] = useState([])
  const [error, setError] = useState('')
  const composer = useRef(null)
  const tail = useRef(null)
  const attach = useAttachments()

  async function refresh() {
    try {
      const next = await api.srs(`/projects/${projectId}/interview`)
      setState(next)
      setTyping(false)
      setText('')
      if (next.done || !next.question) return onDone?.(next)
      setPhase('asking')
    } catch (e) {
      if (/404|not found/i.test(e.message || '')) return onCancel?.()
      setError(e.message)
      setPhase('error')
    }
  }

  useEffect(() => { refresh() }, [projectId])
  useEffect(() => { tail.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [state.question?.id, state.answers?.length])
  useEffect(() => { if (typing) composer.current?.focus() }, [typing])

  const q = state.question
  const options = q?.options?.length ? q.options : (q?.suggested_options || []).map(o => ({ label: o, value: o }))
  const multi = /multi/.test(q?.answer_type || '')
  const prefill = q?.prefill || []
  const recommended = q?.recommended

  useEffect(() => {
    setPicked(multi ? prefill.map(String) : [])
    setText(!options.length && prefill.length ? String(prefill[0]) : '')
  }, [q?.id])

  async function answer(payload) {
    setPhase('sending')
    setError('')
    try {
      let attachments = []
      if (attach.items.length) {
        const uploaded = await attach.upload(projectId)
        attachments = uploaded.ids || []
        const said = payload.value != null || (payload.selected || []).length || String(payload.text || '').trim()
        if (!attachments.length && !said) {
          setError('That attachment could not be read. Try it again, or answer in text.')
          setPhase('asking')
          return
        }
      }
      await api.srs(`/projects/${projectId}/interview/answer`, attachments.length ? { ...payload, attachments } : payload)
      attach.reset()
      await refresh()
    } catch (e) {
      setError(e.message)
      addLog('WARN', `The SRS could not record that answer — ${e.message}`)
      setPhase('asking')
    }
  }

  const history = useMemo(() => {
    const byId = Object.fromEntries((state.answers || []).map(a => [a.question_id, a]))
    return (state.transcript || []).filter(row => byId[row.id]).map(row => ({ row, answer: byId[row.id] }))
  }, [state.transcript, state.answers])
  const answered = (state.answers || []).length
  const total = Math.max((state.transcript || []).length, answered + 1, 8)

  if (phase === 'loading') return <div className="grid min-h-0 flex-1 place-items-center"><Waiting>Preparing your interview…</Waiting></div>
  if (phase === 'error' && !q) return (
    <div className="grid min-h-0 flex-1 place-items-center p-8">
      <div className="max-w-[460px] rounded-[28px] bg-white/75 p-6 shadow-xl ring-1 ring-line dark:bg-white/[.04]">
        <p className="text-[13px] text-ink">The interview could not start.</p>
        <p className="mt-2 text-[12px] text-muted">{error}</p>
        <Button variant="outline" className="mt-4" onClick={onCancel}>Back</Button>
      </div>
    </div>
  )

  return (
    <div className="srs-messenger flex min-h-0 flex-1 flex-col bg-[radial-gradient(circle_at_50%_0%,rgba(93,106,251,.11),transparent_34%),linear-gradient(180deg,rgba(255,255,255,.35),transparent)]">
      <header className="flex shrink-0 items-center gap-3 px-7 py-4">
        <button onClick={onCancel} className="grid size-9 place-items-center rounded-full text-muted transition hover:bg-black/[.05] hover:text-ink dark:hover:bg-white/[.06]"><ArrowLeft className="size-4" /></button>
        <div>
          <p className="text-[14px] font-semibold text-ink">Plan conversation</p>
          <p className="text-[10.5px] text-muted">Question {answered + 1} of about {total}</p>
        </div>
        <span className="flex-1" />
        <div className="hidden items-center gap-1.5 sm:flex">
          {Array.from({ length: Math.min(total, 10) }).map((_, i) => <span key={i} className={cn('h-1.5 rounded-full transition-all', i < answered ? 'w-5 bg-ok' : i === answered ? 'w-8 bg-accent' : 'w-3 bg-line2')} />)}
        </div>
        <button onClick={() => onDone?.(state)} className="ml-2 inline-flex h-9 items-center gap-2 rounded-full bg-black/[.045] px-3.5 text-[11px] font-semibold text-ink transition hover:bg-black/[.08] dark:bg-white/[.06] dark:hover:bg-white/[.1]"><SkipForward className="size-3.5" /> Draft plan now</button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">
        <div className="mx-auto max-w-[820px] py-3">
          <div className="mb-7 flex justify-center">
            <span className="rounded-full bg-white/65 px-3 py-1 text-[10px] font-medium text-muted shadow-sm ring-1 ring-line/70 dark:bg-white/[.04]">AgentForge asks only what it needs to build the app correctly.</span>
          </div>

          {history.map(({ row, answer: a }, i) => (
            <div key={row.id || i} className="mb-7">
              <Message side="left" label="AgentForge">{row.question}</Message>
              <Message side="right" label="You">{said(a)}</Message>
            </div>
          ))}

          {q && (
            <div className="mb-5">
              <Message side="left" label="AgentForge" current>
                <span className="block text-[16px] font-semibold leading-[1.45] text-ink">{q.question}</span>
                {q.why_needed && <span className="mt-2 block text-[11.5px] leading-relaxed text-muted">{q.why_needed}</span>}
                {q.prefill_note && <span className="mt-2 block text-[11px] text-accent">You previously said “{q.prefill_note}”.</span>}
              </Message>
            </div>
          )}

          {!typing && options.length > 0 && (
            <div className="ml-11 max-w-[680px]">
              <div className="flex flex-wrap gap-2">
                {options.map((o, i) => {
                  const value = o.value ?? o.label
                  const known = prefill.map(String).includes(String(value))
                  const chosen = picked.includes(value) || (!multi && known)
                  const suggested = o.suggested || value === recommended
                  return (
                    <button key={`${value}-${i}`} disabled={phase === 'sending'} onClick={() => {
                      if (value === TYPE_ANOTHER) return setTyping(true)
                      if (multi) return setPicked(p => p.includes(value) ? p.filter(x => x !== value) : [...p, value])
                      answer({ key: q.id, value, selected: [String(value)] })
                    }} className={cn('rounded-full px-4 py-2 text-[12px] font-medium shadow-sm ring-1 transition-all disabled:opacity-45', chosen ? 'bg-accent text-white ring-accent' : 'bg-white/80 text-ink ring-line hover:ring-accent/40 dark:bg-white/[.05]')}>
                      {o.label ?? String(value)}
                      {(suggested || known) && <span className={cn('ml-2 text-[9px]', chosen ? 'text-white/70' : 'text-muted2')}>{known ? 'from your brief' : 'suggested'}</span>}
                    </button>
                  )
                })}
              </div>
              <div className="mt-3 flex items-center gap-3">
                {multi && <Button variant="solid" disabled={!picked.length || phase === 'sending'} onClick={() => answer({ key: q.id, value: picked, selected: picked.map(String) })}>Continue with {picked.length}</Button>}
                <button onClick={() => setTyping(true)} className="text-[11px] font-medium text-muted hover:text-ink">Write a different answer</button>
              </div>
            </div>
          )}

          {(typing || options.length === 0) && (
            <div className="ml-auto mt-4 max-w-[690px] rounded-[26px] bg-white/82 p-3 shadow-[0_16px_45px_rgba(15,23,42,.09)] ring-1 ring-line/70 backdrop-blur-xl dark:bg-white/[.045]">
              <TextArea ref={composer} value={text} rows={3} placeholder="Type your answer…" onChange={e => setText(e.target.value)} onKeyDown={e => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && text.trim()) answer({ key: q.id, value: text.trim(), text: text.trim() })
              }} className="w-full resize-none bg-transparent px-2.5 py-2 text-[13px] leading-relaxed text-ink outline-none placeholder:text-muted2" />
              <AttachList attach={attach} className="mx-2 mb-2" />
              <div className="flex items-center gap-2 border-t border-line/70 px-1 pt-2.5">
                <AttachButtons attach={attach} disabled={phase === 'sending'} />
                <span className="flex-1" />
                {options.length > 0 && <button onClick={() => { setTyping(false); setText('') }} className="rounded-full px-3 py-2 text-[11px] text-muted hover:bg-black/[.04] hover:text-ink dark:hover:bg-white/[.05]">Options</button>}
                <button disabled={phase === 'sending' || (!text.trim() && !attach.items.length)} onClick={() => answer({ key: q.id, value: text.trim() || null, text: text.trim() })} className="inline-flex h-9 items-center gap-2 rounded-full bg-accent px-4 text-[11.5px] font-semibold text-white shadow-[0_8px_20px_rgba(93,106,251,.25)] transition hover:bg-press disabled:opacity-40">
                  {phase === 'sending' ? <Loader2 className="size-3.5 animate-spin" /> : <ArrowRight className="size-3.5" />} Send
                </button>
              </div>
            </div>
          )}

          {error && <p className="ml-auto mt-3 max-w-[690px] rounded-2xl bg-warn-tint px-4 py-3 text-[11px] text-warn">{error}</p>}
          <div ref={tail} />
        </div>
      </div>

      <footer className="shrink-0 border-t border-line/60 bg-white/38 px-6 py-3 backdrop-blur-xl dark:bg-white/[.02]">
        <div className="mx-auto flex max-w-[820px] items-center gap-2 text-[10.5px] text-muted">
          <Sparkles className="size-3.5 text-accent" /> Your answers become the implementation contract. You can review the full plan before anything is built.
          <span className="flex-1" />
          <button disabled={answered === 0} onClick={() => onDone?.(state)} className="inline-flex items-center gap-1.5 rounded-full bg-black/[.05] px-3 py-1.5 font-medium text-ink disabled:opacity-40 dark:bg-white/[.06]"><FileText className="size-3" /> Review plan</button>
        </div>
      </footer>
    </div>
  )
}

function Message({ side, label, current, children }) {
  const right = side === 'right'
  return (
    <div className={cn('flex items-end gap-2.5', right && 'justify-end')}>
      {!right && <span className="grid size-8 shrink-0 place-items-center rounded-full bg-accent/10 text-[11px] font-bold text-accent">AF</span>}
      <div className={cn('max-w-[72%]', right && 'text-right')}>
        <div className="mb-1 px-1 text-[9.5px] font-semibold uppercase tracking-[.12em] text-muted2">{label}</div>
        <div className={cn('inline-block rounded-[22px] px-4 py-3 text-left text-[12.5px] leading-relaxed shadow-sm', right ? 'rounded-br-[7px] bg-accent text-white' : current ? 'rounded-bl-[7px] bg-white/88 text-ink ring-1 ring-accent/15 dark:bg-white/[.055]' : 'rounded-bl-[7px] bg-white/70 text-ink ring-1 ring-line/60 dark:bg-white/[.035]')}>{children}</div>
      </div>
    </div>
  )
}

function said(a) {
  if (!a) return ''
  const v = Array.isArray(a.value) ? a.value.join(', ') : a.value
  return v != null && String(v).trim() ? String(v) : (a.raw_text || 'Answered')
}

export function Waiting({ children, sub }) {
  return (
    <div className="flex items-center gap-3 rounded-[24px] bg-white/75 px-6 py-5 shadow-xl ring-1 ring-line/70 dark:bg-white/[.045]">
      <Loader2 className="size-5 shrink-0 animate-spin text-accent" />
      <div><p className="text-[13px] font-medium text-ink">{children}</p>{sub && <p className="mt-1 text-[11px] text-muted">{sub}</p>}</div>
    </div>
  )
}

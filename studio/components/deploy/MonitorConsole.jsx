'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle, Bot, ChevronDown, ChevronUp, ScrollText, SquareTerminal, Sparkles,
} from 'lucide-react'
import { Badge, Button } from '../ui'
import { cn } from '@/lib/utils'


const CHANNELS = [
  { id: 'logs', label:'Live Logs', Icon: ScrollText,
    match: e => e.type === 'log' },
  { id: 'events', label:'Agent Events', Icon: Bot,
    match: e => ['step', 'state', 'monitor', 'change_set'].includes(e.type) },
  { id: 'terminal', label:'Terminal', Icon: SquareTerminal,
    match: e => e.type === 'terminal' },
  { id: 'prompt', label:'Prompt Trace', Icon: Sparkles,
    match: e => e.type === 'prompt' },

  { id: 'errors', label:'Errors', Icon: AlertTriangle,
    match: e => ['failed', 'error'].includes(String(e.status || '').toLowerCase()) },
]

export function MonitorConsole({ events, snapErrors }) {
  const [open, setOpen] = useState('')

  const [floor, setFloor] = useState(0)
  const body = useRef(null)

  const rows = useMemo(() => (events || []).filter(e => (e.event_id || 0) > floor),
                       [events, floor])

  const counts = useMemo(() => {
    const out = {}
    for (const c of CHANNELS) out[c.id] = rows.filter(c.match).length

    out.errors += (snapErrors || []).length
    return out
  }, [rows, snapErrors])

  const shown = open ? rows.filter(CHANNELS.find(c => c.id === open).match) : []

  useEffect(() => {
    if (open && body.current) body.current.scrollTop = body.current.scrollHeight
  }, [open, shown.length])

  return (
    <div className="sticky bottom-0 z-10 border-t-2 border-line2 bg-panel">
      {open && (
        <div ref={body}
             className="max-h-[240px] overflow-auto border-b border-line2 bg-panel2
                        px-4 py-2">
          {open === 'errors'
            ? <Errors rows={shown} snapErrors={snapErrors} />
            : open === 'terminal'
              ? <Terminal rows={shown} />
              : <Lines rows={shown} />}
        </div>
      )}

      {/* Deployment event filters. */}
      <div className="flex items-stretch">
        {CHANNELS.map(({ id, label, Icon }) => {
          const n = counts[id] || 0
          const active = open === id
          return (
            <button key={id}
                    onClick={() => setOpen(active ? '' : id)}
                    className={cn('flex shrink-0 items-center gap-1.5 border-r',
                      'border-line px-3 py-2 font-display text-[10.5px]',
                      'font-extrabold uppercase tracking-[.08em] transition-colors',
                      active ? 'bg-accent text-bg'
                             : 'text-label hover:bg-ink/[.07] hover:text-ink')}>
              <Icon className="size-3" />
              {label}
              {n > 0 && (
                <Badge tone={active ? 'ok' : id === 'errors' ? 'bad' : 'mute'}>{n}</Badge>
              )}
            </button>
          )
        })}
        <span className="flex-1" />
        <span className="flex shrink-0 items-center gap-1 px-2">
          <Button size="sm" variant="ghost"
                  disabled={!rows.length}
                  onClick={() => setFloor(Math.max(...(events || []).map(e => e.event_id || 0), 0))}
                  title="Hide everything received so far. The run's own record is not changed.">
            Clear
          </Button>
          {open
            ? <Button size="icon-sm" variant="ghost" onClick={() => setOpen('')}
                      title="Collapse">
                <ChevronDown className="size-3" />
              </Button>
            : <ChevronUp className="size-3 text-muted2" />}
        </span>
      </div>
    </div>
  )
}

function Lines({ rows }) {
  if (!rows.length) return <Quiet />
  return (
    <div className="font-mono text-[10.5px] leading-relaxed">
      {rows.map(e => (
        <div key={e.event_id} className="flex gap-2 border-b border-line py-px last:border-0">
          <span className="shrink-0 text-muted2">{clock(e.timestamp)}</span>
          <span className="w-[68px] shrink-0 truncate text-accent" title={e.stage}>
            {e.stage}
          </span>
          <span className={cn('min-w-0 flex-1',
            String(e.status).toLowerCase() === 'complete' ? 'text-ok' : 'text-muted')}>
            {e.message}
          </span>
          {Number(e.percent) > 0 && (
            <span className="shrink-0 text-muted2">{e.percent}%</span>
          )}
        </div>
      ))}
    </div>
  )
}

function Terminal({ rows }) {
  if (!rows.length) return <Quiet />
  return (
    <div className="space-y-2">
      {rows.map(e => (
        <div key={e.event_id}>
          <p className="flex items-center gap-2 font-mono text-[10.5px]">
            <span className="text-muted2">{clock(e.timestamp)}</span>
            <span className="text-accent">$</span>
            <span className="min-w-0 flex-1 truncate text-ink">{e.message}</span>
          </p>
          {e.data?.output && (
            <pre className="mt-1 whitespace-pre-wrap border border-line2
                            bg-panel p-2 font-mono text-[10px] leading-relaxed text-muted">
              {e.data.output}
            </pre>
          )}
        </div>
      ))}
    </div>
  )
}

function Errors({ rows, snapErrors }) {
  const extra = snapErrors || []
  if (!rows.length && !extra.length) {
    return <p className="py-3 text-[11px] text-ok">No errors on this run.</p>
  }
  return (
    <div>
      {rows.map(e => (
        <p key={e.event_id} className="flex items-start gap-2 border-b border-line
                                       border-l-[3px] border-l-accent bg-tint px-2
                                       py-1 text-[11px] text-deep last:border-b-0">
          <AlertTriangle className="mt-px size-3 shrink-0" />
          <span className="font-mono text-[10px] text-muted2">{clock(e.timestamp)}</span>
          <span className="min-w-0">[{e.stage}] {e.message}</span>
        </p>
      ))}
      {extra.map((e, i) => (
        <p key={`snap-${i}`} className="flex items-start gap-2 border-b border-line
                                       border-l-[3px] border-l-accent bg-tint px-2
                                       py-1 text-[11px] text-deep last:border-b-0">
          <AlertTriangle className="mt-px size-3 shrink-0" />
          <span className="min-w-0">
            {typeof e === 'string' ? e : (e.message || JSON.stringify(e))}
          </span>
        </p>
      ))}
    </div>
  )
}

const Quiet = () => (
  <p className="py-3 text-[11px] text-muted2">
    Nothing on this channel — or it was cleared from the view.
  </p>
)

function clock(value) {
  if (!value) return '--:--:--'
  const t = typeof value === 'number' ? value : Date.parse(value)
  if (!t || Number.isNaN(t)) return '--:--:--'
  return new Date(t).toLocaleTimeString(undefined, { hour12: false })
}

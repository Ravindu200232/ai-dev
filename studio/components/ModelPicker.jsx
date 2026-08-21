'use client'

import { useMemo, useState } from 'react'
import { Check, ChevronsUpDown, Search } from 'lucide-react'
import { Dropdown, Tag, Tip } from './ui'
import { cn } from '@/lib/utils'

export default function ModelPicker({
  label, value, options, onChange, placeholder = 'choose…', hint,
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')

  const unique = useMemo(() => {
    const seen = new Set()
    return options.filter(m => {
      const k = m.id ?? ''
      if (seen.has(k)) return false
      seen.add(k)
      return true
    })
  }, [options])

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return unique
    return unique.filter(m => (m.id || '').toLowerCase().includes(needle)
                           || (m.label || '').toLowerCase().includes(needle))
  }, [unique, q])

  const current = unique.find(m => m.id === value)

  return (
    // The picker is a row of the rail, not a control sitting inside one: the
    // role reads as a fixed-width label at the left, the model fills the rest
    // in the mono face, and a 1px rule closes the row off from the next.
    <div className="relative">
      <Tip text={hint} className="w-full">
        <button onClick={(e) => { e.stopPropagation(); setOpen(o => !o); setQ('') }}
                className={cn('grid w-full grid-cols-[56px_1fr_auto] items-center',
                  'border-b border-line px-[14px] py-2 text-left transition-colors',
                  open ? 'bg-panel2' : 'hover:bg-ink/[.07]')}>
          <span className="label-2xs text-label">{label}</span>
          <span className="min-w-0">
            <span className={cn('block truncate font-mono text-[11px]',
                                value ? 'text-ink' : 'text-muted2')}>
              {value || placeholder}
            </span>
            {current?.ctx ? (
              <span className="mt-px block text-[10px] text-muted2">
                {Math.round(current.ctx / 1024)}k{current.vision ? ' · vision' : ''}
              </span>
            ) : null}
          </span>
          <ChevronsUpDown className="size-3 shrink-0 text-muted2" />
        </button>
      </Tip>

      <Dropdown open={open} onClose={() => setOpen(false)}
                className="left-2 right-2 top-[calc(100%-1px)]">
        <div className="flex items-center gap-[9px] border-b-2 border-line2
                        bg-panel2 px-3 py-[9px]">
          <Search className="size-[13px] shrink-0 text-label" />
          <input autoFocus value={q} placeholder="Search models…"
                 onChange={e => setQ(e.target.value)}
                 className="min-w-0 flex-1 bg-transparent text-[12px] text-ink
                            caret-accent outline-none placeholder:text-muted2" />
          <span className="shrink-0 font-mono text-[10px] text-muted2">
            {shown.length} of {unique.length}
          </span>
        </div>
        <div className="max-h-[320px] overflow-auto">
          {shown.map(m => (
            <button key={m.id || '__inherit'}
                    onClick={() => { onChange(m.id); setOpen(false) }}
                    className={cn('grid w-full grid-cols-[1fr_auto] items-center gap-2.5',
                      'border-b border-line border-l-[3px] py-2 pl-[9px] pr-3',
                      'text-left transition-colors',
                      m.id === value ? 'border-l-accent bg-panel2'
                                     : 'border-l-transparent hover:bg-ink/[.07]')}>
              <span className="min-w-0">
                <span className="block truncate font-mono text-[11.5px] text-ink">
                  {m.label || m.id}
                </span>
                <span className="mt-px flex flex-wrap items-center gap-1.5
                                 text-[10px] text-muted2">
                  {m.ctx ? <span>{Math.round(m.ctx / 1024)}k</span> : null}
                  {m.vision && <span>· vision</span>}
                  {m.tag && <Tag tone={TAG[m.tag]}>{m.tag}</Tag>}
                  {m.willPull && <Tag tone="accent">will pull</Tag>}
                </span>
                {m.desc && (
                  <span className="mt-0.5 block text-[10px] leading-snug text-muted">
                    {m.desc}
                  </span>
                )}
              </span>
              <Check className={cn('size-3.5 shrink-0 text-accent',
                                   m.id === value ? 'opacity-100' : 'opacity-0')} />
            </button>
          ))}
          {!shown.length && (
            <p className="px-3 py-3 text-[11.5px] text-muted">Nothing matches “{q}”.</p>
          )}
        </div>
      </Dropdown>
    </div>
  )
}

const TAG = { code: 'mute', cloud: 'accent', fast: 'mute', big: 'mute', local: 'mute' }

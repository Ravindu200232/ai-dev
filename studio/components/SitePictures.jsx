'use client'

import { useRef, useState } from 'react'
import { Image as ImageIcon, Loader2, Plus, Upload, X } from 'lucide-react'
import { api } from '@/lib/api'
import { Button } from './ui'
import { cn } from '@/lib/utils'
import { uniqueKey } from '@/lib/picture-keys'

const ACCEPT = '.png,.jpg,.jpeg,.webp,.gif,.bmp,image/*'

/**
 * Pictures the person already has, instead of ones the agent draws.
 *
 * Generating is still what happens by default — this is closed until it is
 * opened, and an empty list changes nothing about the build. What it buys is
 * the case the generator cannot serve: a real photograph of the real thing.
 * Anything uploaded here is written into `public/generated/<key>.png` before
 * the drawing pass runs, and that pass skips every key already on disk.
 */
export default function SitePictures({ pictures, onChange }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const picker = useRef(null)

  const rows = pictures || []

  async function add(files) {
    const chosen = Array.from(files || [])
    if (!chosen.length) return
    setBusy(true)
    setError('')
    const next = rows.slice()
    for (const file of chosen.slice(0, 24)) {
      const key = uniqueKey(file.name, next.map(r => r.key))
      try {
        const r = await api.imageUpload(file, { name: key })
        next.push({ key, file: r.file || '', from: file.name })
      } catch (e) {
        setError(e.message || 'That file could not be read.')
      }
    }
    onChange(next)
    setBusy(false)
  }

  function rename(index, value) {
    const next = rows.slice()
    const taken = next.filter((_, i) => i !== index).map(r => r.key)
    next[index] = { ...next[index], key: uniqueKey(value || 'picture', taken) }
    onChange(next)
  }

  if (!open && !rows.length) {
    return (
      <button onClick={() => setOpen(true)}
              className="mt-3 inline-flex items-center gap-1.5 text-[11.5px]
                         font-medium text-muted underline-offset-2
                         transition-colors hover:text-accent hover:underline">
        <Plus className="size-3.5" />
        Use my own pictures instead of drawn ones
      </button>
    )
  }

  return (
    <div className="mt-3 border border-line2 bg-panel2/40 p-3">
      <div className="flex items-center gap-2">
        <ImageIcon className="size-3.5 shrink-0 text-accent" />
        <span className="label-xs text-ink">Your own pictures</span>
        <span className="flex-1" />
        {!rows.length && (
          <Button variant="outline" size="icon" className="size-[24px]"
                  onClick={() => setOpen(false)} title="Never mind">
            <X className="size-3" />
          </Button>
        )}
      </div>

      <p className="mt-1.5 text-[11px] leading-relaxed text-muted">
        Optional. Anything you add here is used as-is and will not be drawn.
        Everything else the app needs is still generated for you. The name is
        what the page asks for, so <code className="font-mono">hero</code> is
        the banner and <code className="font-mono">room-deluxe</code> is that
        room&rsquo;s photo.
      </p>

      {rows.length > 0 && (
        <ul className="mt-3 border border-line2">
          {rows.map((row, i) => (
            <li key={`${row.from}-${i}`}
                className="flex flex-wrap items-center gap-2 border-b border-line
                           px-2.5 py-2 text-[11.5px] last:border-b-0">
              <input value={row.key}
                     onChange={e => rename(i, e.target.value)}
                     aria-label={`Picture name for ${row.from}`}
                     className="w-[168px] border border-line bg-bg px-2 py-1
                                font-mono text-[11px] text-ink outline-none
                                focus:border-accent" />
              <span className="min-w-0 flex-1 truncate text-muted"
                    title={row.from}>{row.from}</span>
              <Button variant="outline" size="icon" className="size-[24px]"
                      onClick={() => onChange(rows.filter((_, n) => n !== i))}
                      title={`Remove ${row.key}`}>
                <X className="size-3" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-2.5 flex items-center gap-2">
        <Button variant="outline" disabled={busy}
                onClick={() => picker.current?.click()}>
          {busy ? <Loader2 className="size-3.5 animate-spin" />
                : <Upload className="size-3.5" />}
          {busy ? 'Reading…' : rows.length ? 'Add more' : 'Choose pictures'}
        </Button>
        {rows.length > 0 && (
          <span className="text-[11px] text-muted">
            {rows.length} picture{rows.length === 1 ? '' : 's'} — the rest are drawn
          </span>
        )}
      </div>

      {error && (
        <p className={cn('mt-2 border-l-[3px] border-accent bg-tint px-2.5 py-1.5',
                         'text-[11.5px] text-deep')}>{error}</p>
      )}

      <input ref={picker} type="file" hidden multiple accept={ACCEPT}
             onChange={e => { const f = e.target.files; e.target.value = ''; add(f) }} />
    </div>
  )
}

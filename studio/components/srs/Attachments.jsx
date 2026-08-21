'use client'

/** File and voice input for the SRS brief. */
import { useRef } from 'react'
import { AlertTriangle, Check, FileText, Image as ImageIcon, Loader2, Mic,
         Paperclip, Square, X } from 'lucide-react'

import { ACCEPT_UPLOAD } from '@/lib/api'
import { useRecorder } from '@/lib/use-attachments'
import { Button } from '../ui'
import { cn } from '@/lib/utils'

const ICON = { document: FileText, picture: ImageIcon, recording: Mic, file: FileText }

function size(bytes) {
  if (!bytes) return ''
  return bytes < 1_000_000 ? `${Math.max(1, Math.round(bytes / 1000))} KB`
                           : `${(bytes / 1_000_000).toFixed(1)} MB`
}

function clock(s) {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** Compact controls for the brief footer or normal buttons elsewhere. */
export function AttachButtons({ attach, disabled, label = 'Attach', cell }) {
  const picker = useRef(null)
  const recorder = useRecorder(file => attach.add([file]))

  const cellClass = 'm-2 mr-0 inline-flex h-9 items-center gap-2 rounded-xl border border-line bg-white/70 '
                  + 'px-3 text-[11px] font-semibold text-ink shadow-sm transition-all hover:bg-white '
                  + 'disabled:pointer-events-none disabled:text-faint dark:bg-white/5 dark:hover:bg-white/10'

  return (
    <>
      <input ref={picker} type="file" multiple hidden accept={ACCEPT_UPLOAD}
             onChange={e => {
               attach.add(e.target.files)
               // Cleared so re-picking the same file fires change again.
               e.target.value = ''
             }} />

      {cell ? (<>
        <button disabled={disabled} className={cellClass}
                onClick={() => picker.current?.click()}
                title="Attach a PDF, document, screenshot or image">
          <Paperclip className="size-3.5" /> PDF / image
        </button>
        <button disabled={disabled} onClick={recorder.toggle}
                className={cn(cellClass, recorder.recording && 'border-accent bg-accent text-white')}
                title={recorder.recording ? 'Stop recording' : 'Describe the app by voice'}>
          {recorder.recording
            ? <><Square className="size-3 fill-current" /> Stop</>
            : <><Mic className="size-3.5" /> Voice</>}
        </button>
        {recorder.recording && (
          <span className="my-2 ml-2 flex items-center rounded-xl bg-accent/10 px-2.5
                           font-mono text-[10.5px] text-accent">
            {clock(recorder.seconds)}
          </span>
        )}
      </>) : (<>
        <Button variant="ghost" size="sm" disabled={disabled}
                onClick={() => picker.current?.click()}
                title="Attach a document, a photo or a recording — a price list, a form you use today, a screenshot of the old system">
          <Paperclip className="size-3" /> {label}
        </Button>

        <Button variant={recorder.recording ? 'solid' : 'ghost'} size="sm"
                disabled={disabled} onClick={recorder.toggle}
                title={recorder.recording ? 'Stop recording' : 'Say it instead of typing it'}>
          {recorder.recording
            ? <><Square className="size-2.5 fill-current" /> {clock(recorder.seconds)}</>
            : <><Mic className="size-3" /> Record</>}
        </Button>
      </>)}

      {recorder.error && (
        <span className={cn('flex items-center text-[10.5px] text-deep',
                            cell && 'border-r border-line2 px-2.5')}>
          {recorder.error}
        </span>
      )}
    </>
  )
}

export function AttachList({ attach, className }) {
  if (!attach.items.length) return null

  return (
    <ul className={cn('overflow-hidden rounded-2xl border border-line bg-white/45 shadow-sm dark:bg-white/[.02]', className)}>
      {attach.items.map(it => {
        const Icon = ICON[it.kind] || FileText
        const failed = it.state === 'failed'
        return (
          <li key={it.key}
              className={cn('flex items-start gap-2.5 border-b border-line/70 px-3 py-2.5 last:border-b-0',
                            failed ? 'bg-tint' : 'bg-transparent')}>
            <Icon className={cn('mt-px size-3 shrink-0',
                                failed ? 'text-accent' : 'text-muted')} />

            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="truncate text-[11.5px] text-ink">{it.name}</span>
                <span className="shrink-0 font-mono text-[10px] text-muted2">
                  {size(it.size)}
                </span>
                {it.state === 'reading' && (
                  <Loader2 className="size-2.5 shrink-0 animate-spin text-accent" />
                )}
                {it.state === 'done' && !it.note && (
                  <Check className="size-2.5 shrink-0 text-ok" />
                )}
                {(failed || (it.state === 'done' && it.note)) && (
                  <AlertTriangle className="size-2.5 shrink-0 text-warn" />
                )}
              </div>

              {/* A picture can be a logo, a screenshot of a layout somebody
                  wants copied, or a photo to put on a page — and the model's
                  description reads the same for all three. Only the person
                  knows which, so this is where they say it. */}
              {it.kind === 'picture' && it.state !== 'failed' && (
                <input
                  value={it.purpose || ''}
                  onChange={e => attach.describe(it.key, e.target.value)}
                  disabled={it.state === 'reading' || it.state === 'done'}
                  maxLength={300}
                  aria-label={`What ${it.name} is for`}
                  placeholder="What is this for? e.g. our logo · the layout I want · a photo for the home page"
                  className="mt-1.5 w-full rounded-lg border border-line bg-panel2/60
                             px-2 py-1 text-[11px] text-ink outline-none transition
                             placeholder:text-muted2 focus:border-accent/50
                             disabled:opacity-60" />
              )}
              {it.kind === 'picture' && it.state === 'done' && it.url && (
                <p className="mt-1 font-mono text-[10px] text-muted2">
                  saved at {it.url}
                </p>
              )}

              {it.read && (
                <p className="mt-0.5 line-clamp-2 text-[10.5px] leading-snug text-muted">
                  {it.read.slice(0, 220)}
                </p>
              )}
              {it.note && (
                <p className={cn('mt-0.5 text-[10.5px] leading-snug',
                                 failed ? 'text-deep' : 'text-warn')}>
                  {it.note}
                </p>
              )}
              {it.state === 'reading' && (
                <p className="mt-0.5 text-[10.5px] text-muted">
                  {it.kind === 'recording' ? 'Transcribing…'
                    : it.kind === 'picture' ? 'Showing it to the model…'
                    : 'Reading it…'}
                </p>
              )}
            </div>

            <button onClick={() => attach.remove(it.key)}
                    disabled={it.state === 'reading'}
                    title="Remove"
                    className="mt-px shrink-0 text-muted2 transition-colors
                               hover:text-ink disabled:opacity-30">
              <X className="size-3" />
            </button>
          </li>
        )
      })}
    </ul>
  )
}

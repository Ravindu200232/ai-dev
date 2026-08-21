'use client'

/**
 * Attach and record, for the editing chats.
 *
 * Deliberately smaller than the intake's version: those boxes have room to show
 * what was read back, and these sit in a one-line bar over the preview. What is
 * shown is the filename, whether it has been read, and a way to drop it — the
 * reading itself goes into the prompt, which is where it matters.
 */
import { useRef } from 'react'
import { FileText, Image as ImageIcon, Loader2, Mic, Paperclip, Square, X } from 'lucide-react'

import { ACCEPT_UPLOAD } from '@/lib/api'
import { useRecorder } from '@/lib/use-attachments'
import { Button } from './ui'
import { cn } from '@/lib/utils'

const PICTURE = /\.(png|jpe?g|webp|gif|bmp)$/i
const SOUND = /\.(wav|mp3|m4a|ogg|webm|flac)$/i

export default function EditAttach({ attach, disabled, className }) {
  const picker = useRef(null)
  const recorder = useRecorder(file => attach.add([file]))

  return (
    <div className={cn('flex flex-wrap items-center gap-1.5', className)}>
      <input ref={picker} type="file" multiple hidden accept={ACCEPT_UPLOAD}
             onChange={e => { attach.add(e.target.files); e.target.value = '' }} />

      <Button variant="ghost" size="sm" disabled={disabled}
              onClick={() => picker.current?.click()}
              title="Attach a picture, a document or a recording to this request">
        <Paperclip className="size-3" />
      </Button>

      <Button variant={recorder.recording ? 'solid' : 'ghost'} size="sm"
              disabled={disabled} onClick={recorder.toggle}
              title={recorder.recording ? 'Stop recording' : 'Say it instead of typing it'}>
        {recorder.recording
          ? <><Square className="size-2.5 fill-current" /> {recorder.seconds}s</>
          : <Mic className="size-3" />}
      </Button>

      {recorder.error && (
        <span className="text-[10px] text-deep">{recorder.error}</span>
      )}

      {attach.items.map(it => {
        const Icon = PICTURE.test(it.name) ? ImageIcon : SOUND.test(it.name) ? Mic : FileText
        const failed = it.state === 'failed'
        return (
          <span key={it.key}
                title={it.note || it.read?.slice(0, 300) || it.name}
                className={cn('inline-flex max-w-[210px] items-center gap-1',
                              'border px-1.5 py-0.5 text-[10.5px]',
                              failed ? 'border-accent bg-tint text-deep'
                                     : 'border-line2 bg-panel2 text-muted')}>
            {it.state === 'reading'
              ? <Loader2 className="size-2.5 shrink-0 animate-spin text-accent" />
              : <Icon className="size-2.5 shrink-0" />}
            <span className="truncate">{it.name}</span>
            <button onClick={() => attach.remove(it.key)}
                    disabled={it.state === 'reading'}
                    className="shrink-0 text-muted2 hover:text-ink disabled:opacity-30">
              <X className="size-2.5" />
            </button>
          </span>
        )
      })}
    </div>
  )
}

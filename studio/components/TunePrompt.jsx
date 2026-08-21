'use client'

/**
 * "Is this what you mean?" — the edit request, read back before it runs.
 *
 * What people type into the edit box is four or five words typed while
 * looking at the thing they mean: "blackground image include". Every word
 * they left out was in front of them, and the model never sees it. Measured
 * on a real edit, that request produced a background image AND an 80%-white
 * overlay laid on top of it — the picture landed, the page looked identical,
 * and the person had no way to know their request was the ambiguous part.
 *
 * So the request is said back properly first, and shown. Three ways out:
 * send it, change the words and ask again, or send exactly what was typed
 * (attachments and all — the box shows the words, not the paste).
 * Nothing runs until one of them is chosen — an edit is a rewrite of a real
 * file, and it is cheaper to read one sentence than to undo one.
 */
import { useEffect, useRef, useState } from 'react'
import { ArrowRight, Loader2, RefreshCw, X } from 'lucide-react'
import { Button, Modal } from './ui'

export default function TunePrompt({ typed, tuned, onSend, onSendTyped,
                                    onCancel, onRetune }) {
  const [text, setText] = useState(tuned || typed || '')
  const [busy, setBusy] = useState(false)
  const box = useRef(null)

  useEffect(() => { setText(tuned || typed || '') }, [tuned, typed])
  useEffect(() => {
    const t = setTimeout(() => box.current?.focus(), 30)
    return () => clearTimeout(t)
  }, [])

  const send = () => {
    const v = text.trim()
    if (v) onSend(v)
  }

  async function again() {
    const v = text.trim()
    if (!v || busy) return
    setBusy(true)
    try {
      const next = await onRetune(v)
      if (next) setText(next)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal onClose={onCancel} className="max-w-[620px] p-0">
      <header className="flex items-center gap-3 border-b-2 border-line2 px-5 py-3.5">
        <h3 className="font-display text-[15px] font-extrabold uppercase
                       tracking-[-.01em] text-ink">
          Is this what you mean?
        </h3>
        <span className="flex-1" />
        <Button variant="outline" size="icon" className="size-[26px]"
                onClick={onCancel} title="Cancel">
          <X className="size-3.5" />
        </Button>
      </header>

      <div className="px-5 py-4">
        <p className="label-2xs text-muted2">You typed</p>
        <p className="mt-1 border-l-[3px] border-line2 pl-2.5 text-[12px]
                      leading-snug text-muted">
          {typed}
        </p>

        <p className="label-2xs mt-4 text-muted2">Asking for</p>
        {/* Editable on purpose. If it read the request wrong, the fix is to
            say it differently — not to cancel and start over. */}
        <textarea
          ref={box}
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send()
          }}
          rows={4}
          disabled={busy}
          className="mt-1 w-full resize-y border border-line2 bg-canvas px-3 py-2.5
                     font-sans text-[13px] leading-relaxed text-ink outline-none
                     focus:border-ink disabled:opacity-50"
        />
        <p className="mt-1.5 font-mono text-[10px] text-muted2">
          {busy ? 'saying it again…' : 'edit it, ask again, or send it — ⌘/Ctrl+Enter sends'}
        </p>
      </div>

      <footer className="flex items-stretch border-t-2 border-line2">
        <button onClick={onSendTyped}
                className="inline-flex items-center px-5 py-3 font-display
                           text-[11.5px] font-extrabold text-ink transition-colors
                           hover:bg-ink/[.07]">
          Send what I typed
        </button>
        <span className="flex-1" />
        <button onClick={again} disabled={busy}
                className="inline-flex items-center gap-2 border-l border-line2
                           px-4 py-3 font-display text-[11.5px] font-extrabold
                           text-ink transition-colors hover:bg-ink/[.07]
                           disabled:pointer-events-none disabled:opacity-45">
          {busy ? <Loader2 className="size-3 animate-spin" />
                : <RefreshCw className="size-3" />}
          Ask again
        </button>
        <button onClick={send} disabled={busy || !text.trim()}
                className="inline-flex items-center gap-2 bg-accent px-5 py-3
                           font-display text-[11.5px] font-extrabold uppercase
                           tracking-[.06em] text-bg transition-colors
                           hover:bg-press disabled:pointer-events-none
                           disabled:opacity-45">
          Yes — do it
          <ArrowRight className="size-3.5" />
        </button>
      </footer>
    </Modal>
  )
}

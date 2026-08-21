'use client'

/**
 * Files attached to one of the editing chats — selection, section, or the
 * feature box.
 *
 * All three of those send a single instruction string to a model, so an
 * attachment becomes part of that string rather than a new message shape.
 * Nothing in `run_element_edit`, `run_pencil_edit` or `run_feature` changes,
 * and the three cannot drift apart as they would if each grew its own path.
 *
 * A picture is served both ways at once, because "put this on the page" and
 * "make it look like this" are equally likely and the difference is in the
 * words the user typed, not in the file. The server saves it where an `<img>`
 * can reach it AND reads it, so the block below carries the path and the
 * description together and the model picks the one the sentence asks for.
 */
import { useCallback, useRef, useState } from 'react'

import { api } from './api'

let seq = 0

export function useEditAttachments() {
  const [items, setItems] = useState([])
  const busy = useRef(false)

  const add = useCallback(files => {
    const fresh = Array.from(files || []).map(file => ({
      key: `ea-${++seq}`, file, name: file.name || 'upload',
      state: 'waiting', read: '', url: '', kind: '', note: '',
    }))
    if (fresh.length) setItems(list => [...list, ...fresh])
  }, [])

  const remove = useCallback(key => setItems(l => l.filter(i => i.key !== key)), [])
  const reset = useCallback(() => setItems([]), [])

  /** Read everything not yet read, and return the block to append. */
  const collect = useCallback(async project => {
    if (busy.current) return ''
    busy.current = true
    try {
      let queue = []
      setItems(list => {
        queue = list.filter(i => i.state !== 'done')
        return list.map(i => (i.state === 'done' ? i : { ...i, state: 'reading' }))
      })
      await Promise.resolve()

      for (const it of queue) {
        try {
          const r = await api.attach(it.file, { project })
          setItems(l => l.map(x => x.key === it.key
            ? { ...x, state: 'done', read: r.text || '', url: r.url || '',
                kind: r.kind || '', note: r.note || '' }
            : x))
          it.read = r.text || ''; it.url = r.url || ''
          it.kind = r.kind || ''; it.note = r.note || ''
        } catch (e) {
          setItems(l => l.map(x => x.key === it.key
            ? { ...x, state: 'failed', note: e.message } : x))
          it.failed = true
        }
      }

      let all = []
      setItems(list => { all = list; return list })
      await Promise.resolve()
      return blockFor(all)
    } finally {
      busy.current = false
    }
  }, [])

  return { items, add, remove, reset, collect,
           busy: items.some(i => i.state === 'reading') }
}

/**
 * The text appended to the instruction.
 *
 * Written as headed sections rather than pasted in raw, because the model has
 * to be able to tell the user's sentence from the contents of their file — and
 * for a picture it has to be told that the path is real and already on disk, or
 * it invents a different one or leaves a placeholder.
 */
export function blockFor(items) {
  const usable = (items || []).filter(i => i.read || i.url)
  if (!usable.length) return ''

  const parts = usable.map(i => {
    if (i.kind === 'image') {
      return `### ${i.name} — a picture, already saved at ${i.url}\n`
        + `If they asked for this picture to appear, use exactly that path in `
        + `an <img>; it exists on disk, so do not invent another and do not `
        + `leave a placeholder. What it shows:\n${i.read || '(could not be read)'}`
    }
    const what = i.kind === 'audio' ? 'a recording, transcribed'
      : i.kind === 'pdf' ? 'a document' : 'a file'
    return `### ${i.name} — ${what}\n${i.read}`
  })

  return `\n\n## What they attached\n\n${parts.join('\n\n')}`
}

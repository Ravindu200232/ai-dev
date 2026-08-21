'use client'

/** Files attached to an editing chat. */
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

/** The text appended to the instruction. */
function blockFor(items) {
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

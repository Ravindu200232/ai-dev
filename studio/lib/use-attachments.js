'use client'

/**
 * Files a customer attaches while describing their app, and the recorder that
 * makes one.
 *
 * Uploading is explicit rather than automatic. The first input box has no
 * project to attach anything to until "Plan it first" creates one, so the files
 * wait and are sent the moment the id exists — and the caller has to be able to
 * await that, because the questions the interview asks are generated from the
 * sources. An effect firing somewhere inside a child component cannot be
 * awaited, so `upload(projectId)` is a plain function the caller drives.
 *
 * Uploads run one at a time on purpose. Each one may put a scanned page or a
 * photo in front of a vision model, and three of those at once on a local
 * model is how you turn a working upload into a timeout.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { api, uploadMode } from './api'

let seq = 0

const KIND = { pdf: 'document', image: 'picture', voice: 'recording', text: 'file' }

export function describeFile(file) {
  return KIND[uploadMode(file)] || 'file'
}

export function useAttachments() {
  const [items, setItems] = useState([])

  const patch = useCallback((key, fields) => {
    setItems(list => list.map(it => (it.key === key ? { ...it, ...fields } : it)))
  }, [])

  const add = useCallback(files => {
    const fresh = Array.from(files || []).map(file => ({
      key: `at-${++seq}`,
      file,
      name: file.name || 'upload',
      size: file.size || 0,
      kind: describeFile(file),
      state: 'waiting',
      note: '',
      // What this file is for, in the person's own words. Blank is allowed —
      // it is a hint, not a gate.
      purpose: '',
      url: '',
      read: '',
      sourceId: '',
      // Which project the id above belongs to. A source id is meaningless to
      // any other project, so this is what "already sent" is measured against.
      sentTo: '',
    }))
    if (fresh.length) setItems(list => [...list, ...fresh])
    return fresh
  }, [])

  // The person tells us what a file is for; the model cannot work it out.
  const describe = useCallback((key, purpose) => patch(key, { purpose }), [patch])

  const remove = useCallback(key => {
    setItems(list => list.filter(it => it.key !== key))
  }, [])

  const reset = useCallback(() => setItems([]), [])

  /**
   * Send everything not already sent. Returns the source ids, which is what
   * `InterviewAnswer.attachments` wants.
   *
   * One file failing does not fail the rest: a customer who attaches a recording
   * and a price list, where only the recording cannot be decoded, should still
   * have their price list read.
   */
  const uploading = useRef(false)

  const upload = useCallback(async projectId => {
    if (!projectId) return { ids: [], failed: 0 }
    // Re-entrancy guard. Without it a second call sees files whose upload is
    // still in flight — they have no source id yet — and sends every one of
    // them again, duplicating the sources on the project.
    if (uploading.current) return { ids: [], failed: 0 }
    uploading.current = true

    try {
      let queue = []
      const already = []
      setItems(list => {
        // Assignment, not append: React may invoke an updater twice in
        // development, and appending would upload every file two times.
        //
        // Keyed on `sentTo`, not on `sourceId` alone. Going Back out of the
        // interview abandons the project but leaves this component mounted
        // with its files still listed, so the next "Plan it first" creates a
        // NEW project — and an id issued by the old one names nothing in it.
        // Testing `sourceId` sent those files nowhere, for ever.
        queue = list.filter(it => it.sentTo !== projectId)
        already.length = 0
        already.push(...list.filter(it => it.sentTo === projectId && it.sourceId)
                         .map(it => it.sourceId))
        return list.map(it => (it.sentTo === projectId
          ? it : { ...it, state: 'reading', note: '' }))
      })
      await Promise.resolve()   // let the "reading" paint land before the first await

      const ids = [...already]
      let failed = 0
      for (const it of queue) {
        try {
          const res = await api.srsUpload(projectId, it.file,
                                          { purpose: it.purpose || '' })
          const text = res?.source?.text || ''
          const id = res?.source?.id || ''
          if (id) ids.push(id); else failed++
          patch(it.key, {
            state: 'done',
            sourceId: id,
            sentTo: id ? projectId : '',
            read: text,
            // `note` is the extractor's own warning — "some pages are scans",
            // "read by OCR only". It is the difference between a file that was
            // read and one that was merely accepted, so it is shown rather than
            // swallowed.
            url: res?.url || '',
            note: res?.note || (text ? '' : 'Nothing could be read from this one.'),
          })
        } catch (e) {
          failed++
          patch(it.key, { state: 'failed', note: e.message || 'upload failed' })
        }
      }
      return { ids, failed }
    } finally {
      uploading.current = false
    }
  }, [patch])

  return {
    items,
    add,
    describe,
    remove,
    reset,
    upload,
    busy: items.some(it => it.state === 'reading'),
    waiting: items.filter(it => it.state === 'waiting').length,
  }
}

/**
 * Recording an answer instead of typing it.
 *
 * MediaRecorder's default container is whatever the browser prefers — webm on
 * Chromium, mp4 on Safari — so the type is read back off the recorder rather
 * than assumed, and the extension follows it. faster-whisper decodes both.
 */
export function useRecorder(onClip) {
  const [recording, setRecording] = useState(false)
  const [error, setError] = useState('')
  const [seconds, setSeconds] = useState(0)
  const rec = useRef(null)
  const ticker = useRef(null)
  const stream = useRef(null)
  const starting = useRef(false)

  // Kept in a ref so `start` stays stable: the caller passes an inline arrow,
  // which would otherwise rebuild the callback on every render.
  const clip = useRef(onClip)
  clip.current = onClip

  // Stopping the tracks is what turns the browser's recording indicator off.
  // This runs on unmount too, and unmount-while-recording is easy to reach:
  // pressing "Plan it first" mid-recording tears down the whole idle block.
  // Without it the microphone stays live for the rest of the session with no
  // way left in the UI to release it.
  const release = useCallback(() => {
    clearInterval(ticker.current)
    ticker.current = null
    stream.current?.getTracks().forEach(t => t.stop())
    stream.current = null
  }, [])

  useEffect(() => () => {
    try { if (rec.current?.state === 'recording') rec.current.stop() } catch { /* already gone */ }
    release()
  }, [release])

  const stop = useCallback(() => {
    if (rec.current && rec.current.state !== 'inactive') rec.current.stop()
  }, [])

  const start = useCallback(async () => {
    setError('')
    // `recording` only becomes true after getUserMedia resolves, and the first
    // use of the microphone puts a permission prompt in that gap. A second
    // click during it would open a second stream and overwrite the only
    // reference to the first, leaking it permanently.
    if (starting.current || rec.current?.state === 'recording') return
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      return setError('This browser cannot record audio.')
    }

    starting.current = true
    let media
    try {
      media = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (e) {
      starting.current = false
      return setError(e.name === 'NotAllowedError'
        ? 'Microphone permission was refused.'
        : `The microphone could not be opened — ${e.message}`)
    }

    const recorder = new MediaRecorder(media)
    const chunks = []
    recorder.ondataavailable = e => { if (e.data?.size) chunks.push(e.data) }
    recorder.onstop = () => {
      release()
      setRecording(false)
      setSeconds(0)
      const type = recorder.mimeType || 'audio/webm'
      const ext = type.includes('mp4') ? 'm4a' : type.includes('ogg') ? 'ogg' : 'webm'
      const blob = new Blob(chunks, { type })
      if (blob.size > 0) {
        clip.current(new File([blob], `recording-${Date.now()}.${ext}`, { type }))
      }
    }

    stream.current = media
    rec.current = recorder
    recorder.start()
    starting.current = false
    setRecording(true)
    setSeconds(0)
    ticker.current = setInterval(() => setSeconds(n => n + 1), 1000)
  }, [release])

  return { recording, seconds, error, start, stop, toggle: () => (recording ? stop() : start()) }
}

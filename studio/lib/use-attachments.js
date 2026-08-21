'use client'

/** Idea attachments and audio recording. */
import { useCallback, useEffect, useRef, useState } from 'react'

import { api, uploadMode } from './api'

let seq = 0

const KIND = { pdf: 'document', image: 'picture', voice: 'recording', text: 'file' }

function describeFile(file) {
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
      // What this file is for, in the person's own words.
      purpose: '',
      url: '',
      read: '',
      sourceId: '',
  // Project that owns the attachment id.
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

  /** Send everything not already sent. */
  const uploading = useRef(false)

  const upload = useCallback(async projectId => {
    if (!projectId) return { ids: [], failed: 0 }
    // Re-entrancy guard.
    if (uploading.current) return { ids: [], failed: 0 }
    uploading.current = true

    try {
      let queue = []
      const already = []
      setItems(list => {
        // Assignment, not append.
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
      // Keep extraction warnings such as scan or OCR notices.
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

/** Recording an answer instead of typing it. */
export function useRecorder(onClip) {
  const [recording, setRecording] = useState(false)
  const [error, setError] = useState('')
  const [seconds, setSeconds] = useState(0)
  const rec = useRef(null)
  const ticker = useRef(null)
  const stream = useRef(null)
  const starting = useRef(false)

  // Kept in a ref so `start` stays stable.
  const clip = useRef(onClip)
  clip.current = onClip

      // Stop the tracks to clear the recording indicator.
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

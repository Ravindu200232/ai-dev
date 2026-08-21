'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'

export function useRunData(runId) {
  const [data, setData] = useState({ events: [], artifacts: [], evidence: [] })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const inFlight = useRef(false)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])

  const load = useCallback(async () => {
    if (!runId || inFlight.current) return
    inFlight.current = true
    setBusy(true)
    setError('')

    const [events, artifacts, evidence] = await Promise.allSettled([
      api.deploy(`/runs/${runId}/events`),
      api.deploy(`/runs/${runId}/artifacts`),
      api.deploy(`/runs/${runId}/evidence`),
    ])
    if (!alive.current) { inFlight.current = false; return }
    const value = (r, key) => (r.status === 'fulfilled' && Array.isArray(r.value?.[key]))
      ? r.value[key] : []
    setData({
      runId,
      events: value(events, 'events'),
      artifacts: value(artifacts, 'artifacts'),
      evidence: value(evidence, 'evidence'),
    })

    const failed = [events, artifacts].find(r => r.status === 'rejected')
    if (failed) setError(String(failed.reason?.message || failed.reason))
    setBusy(false)
    inFlight.current = false
  }, [runId])

  useEffect(() => {
    setData({ events: [], artifacts: [], evidence: [] })
    load()
  }, [load])

  const mine = data.runId === runId ? data : { events: [], artifacts: [], evidence: [] }
  return { ...mine, busy, error, reload: load }
}

export function pipelineStages(events) {
  const steps = events.filter(e => e.type === 'step' || e.type === 'state')
  const byStage = new Map()
  for (const e of steps) {
    const stage = String(e.stage || '').trim()
    if (!stage) continue
    if (!byStage.has(stage)) {
      byStage.set(stage, { stage, first: e, last: e, messages: [], percent: 0 })
    }
    const row = byStage.get(stage)
    row.last = e
    row.percent = Math.max(row.percent, Number(e.percent) || 0)
    if (e.message) row.messages.push(String(e.message))
  }
  return [...byStage.values()].map(row => {
    const status = String(row.last.status || '')
    const started = Date.parse(row.first.timestamp)
    const ended = Date.parse(row.last.timestamp)
    const ms = (Number.isFinite(started) && Number.isFinite(ended) && ended > started)
      ? ended - started : null
    return {
      stage: row.stage,
      status,

      done: status === 'complete' || status === 'completed' || status === 'success',
      failed: status === 'failed' || status === 'error',
      percent: row.percent,
      ms,
      message: row.messages[row.messages.length - 1] || '',
      at: row.last.timestamp,
    }
  })
}

export function artifactsByKind(artifacts) {
  const groups = new Map()
  for (const a of artifacts) {
    const kind = String(a.kind || 'other')
    if (!groups.has(kind)) groups.set(kind, [])
    groups.get(kind).push(a)
  }
  return [...groups.entries()].map(([kind, items]) => ({ kind, items }))
}

export function size(bytes) {
  const n = Number(bytes)
  if (!Number.isFinite(n) || n <= 0) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

export function duration(ms) {
  if (!Number.isFinite(ms) || ms <= 0) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  const m = Math.floor(ms / 60000)
  return `${m}m ${Math.round((ms % 60000) / 1000)}s`
}

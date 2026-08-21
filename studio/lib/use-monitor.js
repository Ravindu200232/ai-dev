'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'
import { TERMINAL } from '@/lib/deploy-constants'


const CADENCE = { vercel: 20000, aws_ec2: 35000, aws_ecs: 40000 }
const REQUEST_DEADLINE = 90000

export function useMonitor(runId, { target, state, active, frozen } = {}) {
  const [snap, setSnap] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [at, setAt] = useState(null)
  const inFlight = useRef(false)
  const abort = useRef(null)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      abort.current?.abort()
    }
  }, [])

  const refresh = useCallback(async () => {
    if (!runId || inFlight.current) return
    inFlight.current = true
    setBusy(true)
    setError('')
    const ctl = new AbortController()
    abort.current = ctl
    const bell = setTimeout(() => ctl.abort(), REQUEST_DEADLINE)
    try {
      const d = await api.deployRead(`/runs/${runId}/monitor`, { signal: ctl.signal })
      if (alive.current) { setSnap(d); setAt(Date.now()) }
    } catch (e) {
      if (alive.current && e.message !== 'cancelled') setError(e.message)
    } finally {
      clearTimeout(bell)
      inFlight.current = false
      if (alive.current) setBusy(false)
    }
  }, [runId])

  // A snapshot belongs to the run it was fetched for.
  //
  // `refresh` stops when `runId` goes empty, but `snap` was never cleared — so
  // opening a project that has never been deployed kept showing the LAST
  // project's infrastructure, its logs and its pipeline, with nothing on
  // screen saying whose they were. The panel above guards its own `data` with
  // `data.project === project`; this is the same guard for the half that lives
  // in here.
  useEffect(() => {
    setSnap(null)
    setError('')
    setAt(null)
  }, [runId])

  const watching = Boolean(
    runId && active &&
    (state === 'VALIDATING' || (state === 'LIVE' && !snap))
  )

  useEffect(() => {
    if (!watching) return
    const every = CADENCE[target] || CADENCE.vercel
    const tick = () => { if (!document.hidden) refresh() }
    tick()
    const id = setInterval(tick, every)
    return () => clearInterval(id)
  }, [watching, target, refresh])

  return {
    snap: snap || frozen || null,
    live: Boolean(snap),
    busy, error, at,
    refresh,
    canRefresh: Boolean(runId),
  }
}


export function providerOf(snap) {
  if (snap?.aws && Object.keys(snap.aws).length) return 'aws_ec2'
  if (snap?.vercel && Object.keys(snap.vercel).length) return 'vercel'
  return ''
}


export { TERMINAL }

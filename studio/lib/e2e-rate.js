/**
 * How the end-to-end stage is reported to a person.
 *
 * A run is not a light switch. Five journeys with one failure is a very
 * different thing from five journeys with five, and "failing" said the same
 * word for both. This turns the stage into a rate: how many of the journeys
 * that were actually walked came out green.
 */

/** Group a stage's journeys into passed / failed / blocked / not run. */
export function journeyTally(e2e) {
  const rows = Array.isArray(e2e?.flows) ? e2e.flows : []
  const tally = { passed: 0, failed: 0, blocked: 0, skipped: 0, total: rows.length }
  for (const row of rows) {
    if (row?.blocked) tally.blocked += 1
    else if (row?.blocked_upstream) tally.skipped += 1
    else if (row?.ran === false) tally.skipped += 1
    else if ((row?.failed ?? 0) > 0) tally.failed += 1
    else tally.passed += 1
  }
  tally.walked = tally.passed + tally.failed
  tally.rate = tally.walked ? Math.round((tally.passed / tally.walked) * 100) : 0
  return tally
}

/** The one-line verdict, in words rather than a colour. */
export function journeySummary(e2e) {
  const t = journeyTally(e2e)
  if (!t.total) return { ...t, tone: 'muted', label: 'not run yet', detail: 'No journeys have been walked.' }
  if (!t.walked) {
    return {
      ...t, tone: 'muted', label: `0 of ${t.total} walked`,
      detail: t.blocked
        ? `${t.blocked} journey(s) could not start because the model was unavailable. Nothing here says the app is broken.`
        : 'None of the journeys ran.',
    }
  }
  const tone = t.failed === 0 ? 'ok' : t.rate >= 50 ? 'warn' : 'bad'
  const parts = [`${t.passed} of ${t.walked} passed`]
  if (t.blocked) parts.push(`${t.blocked} could not start`)
  if (t.skipped) parts.push(`${t.skipped} skipped`)
  return {
    ...t, tone,
    label: `${t.rate}%`,
    detail: parts.join(' · '),
  }
}

/** What one journey row should say. */
export function journeyStatus(row) {
  if (row?.blocked) return { tone: 'muted', label: 'could not start' }
  if (row?.blocked_upstream) return { tone: 'muted', label: 'waiting on another journey' }
  if (row?.ran === false) return { tone: 'muted', label: 'not run' }
  const failed = row?.failed ?? 0
  if (failed > 0) return { tone: 'bad', label: failed === 1 ? '1 step failed' : `${failed} steps failed` }
  return { tone: 'ok', label: 'passed' }
}

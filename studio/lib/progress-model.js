/**
 * One honest, forward-only progress number for a whole run.
 *
 * The backend does not report a single arc. Every sub-flow — a build, a
 * feature, an image, a repair — reports its own 0→100, so a run that has
 * reached 78% and then starts drawing a picture reports 20% next. The bar
 * used to follow that literally and appeared to go backwards.
 *
 * Here a lower number is read for what it is: a new piece of work starting,
 * not the run losing ground. Whatever has been earned is banked, and the new
 * sub-flow is given a share of the room that is left.
 */

// Nothing is claimed above this until the run really finishes.
export const CEILING = 97

// The first sub-flow of a run is the run; a later one is an extra job.
const FIRST_SHARE = 0.92
const LATER_SHARE = 0.55

// A report this far below the current sub-flow means a new one began.
const RESTART_DROP = 12

// While a step is slow, creep so it never reads as frozen.
const CREEP_PER_SEC = 0.3
const CREEP_LIMIT = 5

export function emptyProgress() {
  return { step: '', raw: 0, pct: 0, floor: 0, span: 0, base: null,
           at: 0, since: 0, started: false }
}

/** Fold one `{step, pct}` report in. The result never moves backwards. */
export function advance(prev, step, rawPct, now = Date.now()) {
  const state = prev && typeof prev === 'object' ? prev : emptyProgress()
  const raw = clamp(Number(rawPct) || 0, 0, 100)
  const label = typeof step === 'string' ? step : (state.step || '')

  // A fresh run announces itself with 0.
  if (raw === 0) return { ...emptyProgress(), step: label, at: now, since: now, started: true }

  // Whatever the creep has already shown someone is now owed to them. It was
  // being displayed but not kept, so the next real report started from the
  // smaller stored number and the bar visibly fell back.
  const shown = displayPct(state, now)
  const held = Math.max(state.pct, shown)

  // 100 closes this piece of work. Bank it and wait for whatever is next.
  if (raw >= 100) {
    const banked = clamp(Math.max(held, state.floor + state.span), 0, CEILING)
    return { ...state, step: label, raw, pct: round(banked), floor: round(banked),
             span: 0, base: null, at: now, since: now, started: true }
  }

  let { floor, span, base } = state
  const starting = base === null || raw + RESTART_DROP < base
  if (starting) {
    floor = held
    const room = Math.max(0, CEILING - floor)
    span = room * (state.started ? LATER_SHARE : FIRST_SHARE)
    base = raw
  }

  const target = floor + span * (raw / 100)
  const pct = clamp(Math.max(held, target), 0, CEILING)
  const moved = pct > held + 0.01
  return {
    ...state, step: label, raw, base, floor, span, pct: round(pct), at: now,
    started: true,
    since: moved || label !== state.step ? now : (state.since || now),
  }
}

/** What to paint now, creeping gently while a slow step runs. */
export function displayPct(state, now = Date.now()) {
  if (!state || !state.started) return 0
  const still = Math.max(0, (now - (state.since || state.at)) / 1000)
  return round(clamp(state.pct + Math.min(CREEP_LIMIT, still * CREEP_PER_SEC), 0, CEILING))
}

/** Seconds since the number last actually moved. */
export function stillFor(state, now = Date.now()) {
  if (!state || !state.started) return 0
  return Math.max(0, Math.floor((now - state.since) / 1000))
}

function clamp(n, lo, hi) { return Math.min(hi, Math.max(lo, n)) }
function round(n) { return Math.round(n * 10) / 10 }

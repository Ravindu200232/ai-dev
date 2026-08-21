/**
 * The progress bar must never go backwards, whatever the backend reports.
 * Every sub-flow the backend actually has is replayed here in sequence.
 */
import { advance, displayPct, emptyProgress, CEILING } from '../lib/progress-model.js'
import { journeySummary } from '../lib/e2e-rate.js'
import { WORK_STAGES, stageIndex, stagesFor } from '../lib/work-stages.js'

const FLOWS = [
  [15, 40, 65, 78, 88, 95, 100],   // feature
  [20, 45, 100],                   // image
  [12, 30, 50, 78, 100],           // pencil
  [10, 35, 80, 100],               // edit
  [20, 45, 65, 78, 100],           // repair
  [35, 75, 100],                   // page rewrite
]

let fail = 0
const t0 = 1700000000000

/**
 * Watch the bar the way a person does: it is repainted between reports, not
 * only when one arrives. The first version of this file only sampled on a
 * report and so never saw the creep being shown and then taken back.
 */
function watch(gapsMs) {
  let s = emptyProgress(), last = 0, t = t0, drops = 0
  for (let r = 0; r < 400; r++) {
    for (const pct of FLOWS[r % FLOWS.length]) {
      const gap = gapsMs[(r + pct) % gapsMs.length]
      // repaint four times while waiting, as the overlay's timer does
      for (let k = 1; k <= 4; k++) {
        const shown = displayPct(s, t + (gap * k) / 4)
        if (shown < last - 0.001) { console.error(`BACKWARD while waiting ${last} -> ${shown}`); drops++ }
        last = Math.max(last, shown)
      }
      t += gap
      s = advance(s, `step ${pct}`, pct, t)
      const shown = displayPct(s, t)
      if (shown < last - 0.001) { console.error(`BACKWARD on report ${last} -> ${shown}`); drops++ }
      if (shown > CEILING + 0.001) { console.error(`OVER CEILING ${shown}`); drops++ }
      last = shown
    }
  }
  return drops
}

// Fast reports, slow ones, and the long stalls a real build has.
fail += watch([700])
fail += watch([700, 4000, 20000, 60000])
fail += watch([45000, 90000, 150000])
const fresh = advance(advance(emptyProgress(), 'Writing…', 45, t0),
                      'Waiting for you', 0, t0 + 1000)
if (fresh.pct !== 0) { console.error('a new run did not reset'); fail++ }

const first = advance(emptyProgress(), 'Planning…', 15, t0)
if (displayPct(first, t0) < 8) { console.error('first report reads too low'); fail++ }
if (displayPct(first, t0 + 40000) <= displayPct(first, t0)) {
  console.error('a stalled step does not creep'); fail++
}

const cases = [
  [{ flows: [{ ran: true, failed: 0 }, { ran: true, failed: 0 }] }, '100%', 'ok'],
  [{ flows: [{ ran: true, failed: 0 }, { ran: true, failed: 2 }, { ran: true, failed: 0 },
             { ran: true, failed: 0 }, { ran: true, failed: 0 }] }, '80%', 'warn'],
  [{ flows: [{ ran: true, failed: 1 }] }, '0%', 'bad'],
  [{ flows: [{ blocked: 1 }, { blocked: 1 }] }, '0 of 2 walked', 'muted'],
]
for (const [input, label, tone] of cases) {
  const got = journeySummary(input)
  if (got.label !== label || got.tone !== tone) {
    console.error(`journeySummary: expected ${label}/${tone}, got ${got.label}/${got.tone}`)
    fail++
  }
}

// Every smaller job lights its lamps in order, from the arc its own
// backend flow really reports.
const ARCS = {
  feature: [15, 40, 65, 78, 88, 95],
  repair: [20, 45, 65, 78],
  // the second flow that also reports as a repair
  'repair:short': [10, 35, 80],
  select: [15, 40, 75],
  pencil: [12, 30, 50, 78],
  image: [20, 45, 70],
}
for (const [name, arc] of Object.entries(ARCS)) {
  const kind = name.split(':')[0]
  const stages = stagesFor(kind)
  if (!stages) { console.error(`${kind}: no stages`); fail++; continue }
  let seen = -1
  for (const pct of arc) {
    const i = stageIndex(kind, pct)
    if (i < seen) { console.error(`${kind}: stage went backwards at ${pct}%`); fail++ }
    if (i < 0 || i >= stages.length) { console.error(`${kind}: stage ${i} out of range`); fail++ }
    seen = i
  }
  if (stageIndex(kind, 0) !== 0) { console.error(`${kind}: does not start at its first stage`); fail++ }
  if (stageIndex(kind, 99) !== stages.length - 1) { console.error(`${kind}: does not end at its last stage`); fail++ }
  if (!arc.some(p => stageIndex(kind, p) === stages.length - 1)) {
    console.error(`${kind}: its last stage is never reached`); fail++
  }
}
if (stagesFor('build') !== null) { console.error('build must use the four-stage rail'); fail++ }

console.log(fail ? `Progress verification: ${fail} FAILURE(S)` :
  'Progress verification: forward-only across 3 timing profiles ' +
  '(reports and repaints), ' +
  `${Object.keys(WORK_STAGES).length} work rails in order, pass rates OK`)
process.exit(fail ? 1 : 0)

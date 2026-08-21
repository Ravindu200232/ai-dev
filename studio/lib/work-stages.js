/**
 * The stages each kind of work really has.
 *
 * A first build has four; a pencil edit has three and none of them is
 * "plan". Showing the build rail for both said the same thing about two jobs
 * that share nothing — but showing no rail at all for the smaller jobs made
 * them look like a different product. So each kind keeps the same shape and
 * the same lamps, with its own steps.
 *
 * The boundaries are the percentages the backend already reports for that
 * flow, so a stage lights up exactly when its work starts. `raw` is the
 * sub-flow's own number, not the smoothed one on the bar.
 */

/**
 * `upto` is the raw percentage at which this stage hands over to the next.
 * The last stage in a list has no bound; it runs to the end.
 */
export const WORK_STAGES = {
  // Planning 15 · Writing 40 · Verifying 65 · Watching 78 · Testing 88 · Final 95
  feature: [
    { id: 'plan', label: 'Plan', icon: 'brain', upto: 22 },
    { id: 'write', label: 'Write', icon: 'file', upto: 55 },
    { id: 'check', label: 'Check', icon: 'flask', upto: 84 },
    { id: 'live', label: 'Live', icon: 'sparkles' },
  ],
  // Two flows land here: reproduce/repair/check (20·45·65·78) and the
  // shorter read/apply/verify (10·35·80). Both walk these three in order.
  repair: [
    { id: 'reproduce', label: 'Reproduce', icon: 'bug', upto: 32 },
    { id: 'fix', label: 'Fix', icon: 'wrench', upto: 55 },
    { id: 'check', label: 'Check', icon: 'flask' },
  ],
  // Finding the code 15 · Editing 40 · Verifying 75
  select: [
    { id: 'find', label: 'Find', icon: 'pointer', upto: 26 },
    { id: 'change', label: 'Change', icon: 'file', upto: 60 },
    { id: 'check', label: 'Check', icon: 'flask' },
  ],
  // Finding 12 · Capturing the region 30 · Redesigning 50 · Verifying 78
  pencil: [
    { id: 'find', label: 'Find', icon: 'pointer', upto: 20 },
    { id: 'read', label: 'Read', icon: 'pencil', upto: 40 },
    { id: 'redesign', label: 'Redesign', icon: 'file', upto: 66 },
    { id: 'check', label: 'Check', icon: 'flask' },
  ],
  // Writing the image prompt 20 · Drawing 45 · Pointing the page at it 70
  image: [
    { id: 'describe', label: 'Describe', icon: 'brain', upto: 30 },
    { id: 'draw', label: 'Draw', icon: 'image', upto: 60 },
    { id: 'place', label: 'Place', icon: 'file' },
  ],
}

/** The stages for this kind of work, or `null` when it has its own rail. */
export function stagesFor(kind) {
  return WORK_STAGES[String(kind || '')] || null
}

/**
 * Which stage a raw sub-flow percentage is in.
 * Returns the index, or the last stage once the flow is finishing.
 */
export function stageIndex(kind, raw) {
  const stages = stagesFor(kind)
  if (!stages) return -1
  const pct = Math.max(0, Math.min(100, Number(raw) || 0))
  for (let i = 0; i < stages.length; i++) {
    const bound = stages[i].upto
    if (bound === undefined || pct < bound) return i
  }
  return stages.length - 1
}

/** The label of the stage currently running, for a heading. */
export function stageLabel(kind, raw) {
  const stages = stagesFor(kind)
  const i = stageIndex(kind, raw)
  return i >= 0 && stages[i] ? stages[i].label : ''
}

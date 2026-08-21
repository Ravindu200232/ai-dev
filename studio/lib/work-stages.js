/** The stages each kind of work really has. */

/** `upto` marks where a stage hands off to the next. */
export const WORK_STAGES = {
  // Planning, writing, verifying, watching, testing, then final checks.
  feature: [
    { id: 'plan', label: 'Plan', icon: 'brain', upto: 22 },
    { id: 'write', label: 'Write', icon: 'file', upto: 55 },
    { id: 'check', label: 'Check', icon: 'flask', upto: 84 },
    { id: 'live', label: 'Live', icon: 'sparkles' },
  ],
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

/** Return stages, or `null` for a custom progress rail. */
export function stagesFor(kind) {
  return WORK_STAGES[String(kind || '')] || null
}

/** Map raw sub-flow progress to a stage. */
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

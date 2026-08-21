/** The name a picture is known by inside a build. */

const MAX = 60

/** `Deluxe Suite 2.PNG` -> `deluxe-suite-2`. */
export function keyFromName(filename) {
  const stem = String(filename || '').replace(/\.[^.]+$/, '')
  const key = stem.toLowerCase().replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '').slice(0, MAX).replace(/-+$/, '')
  return key || 'picture'
}

/** Create a unique build-safe picture key. */
export function uniqueKey(want, taken) {
  const used = Array.isArray(taken) ? taken : []
  const base = keyFromName(want)
  if (!used.includes(base)) return base
  for (let n = 2; n < 500; n++) {
    const suffix = `-${n}`
    const tried = base.slice(0, MAX - suffix.length) + suffix
    if (!used.includes(tried)) return tried
  }
  return `${base}-${Date.now()}`.slice(0, MAX)
}

/** Map picture keys to server paths. */
export function uploadMap(pictures) {
  const out = {}
  for (const row of pictures || []) {
    if (row?.key && row?.file) out[row.key] = row.file
  }
  return out
}

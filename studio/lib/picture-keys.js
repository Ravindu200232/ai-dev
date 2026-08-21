/**
 * The name a picture is known by inside a build.
 *
 * A page asks for `/generated/<key>.png`, and the drawing pass skips any key
 * already on disk. So the key an upload is given decides whether it replaces
 * a generated picture or sits beside one nothing ever looks at. The backend
 * accepts `[A-Za-z0-9._-]{1,60}` and nothing else.
 */

const MAX = 60

/** `Deluxe Suite 2.PNG` -> `deluxe-suite-2`. */
export function keyFromName(filename) {
  const stem = String(filename || '').replace(/\.[^.]+$/, '')
  const key = stem.toLowerCase().replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '').slice(0, MAX).replace(/-+$/, '')
  return key || 'picture'
}

/** A key the build will accept, and that no other picture already has. */
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

/** `{key: server path}` — the shape the build already uses for the logo. */
export function uploadMap(pictures) {
  const out = {}
  for (const row of pictures || []) {
    if (row?.key && row?.file) out[row.key] = row.file
  }
  return out
}

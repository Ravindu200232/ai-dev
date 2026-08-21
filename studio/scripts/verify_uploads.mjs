/**
 * Uploading your own pictures must be opt-in and must not collide.
 * The build treats a key that is already on disk as done, so a wrong key
 * here silently means "drawn anyway" — the naming has to be exact.
 */
import { keyFromName, uniqueKey, uploadMap } from '../lib/picture-keys.js'

let fail = 0
const is = (got, want, what) => {
  if (got !== want) { console.error(`${what}: expected ${want}, got ${got}`); fail++ }
}

is(keyFromName('Deluxe Suite 2.PNG'), 'deluxe-suite-2', 'spaces and case')
is(keyFromName('hero.jpg'), 'hero', 'plain name')
is(keyFromName('room_deluxe.webp'), 'room-deluxe', 'underscores')
is(keyFromName('  ..png'), 'picture', 'nothing usable')
is(keyFromName('a'.repeat(90) + '.png').length, 60, 'long name is cut')

is(uniqueKey('hero.png', []), 'hero', 'first of its name')
is(uniqueKey('hero.png', ['hero']), 'hero-2', 'second of its name')
is(uniqueKey('hero.png', ['hero', 'hero-2']), 'hero-3', 'third of its name')

// Every key must be something the backend will accept.
const OK = /^[A-Za-z0-9._-]{1,60}$/
for (const name of ['Deluxe Suite 2.PNG', 'a/b\\c.png', 'héro.png', '!!!.png',
                    'room deluxe (1).jpeg']) {
  const key = uniqueKey(name, [])
  if (!OK.test(key)) { console.error(`${name} -> ${key} is not a usable key`); fail++ }
}

// An empty list must change nothing about the build.
const empty = uploadMap([])
if (Object.keys(empty).length) { console.error('an empty list sent something'); fail++ }
if (Object.keys(uploadMap(null)).length) { console.error('null sent something'); fail++ }
const mapped = uploadMap([{ key: 'hero', file: '/tmp/hero.png' },
                          { key: 'skipped', file: '' }])
if (mapped.hero !== '/tmp/hero.png') { console.error('a real picture was dropped'); fail++ }
if ('skipped' in mapped) { console.error('a picture with no file was sent'); fail++ }

console.log(fail ? `Upload naming: ${fail} FAILURE(S)`
  : 'Upload naming: keys are safe, unique and stable')
process.exit(fail ? 1 : 0)

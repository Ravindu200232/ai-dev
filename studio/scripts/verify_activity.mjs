import fs from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..')
const sourcePath = path.join(root, 'lib', 'activity.js')
const source = fs.readFileSync(sourcePath, 'utf8')
const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
const mod = await import(moduleUrl)

const cases = [
  ['📝 app/api/rooms/route.js (1007B)', 'Rooms service'],
  ['📝 app/admin/rooms/page.jsx (1.2KB)', 'Rooms page'],
  ['🧪 tests/unit/components/Navbar.test.jsx (2.8KB)', 'Navbar checks'],
]

for (const [raw, expected] of cases) {
  const event = mod.activityEvent(raw, 'INFO')
  if (!event || !event.title.includes(expected)) {
    throw new Error(`friendly activity mismatch for ${raw}: ${JSON.stringify(event)}`)
  }
  if (/\bapp\//i.test(event.title) || /route\.js/i.test(event.title) || /tests\//i.test(event.title)) {
    throw new Error(`technical path leaked into friendly title: ${event.title}`)
  }
}

const direct = mod.humanActivity('📝 components/RoomCard.jsx (1.4KB)')
if (!direct.includes('Room Card')) throw new Error(`component name was not humanized: ${direct}`)

console.log('Friendly activity verification: OK')

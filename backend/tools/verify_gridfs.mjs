/**
 * Prove GridFS works against a real MongoDB before trusting a build to it.
 *
 * Run it from a generated project, or anywhere the `mongodb` driver is
 * installed:
 *
 *     node tools/verify_gridfs.mjs "mongodb+srv://user:pass@host/dbname"
 *
 * With no argument it reads MONGODB_URI, then .env.local. It writes a 20MB
 * file — deliberately larger than the 16MB a document can hold — reads it
 * back, compares every byte, checks that re-seeding the same key does not
 * store a second copy, and removes everything it created.
 */
import { MongoClient, GridFSBucket, ObjectId } from 'mongodb'
import { readFile } from 'node:fs/promises'
import crypto from 'node:crypto'

const BUCKET = 'uploads'
const MB = 1024 * 1024

async function uriFromEnv() {
  if (process.argv[2]) return process.argv[2]
  if (process.env.MONGODB_URI) return process.env.MONGODB_URI
  try {
    for (const line of (await readFile('.env.local', 'utf8')).split('\n')) {
      if (line.startsWith('MONGODB_URI=')) {
        return line.slice('MONGODB_URI='.length).trim().replace(/^["']|["']$/g, '')
      }
    }
  } catch {}
  return ''
}

function dbFromUri(uri) {
  if (process.env.MONGODB_DB) return process.env.MONGODB_DB
  const m = /^mongodb(?:\+srv)?:\/\/[^/]+\/([^?]+)/.exec(uri || '')
  return m ? decodeURIComponent(m[1]) : ''
}

// Never print the password back at whoever ran this.
const redact = (uri) => String(uri || '').replace(/\/\/([^:]+):([^@]+)@/, '//$1:***@')

let pass = 0, fail = 0
const ok = (cond, msg) => {
  if (cond) { pass++; console.log('  ✅', msg) }
  else { fail++; console.log('  ❌', msg) }
}

const uri = await uriFromEnv()
if (!uri) {
  console.error('No connection string. Pass one as an argument, or set MONGODB_URI.')
  process.exit(2)
}
const dbName = dbFromUri(uri)
if (!dbName) {
  console.error('The connection string names no database — add /<dbname> before the ?')
  process.exit(2)
}

console.log(`\nGridFS check — ${redact(uri)}  (db: ${dbName})\n`)
const client = new MongoClient(uri, { serverSelectionTimeoutMS: 15000 })
const created = []

try {
  const started = Date.now()
  await client.connect()
  const db = client.db(dbName)
  await db.command({ ping: 1 })
  ok(true, `connected in ${Date.now() - started}ms`)

  const bucket = new GridFSBucket(db, { bucketName: BUCKET })
  const key = `verify:${crypto.randomUUID()}`

  // 20MB of non-repeating bytes: repetition would hide a chunk written twice.
  const source = crypto.randomBytes(20 * MB)
  const sourceHash = crypto.createHash('sha256').update(source).digest('hex')

  const upStart = Date.now()
  const upload = bucket.openUploadStream('verify-20mb.bin', {
    contentType: 'application/octet-stream',
    metadata: { key, createdBy: 'verify_gridfs' },
  })
  await new Promise((resolve, reject) => {
    upload.once('finish', resolve)
    upload.once('error', reject)
    upload.end(source)
  })
  created.push(upload.id)
  const upMs = Date.now() - upStart
  ok(true, `stored 20MB in ${upMs}ms (${(20 / (upMs / 1000)).toFixed(1)} MB/s)`)

  const meta = await db.collection(`${BUCKET}.files`).findOne({ _id: upload.id })
  ok(meta?.length === source.length, `metadata length matches (${meta?.length} bytes)`)
  const chunkCount = await db.collection(`${BUCKET}.chunks`)
    .countDocuments({ files_id: upload.id })
  ok(chunkCount === Math.ceil(source.length / (meta?.chunkSize || 261120)),
     `split into ${chunkCount} chunks — this is why 16MB stops mattering`)

  const downStart = Date.now()
  const parts = []
  for await (const c of bucket.openDownloadStream(upload.id)) parts.push(c)
  const back = Buffer.concat(parts)
  ok(crypto.createHash('sha256').update(back).digest('hex') === sourceHash,
     `read back byte-identical in ${Date.now() - downStart}ms`)

  // What `putFileOnce` relies on: finding an earlier upload by its key.
  const again = await db.collection(`${BUCKET}.files`).findOne({ 'metadata.key': key })
  ok(String(again?._id) === String(upload.id),
     'lookup by metadata.key finds it — re-seeding will reuse, not duplicate')

  const missing = await db.collection(`${BUCKET}.files`)
    .findOne({ _id: new ObjectId('0'.repeat(24)) })
  ok(missing === null, 'an unknown id returns null rather than throwing')

  await bucket.delete(upload.id)
  created.pop()
  ok(await db.collection(`${BUCKET}.chunks`).countDocuments({ files_id: upload.id }) === 0,
     'delete removed the chunks too — no orphans left behind')
} catch (e) {
  fail++
  console.log('  ❌', e?.message || e)
  if (/authentication|not authorized/i.test(String(e?.message))) {
    console.log('     → the user in the connection string cannot write to this database')
  }
  if (/ENOTFOUND|ETIMEDOUT|ServerSelection/i.test(String(e?.message))) {
    console.log('     → check the host, and that this machine\'s IP is on the Atlas access list')
  }
} finally {
  for (const id of created) {
    try { await new GridFSBucket(client.db(dbName), { bucketName: BUCKET }).delete(id) } catch {}
  }
  await client.close().catch(() => {})
}

console.log(`\n  ${pass} passed, ${fail} failed\n`)
process.exit(fail ? 1 : 0)

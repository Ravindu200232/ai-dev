let clientPromise

// Reuse across HMR reloads so dev doesn't leak a connection per edit.
if (process.env.NODE_ENV === 'development' && global._mongoClientPromise) {
  clientPromise = global._mongoClientPromise
}

// Connect on first use, never during import.
// Next.js imports routes while building, when no database may exist.
function connection() {
  if (!clientPromise) {
    if (!uri) throw new Error('MONGODB_URI is not set')
    clientPromise = new MongoClient(uri).connect()
    if (process.env.NODE_ENV === 'development') {
      global._mongoClientPromise = clientPromise
    }
  }
  return clientPromise
}

/** Awaitable exactly like the promise it replaced. */
export default { then: (ok, no) => connection().then(ok, no) }

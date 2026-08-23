import { toNextJsHandler } from 'better-auth/next-js'

// Build auth lazily so compilation never opens MongoDB.
let handlers
async function ready() {
  if (!handlers) {
    const { auth } = await import('@/lib/auth')
    handlers = toNextJsHandler(auth.handler)
  }
  return handlers
}

export const dynamic = 'force-dynamic'

export async function GET(request) {
  return (await ready()).GET(request)
}

export async function POST(request) {
  return (await ready()).POST(request)
}

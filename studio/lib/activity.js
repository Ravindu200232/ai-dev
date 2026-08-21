function words(value = '') {
  return String(value)
    .replace(/\.[^.\/]+$/, '')
    .replace(/\[([^\]]+)\]/g, '$1')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[-_]+/g, ' ')
    .replace(/\b(id|slug)\b/gi, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function title(value = '') {
  const out = words(value)
  if (!out) return 'app'
  return out.replace(/\b\w/g, c => c.toUpperCase())
}

export function friendlyTarget(path = '') {
  const clean = String(path).replace(/\\/g, '/').replace(/^\.\//, '')
  const parts = clean.split('/').filter(Boolean)
  if (!parts.length) return 'the app'

  const test = parts[0] === 'tests'
  const api = parts[0] === 'app' && parts[1] === 'api'
  const page = parts[0] === 'app' && /^page\.[jt]sx?$/.test(parts.at(-1) || '')
  const component = parts[0] === 'components'

  if (test) {
    const base = parts.at(-1)?.replace(/\.test\.[jt]sx?$/, '') || 'feature'
    return `${title(base)} checks`
  }
  if (api) {
    const route = parts.slice(2, -1).filter(x => !/^\[/.test(x))
    const name = route.at(-1) || 'app'
    return `${title(name)} service`
  }
  if (page) {
    const route = parts.slice(1, -1).filter(x => !/^\[/.test(x))
    const name = route.at(-1) || 'home'
    return `${title(name)} page`
  }
  if (component) return title(parts.at(-1) || 'component')
  if (parts[0] === 'lib') return `${title(parts.at(-1) || 'app')} logic`
  return title(parts.at(-1) || parts.at(-2) || 'app')
}

function pathFrom(line = '') {
  const match = String(line).match(/(?:^|\s)(?:(?:app|components|lib|tests|qa|public)\/[A-Za-z0-9_@.\-\[\]\/]+\.(?:jsx?|tsx?|json|css|md))/i)
  return match?.[0]?.trim() || ''
}

export function activityEvent(text = '', level = 'INFO') {
  const line = String(text).replace(/\s+/g, ' ').trim()
  const low = line.toLowerCase()
  const path = pathFrom(line)
  const target = path ? friendlyTarget(path) : ''

  if (!line) return null
  if (/^\[?next\]?/i.test(line) || low.includes('http request:') || low.includes('webpack')) return null
  if (low.includes('destination stream closed early')) return null
  if (low.includes('adopting the mongodb') || low.includes('connected to mongodb')) return null

  if (/\b\d+\/\d+ files on disk\b/i.test(line)) {
    const m = line.match(/(\d+)\/(\d+) files on disk/i)
    return { kind: 'build', title: `${m?.[1] || 'More'} of ${m?.[2] || ''} build items are ready`, detail: 'The app is being assembled in the background.' }
  }
  if (low.includes('npm install') || low.includes('installing dependenc') || low.includes('preparing project dependenc')) {
    return { kind: 'setup', title: 'Preparing the app tools', detail: 'Dependencies are being made ready once for this project.' }
  }
  if (low.includes('npm error') || /^exit \d+/.test(low)) {
    return { kind: 'warn', title: 'A setup step needs another try', detail: 'AgentForge kept the build moving and will verify it again before finishing.' }
  }
  if (low.includes('build clean')) {
    return { kind: 'done', title: 'The app builds cleanly', detail: 'The latest code compiled successfully.' }
  }
  if (low.includes('npm run build')) {
    return { kind: 'verify', title: 'Checking the app build', detail: 'Making sure the latest changes compile before moving on.' }
  }
  if (low.includes('playwright') || low.includes('e2e') || low.includes('journey')) {
    return { kind: 'test', title: 'Trying the app like a real user', detail: 'The browser is walking through the current user flow.' }
  }
  if (low.includes('vitest') || low.includes('unit test')) {
    return { kind: 'test', title: target ? `Checking ${target}` : 'Running focused checks', detail: 'Only the checks related to the current work run first.' }
  }
  if (low.includes('authored') || low.includes('writing tests') || (path && path.startsWith('tests/'))) {
    return { kind: 'test', title: target ? `Prepared ${target}` : 'Prepared a focused check', detail: 'A small test was added for the latest change.' }
  }
  if (low.includes('repair') || low.includes('root cause') || low.includes('fixing') || low.includes('repaired')) {
    return { kind: 'fix', title: target ? `Fixed ${target}` : 'Fixing a problem that was found', detail: 'The repair is limited to the affected part of the app.' }
  }
  if (low.includes('semantic proof') || low.includes('semantic audit')) {
    return { kind: 'verify', title: 'Checking the requested behavior', detail: 'Confirming the change matches what you asked for.' }
  }
  if (low.includes('analy') || low.includes('planning') || low.includes('writing plan')) {
    return { kind: 'plan', title: 'Planning the safest change', detail: 'Tracing what needs to change before touching the code.' }
  }
  if (path && (low.includes('📝') || low.includes('wrote') || low.includes('written') || low.includes('file'))) {
    return { kind: 'build', title: `Updated ${target}`, detail: 'This part of the app is now ready for verification.' }
  }
  if (low.includes('security')) {
    return { kind: 'test', title: 'Checking security-sensitive behavior', detail: 'Looking for unsafe access or dependency issues.' }
  }
  if (low.includes('api verification')) {
    return { kind: 'test', title: 'Checking the services used by the app', detail: 'Only the relevant API behavior is being verified.' }
  }
  if (low.includes('ready') || low.includes('serving')) {
    return { kind: 'done', title: 'Preparing the live preview', detail: 'The finished app is being refreshed for you.' }
  }

  if (level === 'ERROR' || level === 'WARN') {
    return { kind: 'warn', title: 'AgentForge found something to review', detail: 'The technical detail is available in Terminal if you need it.' }
  }
  return null
}

export function humanActivity(text = '', level = 'INFO') {
  return activityEvent(text, level)?.title || ''
}

export function activityAgent(text = '') {
  const low = String(text).toLowerCase()
  if (low.includes('test') || low.includes('vitest') || low.includes('e2e') || low.includes('playwright')) return 'qa'
  if (low.includes('repair') || low.includes('fix') || low.includes('root cause')) return 'fixer'
  if (low.includes('analy') || low.includes('plan')) return 'analyst'
  if (pathFrom(text) || low.includes('wrote') || low.includes('build')) return 'builder'
  return 'orchestrator'
}

export function liveFileActivity(path = '') {
  return {
    kind: path.startsWith('tests/') ? 'test' : 'build',
    title: path.startsWith('tests/') ? `Preparing ${friendlyTarget(path)}` : `Building ${friendlyTarget(path)}`,
    detail: path.startsWith('tests/')
      ? 'A focused check is being prepared while the app continues building.'
      : 'This part is being written now. The preview will refresh when it is safe to show.',
  }
}

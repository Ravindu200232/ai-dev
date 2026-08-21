const SKIP_DIRS = [
  'node_modules/', 'dist/', 'build/', 'out/', '.git/', '.next/', '.turbo/',
  'coverage/', '.vercel/', '.cache/',
]

const TEXT_RE = /\.(js|jsx|ts|tsx|mjs|cjs|css|scss|html|json|jsonl|md|txt|yml|yaml|env|toml|svg|gitignore|lock|sql|sh)$/i

const ALWAYS = new Set([
  'package.json', 'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock',
  'tsconfig.json', 'jsconfig.json', 'index.html',
  'vite.config.js', 'vite.config.ts', 'next.config.js', 'next.config.mjs',
])

function sanitizeProjectName(name) {
  return String(name || 'imported-project')
    .toLowerCase()
    .replace(/[^a-z0-9-_]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 48) || 'imported-project'
}

const readText = (file) => new Promise((resolve, reject) => {
  const r = new FileReader()
  r.onload = () => resolve(String(r.result || ''))
  r.onerror = () => reject(new Error(`could not read ${file.name}`))
  r.readAsText(file)
})

export async function readFolder(fileList, onProgress) {
  const all = Array.from(fileList || [])
  if (!all.length) throw new Error('nothing was selected')

  const firstPath = all[0].webkitRelativePath || all[0].name
  const root = firstPath.split('/')[0] || 'imported-project'

  const wanted = []
  let skipped = 0
  for (const f of all) {
    let rel = f.webkitRelativePath || f.name
    if (rel.startsWith(root + '/')) rel = rel.slice(root.length + 1)
    if (!rel || rel.endsWith('/')) continue
    if (SKIP_DIRS.some(d => rel.startsWith(d)) || rel.endsWith('.DS_Store')) {
      skipped++
      continue
    }
    const base = rel.split('/').pop()
    if (!TEXT_RE.test(rel) && !ALWAYS.has(base)) { skipped++; continue }
    wanted.push({ rel, file: f })
  }

  const files = {}
  for (let i = 0; i < wanted.length; i++) {
    files[wanted[i].rel] = await readText(wanted[i].file)
    onProgress?.(i + 1, wanted.length)
  }
  if (!Object.keys(files).length) {
    throw new Error('no importable source files found — was that the project '
                  + 'root, or a build output folder?')
  }
  return { name: sanitizeProjectName(root), title: root, files, skipped }
}

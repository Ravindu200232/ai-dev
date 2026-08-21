'use client'

import { useMemo, useState } from 'react'
import { ChevronRight, ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'


/** The project as a tree, the way an editor shows it. */


const KIND = {
  jsx:  ['#0ea5c6', 'JSX'],
  js:   ['#a68a00', 'JS'],
  mjs:  ['#a68a00', 'JS'],
  cjs:  ['#a68a00', 'JS'],
  ts:   ['#3178c6', 'TS'],
  tsx:  ['#3178c6', 'TSX'],
  json: ['#8a8a20', '{ }'],
  css:  ['#2a7fd4', 'CSS'],
  scss: ['#c6538c', 'CSS'],
  html: ['#e34c26', '<>'],
  md:   ['#6b7280', 'MD'],
  py:   ['#4b8bbe', 'PY'],
  env:  ['#b08414', 'ENV'],
  png:  ['#8f5fc4', 'IMG'],
  jpg:  ['#8f5fc4', 'IMG'],
  jpeg: ['#8f5fc4', 'IMG'],
  svg:  ['#d18a1b', 'SVG'],
  webp: ['#8f5fc4', 'IMG'],
  ico:  ['#8f5fc4', 'IMG'],
  lock: ['#7a8494', 'LCK'],
  txt:  ['#7a8494', 'TXT'],
}

const BY_NAME = {
  'package.json': ['#5f9e2f', 'NPM'],
  'package-lock.json': ['#7a8494', 'LCK'],
  'next.config.mjs': ['#5d5d5d', 'NXT'],
  'next.config.js': ['#5d5d5d', 'NXT'],
  'tailwind.config.js': ['#0d9dbf', 'TW'],
  'postcss.config.js': ['#dd3a0a', 'PC'],
  'jsconfig.json': ['#a68a00', 'CFG'],
  'vitest.config.mjs': ['#729b1b', 'TEST'],
  'playwright.config.js': ['#2ead33', 'TEST'],
  '.env.local': ['#b08414', 'ENV'],
  '.gitignore': ['#f14e32', 'GIT'],
  'plan.md': ['#ec3013', 'PLAN'],
}


export function fileBadge(name) {
  const known = BY_NAME[name]
  if (known) return known
  if (/\.test\.[jt]sx?$/.test(name)) return ['#729b1b', 'TEST']
  const ext = name.includes('.') ? name.split('.').pop().toLowerCase() : ''
  return KIND[ext] || ['#7a8494', ext.slice(0, 4).toUpperCase() || '•']
}


/** Turn flat paths into a folder-first tree. */
function build(paths) {
  const root = { name: '', path: '', dirs: new Map(), files: [] }
  for (const p of paths) {
    const parts = p.split('/')
    let node = root
    for (let i = 0; i < parts.length - 1; i++) {
      const seg = parts[i]
      if (!node.dirs.has(seg)) {
        node.dirs.set(seg, {
          name: seg, path: parts.slice(0, i + 1).join('/'),
          dirs: new Map(), files: [],
        })
      }
      node = node.dirs.get(seg)
    }
    node.files.push({ name: parts[parts.length - 1], path: p })
  }

  const shape = (node) => ({
    ...node,
    dirs: [...node.dirs.values()]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map(shape),
    files: node.files.sort((a, b) => a.name.localeCompare(b.name)),
  })
  return shape(root)
}


/** Which folders start open. */
function initialOpen(tree, total) {
  const open = new Set()
  const walk = (node, depth) => {
    for (const d of node.dirs) {
      if (total <= 40 || depth === 0) open.add(d.path)
      walk(d, depth + 1)
    }
  }
  walk(tree, 0)
  return open
}


export default function FileTree({ files, active, dirty, onPick }) {
  const paths = useMemo(() => Object.keys(files || {}), [files])
  const tree = useMemo(() => build(paths), [paths])
  const [open, setOpen] = useState(() => new Set())
  const [seeded, setSeeded] = useState('')

  // Re-seeded when the project changes, not on every render.
  const stamp = paths.length + ':' + (paths[0] || '')
  if (stamp !== seeded) {
    setSeeded(stamp)
    setOpen(initialOpen(tree, paths.length))
  }

  const toggle = (path) => setOpen(prev => {
    const next = new Set(prev)
    if (!next.delete(path)) next.add(path)
    return next
  })

  if (!paths.length) {
    return (
      <p className="px-3 py-2 font-mono text-[10px] text-muted2">No files yet.</p>
    )
  }

  const rows = []
  const walk = (node, depth) => {
    for (const dir of node.dirs) {
      const isOpen = open.has(dir.path)
      rows.push(
        <button key={'d:' + dir.path} onClick={() => toggle(dir.path)}
                className="flex w-full items-center gap-[7px] border-b border-line
                           border-l-[3px] border-l-transparent py-[5px] pr-3
                           text-left font-mono text-[11px] text-muted
                           transition-colors hover:bg-ink/[.07] hover:text-ink"
                style={{ paddingLeft: 9 + depth * 14 }}>
          {isOpen ? <ChevronDown className="size-3 shrink-0 text-label" />
                  : <ChevronRight className="size-3 shrink-0 text-label" />}
          <span className="truncate">{dir.name}</span>
        </button>
      )
      if (isOpen) walk(dir, depth + 1)
    }
    for (const f of node.files) {
      const [colour, label] = fileBadge(f.name)
      const on = f.path === active
      rows.push(
        <button key={'f:' + f.path} onClick={() => onPick(f.path)}
                title={f.path}
                className={cn('flex w-full items-center gap-[7px] border-b',
                  'border-line border-l-[3px] py-[5px] pr-3 text-left',
                  'font-mono text-[11px] transition-colors',
                  on ? 'border-l-accent bg-panel2 text-ink'
                     : 'border-l-transparent text-ink hover:bg-ink/[.07]')}
                style={{ paddingLeft: 9 + depth * 14 }}>
          <span className="shrink-0 px-[3px] py-px text-[7.5px] font-bold leading-[10px]"
                style={{ color: colour, border: `1px solid ${colour}55`,
                         background: `${colour}18` }}>
            {label}
          </span>
          <span className="truncate">{f.name}</span>
          {dirty?.has(f.path) && (
            <span title="unsaved" className="ml-auto size-[5px] shrink-0 bg-accent" />
          )}
        </button>
      )
    }
  }
  walk(tree, 0)

  return <div>{rows}</div>
}

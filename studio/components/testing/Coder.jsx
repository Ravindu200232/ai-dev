'use client'

import { useState } from 'react'
import { Empty, Tag } from '../ui'
import { cn } from '@/lib/utils'


export default function Coder({ qa }) {
  const tests = qa?.tests || {}
  const names = Object.keys(tests).sort()
  const [sel, setSel] = useState(null)

  if (!names.length) {
    return <Empty>No test files on disk for this project.</Empty>
  }
  const current = sel && tests[sel] ? sel : names[0]

  return (
    <div className="flex h-full min-h-[420px] border border-line2">
      <aside className="w-[240px] shrink-0 overflow-y-auto border-r-2 border-line2">
        {names.map(n => (
          <button key={n} onClick={() => setSel(n)}
                  className={cn('block w-full border-b border-line border-l-[3px]',
                    'px-3 py-2 text-left transition-colors',
                    n === current ? 'border-l-accent bg-panel2'
                                  : 'border-l-transparent hover:bg-ink/[.07]')}>
            <span className="block truncate font-mono text-[11px] text-ink">
              {n.split('/').pop()}
            </span>
            <span className="block truncate font-mono text-[10px] text-muted2">
              {n.replace(/\/[^/]+$/, '')}
            </span>
            {n.includes('/quarantine/') && (
              <Tag tone="bad" className="mt-1">set aside</Tag>
            )}
          </button>
        ))}
      </aside>

      <pre className="min-w-0 flex-1 overflow-auto bg-code p-4 font-mono
                      text-[12px] leading-[1.75] text-ink">
        {tests[current]}
      </pre>
    </div>
  )
}

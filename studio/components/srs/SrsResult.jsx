'use client'

import { useEffect, useState } from 'react'
import { FileDown, RefreshCw } from 'lucide-react'
import { api } from '@/lib/api'
import { useStore } from '@/lib/store'
import { Badge, Button, Empty, SubTab, SubTabs } from '../ui'
import { VIEWS, badgeFor } from './views'


export default function SrsResult() {
  const project = useStore(s => s.project)
  const [srs, setSrs] = useState(null)
  const [sub, setSub] = useState('document')
  const [state, setState] = useState('idle')
  const [error, setError] = useState('')

  async function load() {
    if (!project) return
    setState('loading')
    let last
    for (let i = 0; i < 4; i++) {
      try {
        setSrs(await api.srsResults(project))
        setState('ready')
        return
      } catch (e) {
        last = e
        await new Promise(r => setTimeout(r, 700))
      }
    }
    setError(last?.message || 'unknown error')
    setState('error')
  }

  useEffect(() => { load()  }, [project])

  if (!project) return <Empty>Open a project to see the SRS it was built from.</Empty>

  const have = srs?.have || {}
  const anything = Object.values(have).some(Boolean)
  const View = (VIEWS.find(v => v.id === sub) || VIEWS[0]).C

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-[radial-gradient(circle_at_top_right,rgba(93,106,251,.07),transparent_30%)]">
      <SubTabs>
        {VIEWS.map(v => (
          <SubTab key={v.id} on={sub === v.id} onClick={() => setSub(v.id)}>
            {v.label}
            {badgeFor(v.id, srs) != null && (
              <Badge tone={badgeFor(v.id, srs).bad ? 'bad' : 'mute'}>
                {badgeFor(v.id, srs).n}
              </Badge>
            )}
          </SubTab>
        ))}
        <span className="flex-1" />
        <span className="flex shrink-0 items-center gap-2 px-3">
          {have.pdf && (
            <a href={api.srsPdfUrl(project)} target="_blank" rel="noreferrer"
               className="inline-flex h-[30px] items-center gap-1.5 border
                          border-line2 px-3 font-display text-[12px] font-extrabold
                          text-ink transition-colors hover:bg-ink/[.07]">
              <FileDown className="size-3" /> PDF
            </a>
          )}
          <Button variant="outline" onClick={load}>
            <RefreshCw className="size-3" /> Refresh
          </Button>
        </span>
      </SubTabs>

      <div className="mx-auto min-h-0 w-full max-w-[1180px] flex-1 overflow-y-auto p-5">
        {state === 'loading' && <Empty>Reading the SRS…</Empty>}
        {state === 'error' && <Empty bad>Could not read it — {error}</Empty>}
        {state === 'ready' && !anything && (
          <Empty>
            This project has no SRS. It was built straight from a prompt —
            start one with “Plan it first” on the home screen.
          </Empty>
        )}
        {state === 'ready' && anything && <View srs={srs} />}
      </div>
    </div>
  )
}

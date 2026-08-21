'use client'

import { ExternalLink, GitBranch, Rocket } from 'lucide-react'
import { STATE_TEXT } from '@/lib/deploy-constants'
import { SectionLabel, Tag } from '../ui'

export default function DeployResult({ data }) {
  const last = data?.have?.last ? data.last : null
  if (!last) return null

  const [label, tone] = STATE_TEXT[last.state] || [last.state, 'mute']
  const repo = last.repo_state || {}

  const url = repo.application_url || ''
  const slug = typeof repo.repository === 'string' ? repo.repository : ''
  const score = last.readiness?.score

  return (
    <div className="border border-line2 p-4">
      <SectionLabel className="border-b-2 border-line2 pb-1.5"
                    right={<Tag tone={{ pass: 'ok', fail: 'bad',
                                        run: 'accent' }[tone] || 'mute'}>
                      {label}
                    </Tag>}>
        Last deployment
      </SectionLabel>

      <dl className="mt-1 text-[11.5px]">
        <Row label="Target">
          {last.target === 'vercel' ? 'Vercel' : 'AWS EC2'}
        </Row>
        {url && (
          <Row label="Live at">
            <a href={url} target="_blank" rel="noreferrer"
               className="inline-flex items-center gap-1 text-accent hover:underline">
              <Rocket className="size-3" /> {url}
              <ExternalLink className="size-2.5" />
            </a>
          </Row>
        )}
        {slug && (
          <Row label="Repository">
            <a href={`https://github.com/${slug}`} target="_blank" rel="noreferrer"
               className="inline-flex items-center gap-1 text-accent hover:underline">
              <GitBranch className="size-3" /> {slug}
              {repo.branch ? ` · ${repo.branch}` : ''}
              <ExternalLink className="size-2.5" />
            </a>
          </Row>
        )}
        {typeof score === 'number' && (
          <Row label="Readiness">{score}/100</Row>
        )}
        {last.link?.adopted_at && (
          <Row label="Recorded">
            {new Date(last.link.adopted_at).toLocaleString()}
          </Row>
        )}
        <Row label="Run">
          <span className="font-mono text-[10.5px] text-muted2">
            {String(last.run_id).slice(0, 12)}
          </span>
        </Row>
      </dl>

      {last.error && (
        <p className="mt-3 border border-line2 border-l-[3px] border-l-accent
                      bg-tint px-3 py-2.5 text-[11.5px] text-deep">
          {last.error}
        </p>
      )}
    </div>
  )
}

const Row = ({ label, children }) => (
  <div className="flex gap-3 border-b border-line py-1.5 last:border-0">
    <dt className="w-[86px] shrink-0 text-label">{label}</dt>
    <dd className="min-w-0 flex-1 break-words text-ink">{children}</dd>
  </div>
)

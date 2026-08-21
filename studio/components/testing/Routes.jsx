'use client'

import { Badge, Empty, Table, Tag, TR, TH, TD } from '../ui'

/**
 * Every API handler that has a test, and any route the probe found broken.
 *
 * Built from the manifest and the runtime record rather than guessed from the
 * file tree: a route that exists on disk and 500s is the interesting case, and
 * the file tree cannot tell you that.
 */
export default function Routes({ qa }) {
  const runtime = qa?.report?.runtime || []
  const manifest = qa?.manifest || {}

  const tested = [...new Set(Object.values(manifest)
    .map(m => m?.target || '')
    .filter(t => t.startsWith('app/api/')))].sort()

  // A runtime error line names its route; pair them up so a broken one shows.
  const broken = {}
  for (const line of runtime) {
    const m = /Route (\/\S*)/.exec(String(line))
    if (m) broken[m[1]] = String(line).split('\n')[0]
  }

  if (!tested.length && !Object.keys(broken).length) {
    return <Empty>No route record for this project.</Empty>
  }

  return (
    <div>
      <p className="mb-3 text-[11px] text-muted">
        API handlers that have a test, and any route the browser probe found
        broken.
      </p>
      <Table>
        <thead>
          <TR><TH>route</TH><TH>handler</TH><TH>status</TH></TR>
        </thead>
        <tbody>
          {tested.map(t => (
            <TR key={t}>
              <TD>
                <code className="font-mono text-ink">
                  /{t.replace(/^app\//, '').replace(/\/route\.jsx?$/, '')}
                </code>
              </TD>
              <TD className="font-mono text-muted">{t}</TD>
              <TD><Tag tone="ok">tested</Tag></TD>
            </TR>
          ))}
          {Object.entries(broken).map(([route, why]) => (
            <TR key={route} className="bg-tint">
              <TD><code className="font-mono text-deep">{route}</code></TD>
              <TD className="text-muted2">—</TD>
              <TD><Badge tone="bad">{why}</Badge></TD>
            </TR>
          ))}
        </tbody>
      </Table>
    </div>
  )
}

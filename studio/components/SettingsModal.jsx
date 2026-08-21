'use client'

import { useEffect, useState } from 'react'
import { Cloud, CloudOff, Database, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { Button, Input, Modal } from './ui'
import { cn } from '@/lib/utils'
import DeployAccounts from './deploy/DeployAccounts'

export default function SettingsModal({ onClose, onSaved }) {
  const [host, setHost] = useState('')
  const [ctx, setCtx] = useState('')
  const [key, setKey] = useState('')
  const [mongo, setMongo] = useState('')
  const [photo, setPhoto] = useState('')
  const [meta, setMeta] = useState(null)
  const [saving, setSaving] = useState(false)
  const [note, setNote] = useState('loading…')
  const [tone, setTone] = useState('muted')

  useEffect(() => {
    let alive = true
    api.settings()
      .then(d => {
        if (!alive) return
        setHost(d.ollama_host || '')
        setCtx(d.local_num_ctx || '')
        setMeta(d)
        setNote(d.cloud_enabled ? 'cloud enabled' : 'cloud off — local only')
        setTone(d.cloud_enabled ? 'ok' : 'muted')
      })
      .catch(() => { if (alive) { setNote('server offline'); setTone('bad') } })
    return () => { alive = false }
  }, [])

  async function save() {
    setSaving(true)
    setNote('saving…')
    setTone('muted')
    const body = { ollama_host: host.trim(), local_num_ctx: String(ctx).trim() }
    if (key.trim()) body.ollama_api_key = key.trim()

    if (mongo.trim()) body.mongodb_uri = mongo.trim() === '-' ? '' : mongo.trim()
    if (photo.trim()) body.unsplash_key = photo.trim() === '-' ? '' : photo.trim()
    try {
      const d = await api.saveSettings(body)
      setNote(!d.cloud_enabled ? 'cloud off — local only'
        : d.cloud_reachable ? 'cloud key verified' : 'key saved but not accepted')
      setTone(!d.cloud_enabled ? 'muted' : d.cloud_reachable ? 'ok' : 'bad')
      onSaved?.()
      if (d.cloud_reachable) setTimeout(onClose, 800)
    } catch (e) {
      setNote('save failed — ' + e.message)
      setTone('bad')
    }
    setSaving(false)
  }

  const cloudOn = tone === 'ok'

  return (
    // Full size. `max-w-none` is what releases the 520px the Modal primitive
    // sets for small dialogs — tailwind-merge lets the later class win, so
    // the width below is the one that applies.
    <Modal onClose={onClose}
           className="max-w-none w-[min(1160px,95vw)] h-[92vh] overflow-y-auto p-7">
      <header className="mb-4 flex items-center gap-3 border-b-2 border-line2 pb-3">
        <span className={cn('grid size-8 shrink-0 place-items-center border',
          cloudOn ? 'border-accent bg-accent text-bg'
                  : 'border-line2 bg-panel2 text-muted')}>
          {cloudOn ? <Cloud className="size-4" /> : <CloudOff className="size-4" />}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-[16px] font-extrabold uppercase
                         tracking-[-.01em] text-ink">
            Settings
          </h3>
          <p className={cn('mt-0.5 font-mono text-[10.5px]',
            { ok: 'text-ink', bad: 'text-deep', muted: 'text-muted2' }[tone])}>
            {note}
          </p>
        </div>
      </header>

      {/* Two columns once there is room for them. A single stack of five
          fields down a 1160px sheet leaves most of it empty. */}
      <div className="grid gap-3 lg:grid-cols-2">
        <Field label="Ollama host" value={host} onChange={setHost}
               placeholder="http://127.0.0.1:11434" />
        <Field label="ollama.com key" value={key} onChange={setKey} type="password"
               placeholder={meta?.api_key_hint
                 ? `key saved (${meta.api_key_hint})`
                 : 'paste your ollama.com key'} />
        <Field label="Local context window" value={ctx} onChange={setCtx}
               placeholder="e.g. 32768" />
        <Field label="Unsplash key" value={photo} onChange={setPhoto} type="password"
               placeholder={meta?.unsplash_key ? 'key saved' : 'optional — for demo photographs'}
               hint="Used only for the five design demos, and only while they are being drawn — the photographs are inlined, so a demo still works offline afterwards. Without a key the demos use drawn placeholders." />
        <Field label="MongoDB URI" value={mongo} onChange={setMongo}
               placeholder={meta?.mongodb_uri_set
                 ? `saved (${meta.mongodb_uri_hint})`
                 : 'leave empty to use the local MongoDB'}
               hint="Type a single - to clear a saved URI." />
      </div>

      <MongoState mongo={meta?.mongo} />

      <DeployAccounts deploy={meta?.deploy} onSaved={() => {
        api.settings().then(setMeta).catch(() => { })
        onSaved?.()
      }} />

      <footer className="mt-5 flex items-center gap-2 border-t-2 border-line2 pt-3">
        <Button variant="solid" size="lg" disabled={saving} onClick={save}>
          {saving && <Loader2 className="size-3.5 animate-spin" />} Save
        </Button>
        <Button variant="outline" size="lg" onClick={onClose}>Close</Button>
        <span className="flex-1" />
        {meta?.mongo && !meta.mongo.downloaded && !meta.mongo.override && (
          <Button size="lg" onClick={() => api.mongoPrefetch().catch(() => { })}>
            Download mongod
          </Button>
        )}
      </footer>
    </Modal>
  )
}

const Field = ({ label, value, onChange, hint, ...rest }) => (
  <label className="block">
    <span className="label-2xs mb-1 block text-label">{label}</span>
    <Input value={value} onChange={e => onChange(e.target.value)} {...rest} />
    {hint && <span className="mt-1 block text-[10px] text-muted2">{hint}</span>}
  </label>
)

function MongoState({ mongo }) {
  if (!mongo) return null
  let text, tone
  if (mongo.override) {
    text = 'using your MONGODB_URI'
    tone = 'text-ink'
  } else if (mongo.running) {
    text = mongo.external
      ? `adopted the MongoDB already on :${mongo.port}`
      : `MongoDB running on :${mongo.port}`
    tone = 'text-ink'
  } else if (mongo.downloaded) {
    text = 'mongod downloaded, not running'
    tone = 'text-muted'
  } else {
    text = mongo.reason || 'mongod not downloaded yet'
    tone = mongo.reason ? 'text-deep' : 'text-muted'
  }
  return (
    <p className={cn('mt-3 flex items-center gap-2 border border-line2',
                     'bg-panel2 px-2.5 py-2 font-mono text-[10.5px]', tone)}>
      <Database className="size-3.5 shrink-0" /> {text}
    </p>
  )
}

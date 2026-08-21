'use client'

import { useEffect, useState } from 'react'
import { Check, ExternalLink, Loader2, X } from 'lucide-react'
import { api } from '@/lib/api'
import { Button, Input, SectionLabel } from '../ui'
import { cn } from '@/lib/utils'


const SSO_REGIONS = ['us-east-1', 'us-east-2', 'us-west-2', 'eu-west-1',
                     'eu-central-1', 'ap-south-1', 'ap-southeast-1',
                     'ap-southeast-2', 'ap-northeast-1']
const AWS_REGIONS = ['ap-south-1', 'us-east-1', 'us-east-2', 'us-west-2',
                     'eu-west-1', 'eu-west-2', 'eu-central-1',
                     'ap-southeast-1', 'ap-southeast-2', 'ap-northeast-1']

export default function DeployAccounts({ deploy, onSaved }) {
  const [open, setOpen] = useState(false)
  const [probe, setProbe] = useState(null)
  const [note, setNote] = useState('')

  useEffect(() => {
    if (!open || probe) return
    api.deployRead('/onboarding/status')
      .then(setProbe)
      .catch(e => setNote(`could not read the deployment agent — ${e.message}`))
  }, [open, probe])

  const save = async (patch) => {
    await api.saveSettings(patch)
    onSaved?.()
  }

  return (
    <section className="mt-5 border-t-2 border-line2 pt-3">
      <button onClick={() => setOpen(o => !o)} className="w-full text-left">
        <SectionLabel className="border-b-2 border-line2 pb-1.5" right={<span className="text-[10px] text-muted2">
                               {open ? 'hide' : 'show'}
                             </span>}>
          Deployment accounts
        </SectionLabel>
      </button>
      {!open && (
        <p className="mt-1.5 text-[10.5px] text-muted2">
          {summarise(deploy)}
        </p>
      )}

      {open && (
        <div className="mt-3 space-y-4">
          {note && (
            <p className="border-l-[3px] border-accent bg-tint px-2.5 py-1.5
                          text-[11px] text-deep">
              {note}
            </p>
          )}
          <Github probe={probe} onRecheck={() => setProbe(null)} />
          <Aws deploy={deploy} onSave={save} probe={probe} />
          <Vercel deploy={deploy} onSave={save} />
          <Mongo deploy={deploy} onSave={save} />
        </div>
      )}
    </section>
  )
}

function summarise(d) {
  if (!d) return 'AWS, Vercel and the production database.'
  const bits = []
  bits.push(d.aws_profile ? `AWS ${d.aws_profile}` : 'AWS not connected')

  bits.push(d.vercel_token_set || d.vercel_cli_signed_in
    ? 'Vercel connected' : 'Vercel not connected')
  bits.push(d.mongodb_uri_set ? 'database set' : 'no database')
  return bits.join(' · ')
}


function Github({ probe, onRecheck }) {
  const [busy, setBusy] = useState(false)
  const ok = Boolean(probe?.github_authenticated)

  async function signIn() {
    setBusy(true)
    try {

      await api.deploy('/onboarding/login', { tool: 'github' })
    } catch { }
    setBusy(false)
  }

  return (
    <Row title="GitHub" ok={ok} unknown={!probe}
         detail={ok ? `signed in as ${probe.github_account || 'your account'}`
                    : 'the deployment creates a private repository and pushes'
                      + 'the workflows that build it'}
         actions={<>
           {!ok && (
             <Button size="sm" variant="outline" disabled={busy} onClick={signIn}>
               {busy && <Loader2 className="size-3 animate-spin" />} Sign in
             </Button>
           )}
           <Button size="sm" onClick={onRecheck}>Recheck</Button>
         </>} />
  )
}


function Aws({ deploy, onSave, probe }) {
  const [profile, setProfile] = useState(deploy?.aws_profile || '')
  const [startUrl, setStartUrl] = useState(deploy?.aws_start_url || '')
  const [ssoRegion, setSsoRegion] = useState(deploy?.aws_sso_region || 'us-east-1')
  const [region, setRegion] = useState(deploy?.aws_region || 'ap-south-1')
  const [flow, setFlow] = useState(null)
  const [accounts, setAccounts] = useState(null)
  const [account, setAccount] = useState('')
  const [roles, setRoles] = useState([])
  const [role, setRole] = useState('')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')

  const connected = Boolean(deploy?.aws_profile)

  async function begin() {
    setErr('')
    setBusy('starting')
    try {
      const f = await api.deploy('/aws/sso/start',
                                 { start_url: startUrl.trim(), region: ssoRegion })
      setFlow(f)

      const link = f.verification_uri_complete || f.verification_uri
      if (link) window.open(link, '_blank', 'noopener,noreferrer')
      poll(f)
    } catch (e) {
      setErr(e.message)
      setBusy('')
    }
  }

  async function poll(f) {
    setBusy('waiting')
    const every = Math.max(2, Number(f.interval || 5)) * 1000
    const deadline = Date.now() + Math.max(60, Number(f.expires_in || 600)) * 1000
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, every))
      try {
        const d = await api.deploy('/aws/sso/poll', { flow_id: f.flow_id })
        if (d.status === 'complete') {
          setAccounts(d.accounts || [])
          setBusy('')
          return
        }
      } catch (e) {
        setErr(e.message)
        setBusy('')
        return
      }
    }
    setErr('the AWS sign-in expired — start it again')
    setBusy('')
  }

  async function pickAccount(id) {
    setAccount(id)
    setRole('')
    setRoles([])
    if (!id) return
    try {
      const d = await api.deploy('/aws/sso/roles',
                                 { flow_id: flow.flow_id, account_id: id })
      setRoles(d.roles || [])
      if ((d.roles || []).length === 1) setRole(d.roles[0])
    } catch (e) { setErr(e.message) }
  }

  async function use() {
    setBusy('selecting')
    setErr('')
    try {

      const d = await api.deploy('/aws/sso/select', {
        flow_id: flow.flow_id, account_id: account, role_name: role,
        persist_profile: true, profile: 'deployment-agent',
        deployment_region: region,
      })
      await onSave({
        aws_profile: d.profile || 'deployment-agent',
        aws_region: region,
        aws_start_url: startUrl.trim(),
        aws_sso_region: ssoRegion,
      })
      setFlow(null)
      setAccounts(null)
    } catch (e) {
      setErr(e.message)
    }
    setBusy('')
  }

  return (
    <Row title="AWS" ok={connected} unknown={false}
         detail={connected
           ? `profile ${deploy.aws_profile} · ${deploy.aws_region || 'ap-south-1'}`
           : 'sign in through IAM Identity Center — no access keys are stored'}>
      <div className="mt-2 w-full space-y-2">
        <div className=" border border-line bg-panel2 px-2.5 py-2">
          <p className="text-[11px] text-muted">
            Sign in through the browser. Nothing is typed here, and no access
            key is stored — only a short-lived session.
          </p>
          <Button size="sm" variant="solid" className="mt-2"
                  disabled={Boolean(busy)}
                  onClick={async () => {
                    setBusy('console'); setErr('')
                    try {
                      await api.deploy('/onboarding/login', {
                        tool: 'aws-console-login',
                        profile: 'deployment-agent',
                        region,
                      })

                      setProfile('deployment-agent')
                    } catch (e) { setErr(e.message) }
                    setBusy('')
                  }}>
            {busy === 'console' && <Loader2 className="size-3 animate-spin" />}
            Sign in to AWS in the browser
          </Button>
        </div>

        <Field label="Identity Center start URL">
          <Input value={startUrl} onChange={e => setStartUrl(e.target.value)}
                 placeholder="https://d-xxxxxxxxxx.awsapps.com/start" />
        </Field>
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="Sign-in region">
            <Select value={ssoRegion} onChange={setSsoRegion} options={SSO_REGIONS} />
          </Field>
          <Field label="Deploy into">
            <Select value={region} onChange={setRegion} options={AWS_REGIONS} />
          </Field>
        </div>

        {!flow && (
          <Button size="sm" variant="outline"
                  disabled={!startUrl.trim() || Boolean(busy)} onClick={begin}>
            {busy === 'starting' && <Loader2 className="size-3 animate-spin" />}
            {connected ? 'Connect a different account' : 'Sign in to AWS'}
          </Button>
        )}

        {flow && !accounts && (
          <div className=" border border-line bg-bg px-2.5 py-2">
            <p className="text-[11px] text-muted">
              Approve this in the browser tab that opened, then come back.
            </p>
            <p className="mt-1 flex items-center gap-2">
              <code className=" bg-panel2 px-1.5 py-0.5 font-mono
                               text-[13px] tracking-[2px] text-ink">
                {flow.user_code}
              </code>
              <a href={flow.verification_uri_complete || flow.verification_uri}
                 target="_blank" rel="noreferrer"
                 className="inline-flex items-center gap-1 text-[10.5px] text-accent
                            hover:underline">
                open again <ExternalLink className="size-2.5" />
              </a>
              {busy === 'waiting' && (
                <Loader2 className="size-3 animate-spin text-muted2" />
              )}
            </p>
          </div>
        )}

        {accounts && (
          <div className="space-y-2">
            <Field label="Account">
              <Select value={account} onChange={pickAccount} placeholder="choose an account"
                      options={accounts.map(a => ({
                        value: a.account_id,
                        label: `${a.account_name || a.account_id} (${a.account_id})`,
                      }))} />
            </Field>
            {roles.length > 0 && (
              <Field label="Role">
                <Select value={role} onChange={setRole} placeholder="choose a role"
                        options={roles} />
              </Field>
            )}
            <Button size="sm" variant="solid"
                    disabled={!account || !role || Boolean(busy)} onClick={use}>
              {busy === 'selecting' && <Loader2 className="size-3 animate-spin" />}
              Use this account
            </Button>
          </div>
        )}

        <div className="border-t border-line pt-2">
          <Field label="Or use an AWS CLI profile you already have"
                 hint="Made by `aws configure sso`, or any profile that works.
                       Nothing secret is copied — only the name.">
            {(probe?.aws_profiles || []).length > 0 ? (
              <Select value={profile} onChange={setProfile}
                      placeholder="choose a profile"
                      options={probe.aws_profiles.map(p => ({
                        value: typeof p === 'string' ? p : p.name,
                        label: typeof p === 'string' ? p : p.name,
                      }))} />
            ) : (
              <Input value={profile} onChange={e => setProfile(e.target.value)}
                     placeholder="deployment-agent" />
            )}
          </Field>
          <Button size="sm" variant="outline" className="mt-2"
                  disabled={!profile.trim() || Boolean(busy)}
                  onClick={async () => {
                    setBusy('profile'); setErr('')
                    try {
                      await onSave({ aws_profile: profile.trim(),
                                     aws_region: region })
                    } catch (e) { setErr(e.message) }
                    setBusy('')
                  }}>
            {busy === 'profile' && <Loader2 className="size-3 animate-spin" />}
            Use this profile
          </Button>
        </div>

        {err && <p className="text-[10.5px] text-deep">{err}</p>}
      </div>
    </Row>
  )
}


function Vercel({ deploy, onSave }) {
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState('')
  const [status, setStatus] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    let ok = true
    api.deploy('/aws/vercel/status', { token: '' })
      .then(d => { if (ok) setStatus(d) })
      .catch(() => { })
    return () => { ok = false }
  }, [deploy?.vercel_token_set])

  async function check() {
    setBusy('check')
    setErr('')
    try {
      setStatus(await api.deploy('/aws/vercel/status', { token: token.trim() }))
    } catch (e) { setErr(e.message) }
    setBusy('')
  }

  async function cli() {
    setBusy('cli')
    try { await api.deploy('/onboarding/login', { tool: 'vercel' }) } catch { }
    setBusy('')
  }

  async function saveToken() {
    setBusy('save')
    setErr('')
    try {
      await onSave({ vercel_token: token.trim() })
      setToken('')
    } catch (e) { setErr(e.message) }
    setBusy('')
  }

  const who = status?.username || status?.email || status?.name || ''
  const connected = Boolean(status?.connected) || Boolean(deploy?.vercel_token_set)
    || Boolean(deploy?.vercel_cli_signed_in)
  const detail = status?.connected
    ? `connected as ${who}${status.source ? ` (${status.source})` : ''}`
    : deploy?.vercel_token_set
      ? `token saved (${deploy.vercel_token_hint})`
      : 'sign in with the CLI, or paste a token'

  return (
    <Row title="Vercel" ok={connected}
         unknown={!status && !deploy?.vercel_token_set && !deploy?.vercel_cli_signed_in}
         detail={detail}>
      <div className="mt-2 w-full space-y-2">
        <Field label="Access token"
               hint="Stored here and set as a GitHub Actions secret on the repository
                     the deployment creates — Vercel has no OIDC equivalent. Type a
                     single - to clear it.">
          <Input type="password" value={token} onChange={e => setToken(e.target.value)}
                 placeholder={deploy?.vercel_token_set
                   ? `saved (${deploy.vercel_token_hint})`
                   : 'paste a Vercel token'} />
        </Field>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" disabled={!token.trim() || Boolean(busy)}
                  onClick={saveToken}>
            {busy === 'save' && <Loader2 className="size-3 animate-spin" />} Save token
          </Button>
          <Button size="sm" disabled={Boolean(busy)} onClick={cli}>
            {busy === 'cli' && <Loader2 className="size-3 animate-spin" />}
            Sign in with the CLI
          </Button>
          <Button size="sm" disabled={Boolean(busy)} onClick={check}>
            {busy === 'check' && <Loader2 className="size-3 animate-spin" />} Check
          </Button>
        </div>
        {status && !status.connected && (
          <p className="text-[10.5px] text-muted">
            {status.message || 'not connected yet'}
          </p>
        )}
        {err && <p className="text-[10.5px] text-deep">{err}</p>}
      </div>
    </Row>
  )
}


function Mongo({ deploy, onSave }) {
  const [uri, setUri] = useState('')
  const [busy, setBusy] = useState('')
  const [result, setResult] = useState(null)
  const [err, setErr] = useState('')

  async function test() {
    setBusy('test')
    setErr('')
    setResult(null)
    try {

      setResult(await api.deploy('/mongodb/check', { uri: uri.trim() }))
    } catch (e) { setErr(e.message) }
    setBusy('')
  }

  async function save() {
    setBusy('save')
    setErr('')
    try {
      await onSave({ deploy_mongodb_uri: uri.trim() })
      setUri('')
    } catch (e) { setErr(e.message) }
    setBusy('')
  }

  return (
    <Row title="Production database" ok={Boolean(deploy?.mongodb_uri_set)} unknown={false}
         detail={deploy?.mongodb_uri_set ? `saved (${deploy.mongodb_uri_hint})`
                                         : 'the deployed app needs one it can reach'}>
      <div className="mt-2 w-full space-y-2">
        <Field label="MongoDB URI"
               hint="Kept separate from the MongoDB URI above, which is AgentForge's own
                     and is usually a local one. A loopback address is refused here —
                     deployed, it would point at a database that does not exist.">
          <Input type="password" value={uri} onChange={e => setUri(e.target.value)}
                 placeholder={deploy?.mongodb_uri_set
                   ? `saved (${deploy.mongodb_uri_hint})`
                   : 'mongodb+srv://user:password@cluster.mongodb.net/database'} />
        </Field>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" disabled={!uri.trim() || Boolean(busy)}
                  onClick={save}>
            {busy === 'save' && <Loader2 className="size-3 animate-spin" />} Save
          </Button>
          <Button size="sm" disabled={!uri.trim() || Boolean(busy)} onClick={test}>
            {busy === 'test' && <Loader2 className="size-3 animate-spin" />} Test
          </Button>
        </div>
        {result && (
          <p className={cn('text-[10.5px]', result.ok ? 'text-ok' : 'text-bad')}>
            {result.message}
            {result.server_version ? ` · MongoDB ${result.server_version}` : ''}
          </p>
        )}
        {err && <p className="text-[10.5px] text-deep">{err}</p>}
      </div>
    </Row>
  )
}


function Row({ title, ok, unknown, detail, actions, children }) {
  return (
    <div className={cn('border border-line2 border-l-[3px] bg-panel2 p-2.5',
      unknown ? 'border-l-transparent' : ok ? 'border-l-ok' : 'border-l-bad')}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="grid size-3.5 shrink-0 place-items-center">
          {unknown ? <Loader2 className="size-3 animate-spin text-muted2" />
                   : ok ? <Check className="size-3.5 text-ok" />
                        : <X className="size-3.5 text-bad" />}
        </span>
        <span className="text-[12px] font-extrabold text-ink">{title}</span>
        <span className="min-w-0 flex-1 truncate text-[10.5px] text-muted">{detail}</span>
        {actions}
      </div>
      {children}
    </div>
  )
}

const Field = ({ label, hint, children }) => (
  <label className="block">
    <span className="label-2xs mb-1 block text-label">{label}</span>
    {children}
    {hint && <span className="mt-1 block text-[9.5px] leading-snug text-muted2">
               {hint}
             </span>}
  </label>
)

const Select = ({ value, onChange, options, placeholder }) => (
  <select value={value} onChange={e => onChange(e.target.value)}
          className="h-[30px] w-full border border-line2 bg-panel2 px-2
                     text-[12px] text-ink outline-none focus:border-accent">
    {placeholder && <option value="">{placeholder}</option>}
    {options.map(o => {
      const v = typeof o === 'string' ? o : o.value
      const l = typeof o === 'string' ? o : o.label
      return <option key={v} value={v}>{l}</option>
    })}
  </select>
)

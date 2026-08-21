
export const API = '/__agentforge/api'

async function req(path, opts) {
  const r = await fetch(API + path, opts)
  const text = await r.text()
  let data = null
  try { data = text ? JSON.parse(text) : null } catch { data = { raw: text } }
  if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`)
  return data
}

const post = (path, body) => req(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body || {}),
})

export const api = {
  projects: () => req('/projects'),
  models: () => req('/models'),
  mongo: () => req('/mongo'),
  settings: () => req('/settings'),
  saveSettings: (s) => post('/settings', s),
  imageCheck: () => req('/image-check'),
  imageStart: () => post('/image-start', {}),
  files: (project) => req(`/files/${encodeURIComponent(project)}`),
  saveFile: (project, path, content) => post('/save-file', { project, path, content }),
  open: (project) => post(`/open/${encodeURIComponent(project)}`, {}),
  deleteProject: (project) => post('/delete-project', { project }),

  // Stop the running build. The backend kills whatever child process is
  // holding the thread, then removes the half-built project and the
  // specification it was built from — a cancelled run leaves nothing.
  cancelBuild: () => post('/build/cancel', {}),

  // Throw away a specification that has not been approved. Answered by the
  // local backend, not the SRS agent: the staging directory is a file on
  // this machine and the two share it.
  discardSrs: (srs_id) => post('/discard-srs', { srs_id }),
  undo: (project, id) => post('/undo', { project, id }),

  logoPrompt: (prompt, model, opts) => localJob('/logo-prompt', { prompt, model }, opts),
  image: (body, opts) => localJob('/image', body, opts),

  // Five designs of one page. A job, not a POST: it is five model calls and
  // outlives any request timeout.
  themes: (body, opts) => localJob('/themes', body, opts),
  tune: (body, opts) => localJob('/tune', body, opts),

  // The same answer as `image`, from a file instead of a prompt. A plain POST
  // rather than a job: this writes a file, where generating one waits on
  // Fooocus and needs the shim to outlive the 30s rewrite.
  imageUpload: (file, body) => Promise.resolve(tooBig(file)).then(big => {
    if (big) throw big
    return fileToBase64(file).then(data_base64 =>
      post('/image-upload', { ...body, filename: file.name, data_base64 }))
  }),

  // Read one attachment for an editing chat: a picture is saved into the
  // project and described, a PDF and a recording come back as text.
  attach: (file, body) => Promise.resolve(tooBig(file)).then(big => {
    if (big) throw big
    return fileToBase64(file).then(data_base64 =>
      post('/attach', { ...body, filename: file.name, data_base64 }))
  }),

  // Put your own picture where the one you picked is. Returns as soon as the
  // work is queued; the result arrives over the websocket like every other
  // edit, so the log, the phases and the undo point behave the same.
  imageSwap: (file, body) => Promise.resolve(tooBig(file)).then(big => {
    if (big) throw big
    return fileToBase64(file).then(data_base64 =>
      post('/image-swap', { ...body, filename: file.name, data_base64 }))
  }),
  uploadProject: (body) => post('/upload-project', body),
  mongoPrefetch: () => post('/mongo/prefetch', {}),

  qa: (project) => req(`/qa/${encodeURIComponent(project)}`),
  qaPdfUrl: (project) => `${API}/qa-pdf/${encodeURIComponent(project)}`,

  srsResults: (project) => req(`/srs-results/${encodeURIComponent(project)}`),

  srsPdfUrl: (project) => `${API}/srs-pdf/${encodeURIComponent(project)}`,
  srsStatus: () => req('/srs-status'),

  srs: (path, body) => body === undefined
    ? req(`/srs${path}`)
    : srsJob(path, body),

  // `purpose` is what the person says the file is for. The model can
  // describe any picture; only they know which job it has.
  srsUpload: (project, file, opts = {}) => Promise.resolve(tooBig(file)).then(big => {
    if (big) throw big
    return fileToBase64(file).then(data_base64 =>
      srsJob(`/projects/${encodeURIComponent(project)}/inputs-json`, {
        mode: uploadMode(file),
        filename: file.name || 'upload',
        content_type: file.type || '',
        purpose: opts.purpose || '',
        data_base64,
      }, opts))
  }),

  deployStatus: () => req('/deploy-status'),
  deployResults: (project) => req(`/deploy-results/${encodeURIComponent(project)}`),
  deployStart: (body) => post('/deploy-start', body),

  deploy: (path, body) => body === undefined
    ? req(`/deploy${path}`)
    : deployJob('POST', path, body),

  deployRead: (path, opts) => deployJob('GET', path, null, opts),
}


async function deployJob(method, path, body, { onWait, signal } = {}) {
  const started = await post('/deploy/jobs', { path, method, body })
  const id = started.job_id
  for (let i = 0; ; i++) {
    if (signal?.aborted) throw new Error('cancelled')
    await new Promise(r => setTimeout(r, i < 10 ? 300 : 900))
    const job = await req(`/deploy/jobs/${id}`)
    if (job.status === 'running') { onWait?.(job.elapsed); continue }
    if (job.status === 'error') throw new Error(job.error || 'the deployment agent failed')
    if (job.http_status >= 400) {
      const detail = job.result?.error ?? job.result?.detail
      throw new Error(typeof detail === 'string'
        ? detail : `HTTP ${job.http_status}`)
    }
    return job.result
  }
}

export { deployJob }

async function srsJob(path, body, { onWait, signal } = {}) {
  const started = await post('/srs/jobs', { path, method: 'POST', body })
  const id = started.job_id
  for (let i = 0; ; i++) {
    if (signal?.aborted) throw new Error('cancelled')
    await new Promise(r => setTimeout(r, i < 10 ? 300 : 900))
    const job = await req(`/srs/jobs/${id}`)
    if (job.status === 'running') { onWait?.(job.elapsed); continue }
    if (job.status === 'error') throw new Error(job.error || 'the SRS failed')
    if (job.http_status >= 400) {
      const detail = job.result?.detail ?? job.result?.error
      throw new Error(typeof detail === 'string'
        ? detail : `HTTP ${job.http_status}`)
    }
    return job.result
  }
}

export { srsJob }


// An attachment goes up as base64 inside a JSON body rather than as multipart,
// because the job shim above is what keeps a slow upload alive: the rewrite in
// front of the SRS abandons any request at 30 seconds, and transcribing a
// recording or showing a scanned PDF to a vision model runs past that. The shim
// replays JSON only, so the file has to be JSON.
// Measured against the running stack: the Next rewrite in front of both APIs
// refuses a request body over 10 MiB with a bare 500 that never reaches Python,
// so nothing server-side can turn it into a sentence. base64 costs a third on
// top, which leaves roughly this much actual file. Checked here so the answer
// is instant and says what is wrong, rather than a crash after a long upload.
export const MAX_UPLOAD_BYTES = 7_500_000

function tooBig(file) {
  if ((file?.size || 0) <= MAX_UPLOAD_BYTES) return null
  const mb = n => `${(n / 1_000_000).toFixed(1)} MB`
  return new Error(
    `${file.name || 'that file'} is ${mb(file.size)} — the limit is ${mb(MAX_UPLOAD_BYTES)}.`)
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error(`${file.name} could not be read`))
    // readAsDataURL gives "data:<type>;base64,<payload>"; the server strips the
    // prefix too, so a change here cannot break it.
    reader.onload = () => resolve(String(reader.result || '').split(',')[1] || '')
    reader.readAsDataURL(file)
  })
}

export const ACCEPT_UPLOAD =
  '.pdf,.png,.jpg,.jpeg,.webp,.gif,.bmp,.wav,.mp3,.m4a,.ogg,.webm,.flac,image/*,audio/*,application/pdf'

export function uploadMode(file) {
  const type = (file.type || '').toLowerCase()
  const name = (file.name || '').toLowerCase()
  if (type.includes('pdf') || name.endsWith('.pdf')) return 'pdf'
  if (type.startsWith('image/') || /\.(png|jpe?g|webp|gif|bmp)$/.test(name)) return 'image'
  if (type.startsWith('audio/') || /\.(wav|mp3|m4a|ogg|webm|flac)$/.test(name)) return 'voice'
  return 'text'
}

export { fileToBase64 }


async function localJob(path, body, { onWait, signal } = {}) {
  const started = await post('/jobs', { path, method: 'POST', body })
  const id = started.job_id
  for (let i = 0; ; i++) {
    if (signal?.aborted) throw new Error('cancelled')
    await new Promise(r => setTimeout(r, i < 10 ? 300 : 900))
    const job = await req(`/jobs/${id}`)
    if (job.status === 'running') { onWait?.(job.elapsed); continue }
    if (job.status === 'unknown') throw new Error(job.error || 'the job expired')
    if (job.status === 'error') throw new Error(job.error || 'the request failed')
    if (job.http_status >= 400) {
      const detail = job.result?.error ?? job.result?.detail
      throw new Error(typeof detail === 'string' ? detail : `HTTP ${job.http_status}`)
    }
    return job.result
  }
}

export { localJob }


export const HTTP_FALLBACK = {
  build: '/build',
  update: '/update',
  agent_build: '/agent-build',
  agent_update: '/agent-update',
  agent_resume: '/resume',
  feature: '/feature',
  element_edit: '/element-edit',

  image_edit: '/image-edit',
  pencil_edit: '/pencil-edit',
}

export const CURATED = [
  { id: 'llama3.1:8b', label: 'Llama 3.1 8B', tag: 'fast',
    desc: 'Meta. Fast instruction following.' },
  { id: 'llama3.2:3b', label: 'Llama 3.2 3B', tag: 'fast',
    desc: 'Meta. Ultra-fast, lightweight.' },
  { id: 'mistral:7b', label: 'Mistral 7B', tag: 'fast',
    desc: 'Mistral AI. Quick, solid reasoning.' },
  { id: 'phi4:14b', label: 'Phi-4 14B', tag: 'quality',
    desc: 'Microsoft. Excellent reasoning for its size.' },
  { id: 'gemma3:12b', label: 'Gemma 3 12B', tag: 'quality',
    desc: 'Google. Strong general model.' },
  { id: 'qwen2.5-coder:14b', label: 'Qwen 2.5 Coder 14B', tag: 'code',
    desc: 'Best local model for code.' },
  { id: 'qwen2.5-coder:7b', label: 'Qwen 2.5 Coder 7B', tag: 'code',
    desc: 'Lighter code model.' },
  { id: 'deepseek-coder-v2:16b', label: 'DeepSeek Coder V2 16B', tag: 'code',
    desc: 'Excellent at code generation.' },
  { id: 'codellama:13b', label: 'Code Llama 13B', tag: 'code',
    desc: 'Meta. Llama fine-tuned for code.' },
  { id: 'llama3.1:70b', label: 'Llama 3.1 70B', tag: 'big',
    desc: 'Highest quality — needs a lot of memory.' },
  { id: 'qwen2.5:72b', label: 'Qwen 2.5 72B', tag: 'big',
    desc: 'Very capable — needs ~40GB.' },
]

export const isCloud = (id) => String(id || '').includes('-cloud')
  || String(id || '').endsWith(':cloud')

export function catalogue(payload) {
  const p = payload || {}
  const cloud = (p.cloud || []).map(m => ({ ...m, cloud: true }))
  const localObjs = (p.local_models || []).map(m =>
    typeof m === 'string' ? { id: m } : m)
  const installed = (p.local || []).map(m => typeof m === 'string' ? m : m.id)

  const byId = new Map()
  for (const m of localObjs) byId.set(m.id, { tag: 'local', ...m })
  for (const id of installed) {
    if (!byId.has(id)) byId.set(id, { id, tag: 'local' })
  }

  for (const m of CURATED) {
    if (!byId.has(m.id)) byId.set(m.id, { ...m, willPull: true })
  }
  const local = [...byId.values()]
  return {
    cloud, local, installed,
    all: [...cloud, ...local],
    cloudEnabled: !!p.cloud_enabled,
    cloudVia: p.cloud_via || 'none',
  }
}

export const hasVision = (all, id) => !!all.find(m => m.id === id)?.vision

export const maxContext = (all, id) =>
  Number(all.find(m => m.id === id)?.ctx) || 0

export const roomyContext = (all, id) => {
  const ctx = maxContext(all, id)
  return ctx === 0 || ctx >= 32768
}

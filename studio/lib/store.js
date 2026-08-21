
import { create } from 'zustand'
import { advance, emptyProgress } from './progress-model'

const LS = typeof window === 'undefined' ? null : window.localStorage
const read = (k, fallback) => {
  try { return LS?.getItem(k) ?? fallback } catch { return fallback }
}
const readJSON = (k, fallback) => {
  try { return JSON.parse(LS?.getItem(k) || '') ?? fallback } catch { return fallback }
}


const DEFAULTS = {
// Use the light theme until the browser saves another choice.
  theme: 'light',

  models: { refine: 'llama3.1:8b', build: 'qwen2.5-coder:14b', agent: '', qa: '',
            srs: '', deploy: '', image: 'fooocus' },
  agentMode: true,
  think: false,
  images: false,
  hist: [],
}


export const KEYS = {
  theme: 'agentforge-theme', hist: 'agentforge-hist',
  refine: 'agentforge-rm', build: 'agentforge-bm', agent: 'agentforge-am', qa: 'agentforge-qm',
  srs: 'agentforge-sm', deploy: 'agentforge-dm', image: 'agentforge-im',

  srsId: 'agentforge-srs-id', srsPhase: 'agentforge-srs-phase',
  agentMode: 'agentforge-agent', think: 'agentforge-think', images: 'agentforge-img',
}


const RESUMABLE_SRS_PHASES = new Set(['interview', 'plan', 'review'])




export const useStore = create((set, get) => ({

  status: 'connecting',
  statusText: 'connecting…',
  setStatus: (status, statusText) => set({ status, statusText }),

  busy: false,
  // Stored projects use the build overlay while opening.
  opening: false,

  // Increment when projects on disk change.
  projectsStamp: 0,

  // Which kind of work is running.
  workKind: '',
  setWorkKind: (workKind) => set({ workKind }),
  setOpening: (opening) => set({ opening }),
  askOpen: false,
  setAskOpen: (askOpen) => set({ askOpen }),
  setBusy: (busy) => set(busy ? { busy } : { busy, opening: false }),
  bumpProjects: () => set(s => ({ projectsStamp: s.projectsStamp + 1 })),

  // Which page of the generated app the preview is showing.
  previewRoute: '/',
  setPreviewRoute: (previewRoute) => set({ previewRoute }),

  e2eLive: null,
  setE2eLive: (e2eLive) => set({ e2eLive }),
  project: null,
  view: 'preview',
  setView: (view) => set({ view }),

  srsId: null,

  srsPhase: 'idle',
  srsBusy: '',

  setSrs: (patch) => {
    set(patch)
    try {
      const s = get()
      if (s.srsId) {
        LS?.setItem(KEYS.srsId, s.srsId)
        LS?.setItem(KEYS.srsPhase, s.srsPhase || 'idle')
      }
    } catch { }
  },
  resetSrs: () => {
    set({ srsId: null, srsPhase: 'idle', srsBusy: '' })
    try {
      LS?.removeItem(KEYS.srsId)
      LS?.removeItem(KEYS.srsPhase)
    } catch { }
  },

  logs: [],
  addLog: (level, text) => set(s => ({
    logs: [...s.logs.slice(-800), { level, text, at: Date.now() }],
  })),

  steps: {},
  setStep: (id, status) => set(s => ({ steps: { ...s.steps, [id]: status } })),
  progress: emptyProgress(),
  setProgress: (step, pct) =>
    set(s => ({ progress: advance(s.progress, step, pct) })),
  phases: [],
  upsertPhase: (p) => set(s => {
    const i = s.phases.findIndex(x => x.phase === p.phase)
    if (i < 0) return { phases: [...s.phases, p] }
    const next = s.phases.slice()
    next[i] = { ...next[i], ...p }
    return { phases: next }
  }),

  files: {},
  activeFile: null,
  liveFile: null,
  liveBuf: '',

  // While a file is being written the code pane follows the writer.
  follow: true,
  putFile: (name, content) => set(s => ({ files: { ...s.files, [name]: content } })),
  setFiles: (files) => set({ files }),
  setActiveFile: (activeFile) => set({ activeFile, follow: false }),

  ...DEFAULTS,

  hydrate: () => {
    if (!LS) return
    const theme = read(KEYS.theme, DEFAULTS.theme)

    try { document.documentElement.setAttribute('data-theme', theme) } catch { }
    set({
      theme,
      models: {
        refine: read(KEYS.refine, DEFAULTS.models.refine),
        build: read(KEYS.build, DEFAULTS.models.build),
        agent: read(KEYS.agent, DEFAULTS.models.agent),
        qa: read(KEYS.qa, DEFAULTS.models.qa),
        srs: read(KEYS.srs, DEFAULTS.models.srs),
        deploy: read(KEYS.deploy, DEFAULTS.models.deploy),
        image: read(KEYS.image, DEFAULTS.models.image),
      },
      agentMode: read(KEYS.agentMode, '1') === '1',
      think: read(KEYS.think, '0') === '1',
      images: read(KEYS.images, '0') === '1',
      hist: readJSON(KEYS.hist, []),
    })

    const srsId = read(KEYS.srsId, '')
    const srsPhase = read(KEYS.srsPhase, 'idle')
    if (srsId && RESUMABLE_SRS_PHASES.has(srsPhase)) {
      set({ srsId, srsPhase })
    }
  },

  setTheme: (theme) => {
    set({ theme })
    try {
      document.documentElement.setAttribute('data-theme', theme)
      LS?.setItem(KEYS.theme, theme)
    } catch { }
  },
  persist: (key, value) => { try { LS?.setItem(key, value) } catch { } },

      // Clear project state before opening another project.
  reset: (project) => set({
    project, logs: [], steps: {}, phases: [], files: {},
    activeFile: null, liveFile: null, liveBuf: '', follow: true,
    progress: emptyProgress(),
    tests: emptyTests(),
    question: null,
    qaReport: null,
    undo: null,
    previewRoute: '/',
    e2eLive: null,
  }),

  tests: emptyTests(),
  testStart: () => set({ tests: { ...emptyTests(), running: true, startedAt: Date.now() } }),
  testRun: (attempt) => set(s => ({ tests: { ...s.tests, attempt, running: true } })),

  stage: '',
  setStage: (stage) => set({ stage }),
  testResult: (m) => set(s => {
    const rows = [...s.tests.rows, {
      status: m.status || 'run', msg: m.msg || '', detail: m.detail || '',
      stage: s.stage, at: Date.now(),
    }]

    const pass = rows.filter(r => r.status === 'pass').length
    const fail = rows.filter(r => r.status === 'fail').length
    const warn = rows.filter(r => r.status === 'warn').length
    return { tests: { ...s.tests, rows, pass, fail, warn } }
  }),
  testFixing: (m) => set(s => ({
    tests: {
      ...s.tests,
      fixing: [...s.tests.fixing,
               { attempt: m.attempt, errors: m.errors || [], at: Date.now() }],
    },
  })),
  testDone: () => set(s => ({ tests: { ...s.tests, running: false } })),

  qaReport: null,
  setQaReport: (qaReport) => set({ qaReport }),

  undo: null,
  setUndo: (undo) => set({ undo }),

  // A question the run stopped on, waiting for an answer.
  question: null,
}))

function emptyTests() {
  return { running: false, attempt: 0, rows: [], fixing: [],
           pass: 0, fail: 0, warn: 0, startedAt: 0 }
}

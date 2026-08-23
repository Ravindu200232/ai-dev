'use strict'

const { app, BrowserWindow, ipcMain, shell } = require('electron')
const { spawn } = require('node:child_process')
const path = require('node:path')
const fs = require('node:fs')

const { pythonCommand, goCommand, portOpen, reclaimPort, run } = require('./runtime')

const STUDIO_PORT = 3000
// The backend's own listeners.
const BACKEND_PORTS = [7824, 7825, 7834]
const STUDIO_URL = `http://localhost:${STUDIO_PORT}/__agentforge`

let shellWindow = null
const children = []

/** Backend and Studio locations. */
async function roots() {
  const base = path.join(__dirname, '..')
  return { backend: path.join(base, 'backend'), studio: path.join(base, 'studio') }
}

function waitForPort(port, { timeout = 240000, every = 700 } = {}) {
  const deadline = Date.now() + timeout
  return new Promise((resolve) => {
    const tick = async () => {
      if (await portOpen(port)) return resolve(true)
      if (Date.now() > deadline) return resolve(false)
      setTimeout(tick, every)
    }
    tick()
  })
}

function track(child) {
  if (child) children.push(child)
  return child
}

/** Stop every process started by the app. */
function stopChildren() {
  while (children.length) {
    const c = children.pop()
    try {
      if (process.platform === 'win32') {
        // The npm and py launchers spawn their own children.
        spawn('taskkill', ['/pid', String(c.pid), '/f', '/t'], { windowsHide: true })
      } else {
        process.kill(-c.pid, 'SIGTERM')
      }
    } catch { /* already gone */ }
  }
}

const send = (line) => {
  if (shellWindow && !shellWindow.isDestroyed()) {
    shellWindow.webContents.send('app:log', String(line))
  }
}

const step = (text, pct) => {
  if (shellWindow && !shellWindow.isDestroyed()) {
    shellWindow.webContents.send('app:step', { text, pct })
  }
}

/** Where the compiled backend lives, and how to build it if it is not there. */
function backendBinary(backend) {
  const name = process.platform === 'win32' ? 'agentforge.exe' : 'agentforge'
  return path.join(backend, 'agent', 'bin', name)
}

async function buildBackend(backend) {
  const go = await goCommand()
  if (!go) {
    throw new Error('Go could not be found. Install Go 1.24 or newer, then start AgentForge again.')
  }
  step('Building the AgentForge backend — first run only…', 16)
  const agentDir = path.join(backend, 'agent')
  const out = path.join('bin', process.platform === 'win32' ? 'agentforge.exe' : 'agentforge')
  const built = await run(go, ['build', '-o', out, './cmd/agentforge'], {
    timeout: 600000,
    env: { GOFLAGS: '-mod=mod' },
    cwd: agentDir,
  })
  if (!built.ok) throw new Error(`The backend did not build:\n${built.out}`)
}

async function startBackend() {
  const { backend } = await roots()

  const binary = backendBinary(backend)
  if (!fs.existsSync(binary)) await buildBackend(backend)

  // The SRS and deployment agents are still Python, and the backend spawns
  // them, so it needs to be told which interpreter works here.
  const py = await pythonCommand()
  if (!py) throw new Error('Python could not be found on this machine.')

  step('Starting the AgentForge backend…', 20)
  const child = track(spawn(binary, [], {
    cwd: backend,
    env: {
      ...process.env,
      AGENTFORGE_BASE: backend,
      AGENTFORGE_PYTHON: [py.cmd, ...py.prefix].join(' ').trim(),
      AGENTFORGE_NODE: process.env.AGENTFORGE_NODE || '',
      AGENTFORGE_NPM: process.env.AGENTFORGE_NPM || '',
      PYTHONUNBUFFERED: '1',
    },
    windowsHide: true,
    detached: process.platform !== 'win32',
  }))
  child.stdout?.on('data', d => send(String(d).trimEnd()))
  child.stderr?.on('data', d => send(String(d).trimEnd()))
  return child
}

async function startStudio() {
  const { studio } = await roots()

    // npm writes `next.cmd` only on Windows.
  const shim = process.platform === 'win32' ? 'next.cmd' : 'next'
  if (!fs.existsSync(path.join(studio, 'node_modules', '.bin', shim))) {
    step('Preparing the Studio — first run only, a few minutes…', 35)
    const install = track(spawn('npm', ['install', '--no-audit', '--no-fund'], {
      cwd: studio, shell: true, windowsHide: true,
    }))
    install.stdout?.on('data', d => send(String(d).trimEnd()))
    install.stderr?.on('data', d => send(String(d).trimEnd()))
    const ok = await new Promise(r => install.on('close', c => r(c === 0)))
    if (!ok) throw new Error('The Studio dependencies did not install.')
  }

  step('Starting the Studio…', 55)
  const child = track(spawn('npm', ['run', 'dev'], {
    cwd: studio, shell: true, windowsHide: true,
    detached: process.platform !== 'win32',
  }))
  child.stdout?.on('data', d => send(String(d).trimEnd()))
  child.stderr?.on('data', d => send(String(d).trimEnd()))
  return child
}

function createWindow() {
  shellWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1080,
    minHeight: 680,
    backgroundColor: '#eef3fb',
    show: true,
    autoHideMenuBar: true,
    title: 'AgentForge',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  // Anything that is not the studio opens in the real browser.
  shellWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })
  shellWindow.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith(`http://localhost:${STUDIO_PORT}`)) {
      e.preventDefault()
      shell.openExternal(url)
    }
  })
  shellWindow.on('closed', () => { shellWindow = null })
  return shellWindow
}

/** Click the icon, get the Studio. */
/** Take back the ports a previous run left held. */
async function reclaimPorts(mine) {
  const stale = []
  for (const port of [STUDIO_PORT, ...BACKEND_PORTS]) {
    const what = await reclaimPort(port, mine)
    if (what === 'reclaimed') stale.push(port)
    if (what === 'foreign' && port === STUDIO_PORT) {
      return { ok: false, note: 'Another program is already using port 3000. Close it and start AgentForge again.' }
    }
  }
  if (stale.length) {
    send(`Stopped ${stale.length} leftover server(s) from a previous run: ${stale.join(', ')}.`)
  }
  return { ok: true }
}

async function launch() {
  try {
    const { studio } = await roots()

    step('Checking for leftovers from the last run…', 12)
    const clear = await reclaimPorts(path.join(__dirname, '..'))
    if (!clear.ok) return clear

    await startBackend()
    await startStudio()
    step('Waiting for the Studio to answer…', 75)
    const up = await waitForPort(STUDIO_PORT)
    if (!up) return { ok: false, note: 'The Studio did not answer on port 3000.' }

    step('Opening…', 100)
    await shellWindow.loadURL(STUDIO_URL)
    return { ok: true }
  } catch (e) {
    return { ok: false, note: e.message }
  }
}

function boot() {
  createWindow()
  shellWindow.loadFile(path.join(__dirname, 'splash.html'))
}

ipcMain.handle('app:launch', launch)

app.whenReady().then(boot)

/** Nothing this app started outlives the window. */
async function shutDown() {
  stopChildren()
  try {
    const mine = path.join(__dirname, '..')
    for (const port of [STUDIO_PORT, ...BACKEND_PORTS]) {
      await reclaimPort(port, mine)
    }
  } catch { /* Shutdown errors have nowhere useful to go. */ }
}

app.on('window-all-closed', async () => {
  await shutDown()
  if (process.platform !== 'darwin') app.quit()
})
app.on('before-quit', stopChildren)
process.on('exit', stopChildren)

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) boot()
})

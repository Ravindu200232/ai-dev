'use strict'

const { app, BrowserWindow, ipcMain, shell } = require('electron')
const { spawn } = require('node:child_process')
const path = require('node:path')
const fs = require('node:fs')

const { pythonCommand, portOpen, reclaimPort } = require('./runtime')

const STUDIO_PORT = 3000
// The backend's own listeners. Named here because a run that was killed rather
// than closed leaves every one of them held, and the next launch has to be
// able to take them back.
const BACKEND_PORTS = [7824, 7825, 7826, 7834]
const STUDIO_URL = `http://localhost:${STUDIO_PORT}/__agentforge`

let shellWindow = null
const children = []

/**
 * Where the backend and studio are.
 *
 * The folders beside this one, used in place. There is no packaged copy and no
 * mirror: this app is started from the same checkout it runs, so the code it
 * launches is the code on disk, and editing a file is all it takes to change
 * what the next start does.
 *
 * It used to copy both trees into %LOCALAPPDATA% because an installed copy
 * under Program Files is read-only and the backend writes `logs/` while the
 * studio writes `node_modules/`. Nothing is installed any more, so the copy —
 * and the whole class of bug where the app ran a stale mirror of itself — is
 * gone with it.
 */
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

/** Stop everything this app started. Called on quit, and safe to call twice. */
function stopChildren() {
  while (children.length) {
    const c = children.pop()
    try {
      if (process.platform === 'win32') {
        // The npm and py launchers spawn their own children; killing the
        // wrapper alone leaves node and python holding the ports, and the next
        // launch then fails to bind with nothing visible to blame.
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

async function startBackend() {
  const { backend } = await roots()
  const py = await pythonCommand()
  if (!py) throw new Error('Python could not be found on this machine.')

  step('Starting the AgentForge backend…', 20)
  const child = track(spawn(py.cmd, [...py.prefix, 'server.py'], {
    cwd: backend,
    env: {
      ...process.env,
      // The backend already reads these — see builder_common — so a machine
      // with an unusual PATH still finds the same node the shell used.
      AGENTFORGE_NODE: process.env.AGENTFORGE_NODE || '',
      AGENTFORGE_NPM: process.env.AGENTFORGE_NPM || '',
      PYTHONUNBUFFERED: '1',
    },
    shell: true,
    windowsHide: true,
    detached: process.platform !== 'win32',
  }))
  child.stdout?.on('data', d => send(String(d).trimEnd()))
  child.stderr?.on('data', d => send(String(d).trimEnd()))
  return child
}

async function startStudio() {
  const { studio } = await roots()

  // npm writes `next.cmd` on Windows and an extensionless `next` elsewhere.
  // Testing for the shim rather than for node_modules on purpose: a partly
  // extracted install leaves the folder there with an empty .bin, and
  // `npm run dev` then dies with "'next' is not recognized".
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

  // Anything that is not the studio opens in the real browser: the deployed
  // site, a GitHub run, a docs link. The shell stays the studio.
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

/**
 * Click the icon, get the Studio.
 *
 * The window shows a splash while this runs because a cold start is close to a
 * minute, not because anything is being asked. Whatever the machine needed was
 * installed by the installer; if something is missing anyway the splash names
 * it rather than opening onto a dead page.
 */
/**
 * Take back the ports a previous run left held.
 *
 * The app used to adopt whatever was already answering on 3000, which read as
 * good manners and was the reason an upgrade changed nothing: the survivor of
 * the last run was still serving the previous version's code. Closing the app
 * should stop its servers and opening it should start them, so a leftover is
 * something to clear rather than to inherit.
 *
 * Only processes started from this app's own directory are touched. Somebody
 * else's dev server on 3000 is not ours to kill; it is reported instead, and
 * the launch fails with a reason rather than silently showing their site.
 */
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

/**
 * Nothing this app started outlives the window.
 *
 * stopChildren kills the processes it spawned, which is enough when they are
 * still the ones holding the ports. A run that crashed, or a launcher whose
 * child outlived it, leaves a listener with no parent left to kill — so the
 * ports are swept as well, and only for processes started from our own tree.
 */
async function shutDown() {
  stopChildren()
  try {
    const mine = path.join(__dirname, '..')
    for (const port of [STUDIO_PORT, ...BACKEND_PORTS]) {
      await reclaimPort(port, mine)
    }
  } catch { /* on the way out; nothing left to report to */ }
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

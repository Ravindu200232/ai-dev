'use strict'

const { spawn } = require('node:child_process')
const net = require('node:net')

/** Runtime discovery and process helpers. */

/** Run a command without throwing for a missing binary. */
function run(cmd, args, { timeout = 15000, env, cwd } = {}) {
  return new Promise((resolve) => {
    let done = false
    let out = ''
    let child
    try {
      child = spawn(cmd, args, {
        shell: process.platform === 'win32',
        env: { ...process.env, ...(env || {}) },
        cwd,
        windowsHide: true,
      })
    } catch {
      return resolve({ ok: false, out: '' })
    }
    const finish = (ok) => { if (!done) { done = true; resolve({ ok, out: out.trim() }) } }
    const timer = setTimeout(() => { try { child.kill() } catch {} ; finish(false) }, timeout)
    child.stdout?.on('data', d => { out += d })
    child.stderr?.on('data', d => { out += d })
    child.on('error', () => { clearTimeout(timer); finish(false) })
    child.on('close', code => { clearTimeout(timer); finish(code === 0) })
  })
}

/** Check whether a port already has a listener. */
function portOpen(port, host = '127.0.0.1', timeout = 1200) {
  return new Promise((resolve) => {
    const s = new net.Socket()
    const done = (v) => { try { s.destroy() } catch {} ; resolve(v) }
    s.setTimeout(timeout)
    s.once('connect', () => done(true))
    s.once('timeout', () => done(false))
    s.once('error', () => done(false))
    s.connect(port, host)
  })
}

/** The go toolchain, or null. Only needed to build the backend the first time. */
async function goCommand() {
  const configured = String(process.env.AGENTFORGE_GO || '').trim()
  const candidates = configured ? [configured] : ['go']
  if (process.platform !== 'win32') candidates.push('/usr/local/go/bin/go')

  for (const cmd of candidates) {
    const r = await run(cmd, ['version'], { timeout: 8000 })
    if (r.ok && /go\d+\./.test(r.out.replace(/\s+/g, ''))) return cmd
  }
  return null
}

/** The pid listening on a port, or 0. */
async function listenerPid(port) {
  if (process.platform === 'win32') {
    const r = await run('netstat', ['-ano', '-p', 'TCP'], { timeout: 8000 })
    for (const line of r.out.split(/\r?\n/)) {
      const m = line.match(/^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)/)
      if (m && Number(m[1]) === port) return Number(m[2])
    }
    return 0
  }
  const r = await run('lsof', ['-ti', `tcp:${port}`, '-sTCP:LISTEN'], { timeout: 8000 })
  return Number(String(r.out).split(/\s+/)[0] || 0) || 0
}

/** Read the command that started a process. */
async function commandOf(pid) {
  if (!pid) return ''
  if (process.platform === 'win32') {
    const r = await run('powershell', ['-NoProfile', '-Command',
      `(Get-CimInstance Win32_Process -Filter "ProcessId=${pid}").CommandLine`],
      { timeout: 12000 })
    return r.out || ''
  }
  const r = await run('ps', ['-p', String(pid), '-o', 'command='], { timeout: 8000 })
  return r.out || ''
}

function killTree(pid) {
  if (!pid) return Promise.resolve()
  if (process.platform === 'win32') {
    return run('taskkill', ['/F', '/T', '/PID', String(pid)], { timeout: 15000 })
  }
  try { process.kill(-pid, 'SIGKILL') } catch { try { process.kill(pid, 'SIGKILL') } catch {} }
  return Promise.resolve()
}

/** Take back a port this app left occupied. */
async function reclaimPort(port, marker) {
  const pid = await listenerPid(port)
  if (!pid) return 'free'
  const cmd = (await commandOf(pid)).replace(/\\/g, '/').toLowerCase()
  if (!cmd.includes(String(marker).replace(/\\/g, '/').toLowerCase())) return 'foreign'
  await killTree(pid)
  return 'reclaimed'
}

module.exports = {
  run, portOpen, goCommand,
  listenerPid, commandOf, killTree, reclaimPort,
}

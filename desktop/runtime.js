'use strict'

const { spawn } = require('node:child_process')
const net = require('node:net')

/**
 * The two questions the shell has to answer before it can start anything:
 * which python works here, and is something already listening.
 *
 * Checking for the rest of the toolchain is the installer's job now, so none
 * of it lives here. What remains is what changes between one launch and the
 * next — a port that is free this time and taken the next, a python that moved.
 */

/** Run a command and return {ok, out}. Never throws — a missing binary is an answer. */
function run(cmd, args, { timeout = 15000, env } = {}) {
  return new Promise((resolve) => {
    let done = false
    let out = ''
    let child
    try {
      child = spawn(cmd, args, {
        shell: process.platform === 'win32',
        env: { ...process.env, ...(env || {}) },
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

/** Is something listening? A running studio is adopted rather than fought with. */
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

/**
 * The python command that actually works here, or null.
 *
 * `py -3` is tried first because the launcher is what a Windows install
 * registers, and a bare `python` can be the Microsoft Store stub that exits
 * without ever running anything.
 */
async function pythonCommand() {
  const configured = String(process.env.AGENTFORGE_PYTHON || '').trim()
  if (configured) {
    const r = await run(configured, ['--version'], { timeout: 8000 })
    if (r.ok && /python\s+3\./i.test(r.out)) return { cmd: configured, prefix: [] }
  }

  for (const [cmd, args] of [['py', ['-3']], ['python', []], ['python3', []]]) {
    const r = await run(cmd, [...args, '--version'], { timeout: 8000 })
    if (r.ok && /python\s+3\./i.test(r.out)) return { cmd, prefix: args }
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

/** How a process was started, so it can be identified before it is killed. */
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

/**
 * Take back a port this app left occupied.
 *
 * A previous run that was killed rather than closed leaves its backend and its
 * Next server holding these ports. Adopting whatever answers looked like the
 * polite thing to do and was not: the survivor is serving the code that
 * shipped with the *previous* version, so an upgrade appeared to change
 * nothing at all. Reclaiming means the app that is running is always the app
 * that was installed.
 *
 * `marker` is a path that only this app's processes have on their command
 * line, so a dev server somebody else is running on 3000 is left alone — it is
 * not ours to kill, and the caller falls back to reporting the clash.
 *
 * @returns {Promise<'free'|'reclaimed'|'foreign'>}
 */
async function reclaimPort(port, marker) {
  const pid = await listenerPid(port)
  if (!pid) return 'free'
  const cmd = (await commandOf(pid)).replace(/\\/g, '/').toLowerCase()
  if (!cmd.includes(String(marker).replace(/\\/g, '/').toLowerCase())) return 'foreign'
  await killTree(pid)
  return 'reclaimed'
}

module.exports = {
  run, portOpen, pythonCommand,
  listenerPid, commandOf, killTree, reclaimPort,
}

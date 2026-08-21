'use strict'

const { contextBridge, ipcRenderer } = require('electron')

/**
 * The splash page's only way into the main process.
 *
 * Three verbs, and deliberately no fourth. The window that runs this preload
 * navigates to the Studio a moment later, and the Studio is ordinary web
 * content served over localhost — it must not inherit a bridge that can start
 * processes beyond the one it is already running inside.
 */
contextBridge.exposeInMainWorld('agentforge', {
  launch: () => ipcRenderer.invoke('app:launch'),
  onLog: (fn) => {
    const handler = (_e, line) => fn(line)
    ipcRenderer.on('app:log', handler)
    return () => ipcRenderer.removeListener('app:log', handler)
  },
  onStep: (fn) => {
    const handler = (_e, payload) => fn(payload)
    ipcRenderer.on('app:step', handler)
    return () => ipcRenderer.removeListener('app:step', handler)
  },
})

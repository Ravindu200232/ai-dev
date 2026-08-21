'use strict'

const { contextBridge, ipcRenderer } = require('electron')

/** The splash page's only way into the main process. */
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

// Cairn — Electron preload
//
// Bridges the sandboxed renderer to the main process. Exposes exactly the
// surface the UI needs; never exposes the raw Resolve interface or IPC.
//
// This file is the trust boundary — extend it by adding explicit functions
// here and handlers in main.js. Do not expose ipcRenderer directly.

'use strict';

const { contextBridge, ipcRenderer } = require('electron/renderer');

contextBridge.exposeInMainWorld('cairn', {
    /**
     * Proof-of-life snapshot of Resolve state (project/timeline names).
     * Safe to call before Resolve is open — returns `{ available: false, ... }`.
     */
    getResolveSnapshot: () => ipcRenderer.invoke('cairn:resolve:snapshot'),

    /**
     * Lightweight ping — confirms the IPC channel is alive and exposes
     * Electron/Node/Chrome versions for the Settings "About" panel.
     */
    ping: () => ipcRenderer.invoke('cairn:ping'),
});

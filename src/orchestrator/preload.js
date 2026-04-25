// Cairn — Electron preload
//
// Bridges the sandboxed renderer to the main process. Exposes exactly the
// surface the UI needs; never exposes the raw Resolve interface or IPC.
//
// This file is the trust boundary — extend it by adding explicit functions
// here and handlers in main.js. Do not expose ipcRenderer directly.

"use strict";

const { contextBridge, ipcRenderer } = require("electron/renderer");

contextBridge.exposeInMainWorld("cairn", {
  /**
   * Proof-of-life snapshot of Resolve state (project/timeline names).
   * Safe to call before Resolve is open — returns `{ available: false, ... }`.
   */
  getResolveSnapshot: () => ipcRenderer.invoke("cairn:resolve:snapshot"),

  /**
   * Lightweight ping — confirms the IPC channel is alive and exposes
   * Electron/Node/Chrome versions for the Settings "About" panel.
   */
  ping: () => ipcRenderer.invoke("cairn:ping"),

  /**
   * Echo — sends a string to the Python worker and returns it with a
   * timestamp. Temporary proof-of-life for the Node↔Python bridge.
   * @param {string} payload
   */
  echo: (payload) => ipcRenderer.invoke("cairn:echo", payload),

  ingest: {
    /**
     * Run the ingest pipeline for one shoot folder.
     * @param {{ path: string, name?: string, dry_run?: boolean, write_sidecar?: boolean, resolve?: boolean }} payload
     */
    run: (payload) => ipcRenderer.invoke("cairn:ingest", payload),
    /**
     * Subscribe to ingest progress events. Returns an unsubscribe function.
     * @param {(event: object) => void} callback
     * @returns {() => void}
     */
    onProgress: (callback) => {
      const listener = (_event, data) => callback(data);
      ipcRenderer.on("cairn:ingest:progress", listener);
      return () => ipcRenderer.removeListener("cairn:ingest:progress", listener);
    },
  },

  keys: {
    /** Retrieve stored API keys from the OS keychain. */
    get: () => ipcRenderer.invoke("cairn:keys:get"),
    /**
     * Persist API keys to the OS keychain.
     * @param {{ anthropic?: string, assemblyai?: string }} payload
     */
    set: (payload) => ipcRenderer.invoke("cairn:keys:set", payload),
    /**
     * Validate a key against its API. Returns { valid, error? }.
     * @param {{ type: "anthropic"|"assemblyai", key: string }} params
     */
    validate: (params) => ipcRenderer.invoke("cairn:keys:validate", params),
  },
});

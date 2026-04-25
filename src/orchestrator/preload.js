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
   * Open a native folder picker. Returns the chosen absolute path, or null if cancelled.
   */
  openFolder: () => ipcRenderer.invoke("cairn:dialog:open-folder"),

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

  classify: {
    /**
     * Run VAD classification on all video clips in a shoot folder.
     * @param {{ path: string, dry_run?: boolean }} payload
     */
    run: (payload) => ipcRenderer.invoke("cairn:classify", payload),
    /**
     * Subscribe to classify progress events. Returns an unsubscribe function.
     * @param {(event: object) => void} callback
     * @returns {() => void}
     */
    onProgress: (callback) => {
      const listener = (_event, data) => callback(data);
      ipcRenderer.on("cairn:classify:progress", listener);
      return () => ipcRenderer.removeListener("cairn:classify:progress", listener);
    },
    /**
     * Extract a thumbnail frame from a clip at ~3 s. Returns a data: URL or null.
     * @param {string} clipPath
     * @returns {Promise<{dataUrl: string|null}>}
     */
    thumbnail: (clipPath) => ipcRenderer.invoke("cairn:classify:thumbnail", { clipPath }),
    /**
     * Confirm a user-overridden classification for an AMBIGUOUS clip.
     * Updates sidecar and Resolve.
     * @param {{ clip_path: string, label: string, shoot_folder: string }} payload
     */
    confirm: (payload) => ipcRenderer.invoke("cairn:classify:confirm", payload),
  },

  transcribe: {
    /**
     * Get clip count + estimated audio minutes for cost preview.
     * @param {{ path: string }} payload
     */
    estimate: (payload) => ipcRenderer.invoke("cairn:transcribe:estimate", payload),
    /**
     * Run transcription pipeline on all A-ROLL clips in the shoot folder.
     * @param {{ path: string, cloud_allowed?: boolean, dry_run?: boolean }} payload
     */
    run: (payload) => ipcRenderer.invoke("cairn:transcribe", payload),
    /**
     * Subscribe to transcribe progress events. Returns an unsubscribe function.
     * @param {(event: object) => void} callback
     * @returns {() => void}
     */
    onProgress: (callback) => {
      const listener = (_event, data) => callback(data);
      ipcRenderer.on("cairn:transcribe:progress", listener);
      return () => ipcRenderer.removeListener("cairn:transcribe:progress", listener);
    },
    /**
     * Save user-assigned speaker names to all transcribed clip sidecars.
     * @param {{ shoot_folder: string, speaker_labels: Record<string, string> }} payload
     */
    labelSpeakers: (payload) => ipcRenderer.invoke("cairn:transcribe:label-speakers", payload),
  },

  brief: {
    /** Read the current brief.json for a shoot folder (null if none). */
    get: (payload) => ipcRenderer.invoke("cairn:brief:get", payload),
    /**
     * Send a user chat message; returns { reply, draft, finalised, fields, judge }.
     * @param {{ path: string, message: string }} payload
     */
    message: (payload) => ipcRenderer.invoke("cairn:brief:message", payload),
    /**
     * Save edited brief fields from the UI form.
     * @param {{ path: string, updates: object }} payload
     */
    save: (payload) => ipcRenderer.invoke("cairn:brief:save", payload),
    /** Delete brief.json and start over. */
    reset: (payload) => ipcRenderer.invoke("cairn:brief:reset", payload),
  },

  vision: {
    /**
     * Get B-ROLL clip count + estimated Claude vision cost for a shoot folder.
     * @param {{ path: string }} payload
     */
    estimate: (payload) => ipcRenderer.invoke("cairn:vision:estimate", payload),
    /**
     * Run Claude vision tagging on all B-ROLL clips in the shoot folder.
     * @param {{ path: string, cloud_allowed?: boolean, dry_run?: boolean }} payload
     */
    run: (payload) => ipcRenderer.invoke("cairn:vision", payload),
    /**
     * Subscribe to vision progress events. Returns an unsubscribe function.
     * @param {(event: object) => void} callback
     * @returns {() => void}
     */
    onProgress: (callback) => {
      const listener = (_event, data) => callback(data);
      ipcRenderer.on("cairn:vision:progress", listener);
      return () => ipcRenderer.removeListener("cairn:vision:progress", listener);
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

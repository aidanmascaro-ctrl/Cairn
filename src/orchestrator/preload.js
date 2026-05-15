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

  timeline: {
    /**
     * Build the assembly timeline (Stage 4) — A-roll on V1/A1, B-roll on V2/A2.
     * Skips AMBIGUOUS clips (the user must confirm them via the Classify panel first).
     * @param {{ path: string, name?: string }} payload
     * @returns {Promise<{ok: boolean, name: string|null, fps: number|null, resolution: string|null, a_count: number, b_count: number, ambiguous_count: number, missing_paths: string[], warnings: string[], error: string|null}>}
     */
    build: (payload) => ipcRenderer.invoke("cairn:timeline:build", payload),
  },

  classify: {
    /**
     * Run classification on all video clips in a shoot folder.
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
    /**
     * Re-analyse a clip and return annotated frame images for the visual inspector.
     * Returns base64 JPEG data URLs with face boxes and motion bar overlaid.
     * @param {{ clip_path: string }} payload
     */
    inspect: (payload) => ipcRenderer.invoke("cairn:classify:inspect", payload),
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

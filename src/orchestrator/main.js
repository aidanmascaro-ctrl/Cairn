// Cairn — Electron main process
//
// Runs inside DaVinci Resolve's Workflow Integration host. Follows the
// sandboxed model from Blackmagic's SamplePromisePlugin (Developer/
// Workflow Integrations/Examples/SamplePromisePlugin): contextIsolation
// enabled, no nodeIntegration in the renderer, all Resolve operations
// brokered through preload + ipcMain.
//
// The renderer (built by Vite from src/ui) loads dist/index.html and
// calls window.cairn.<op>() which resolves through this process.

"use strict";

const { app, BrowserWindow, ipcMain } = require("electron");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { createInterface } = require("node:readline");
const { randomUUID } = require("node:crypto");
const https = require("node:https");
const keytar = require("keytar");

// ── Inline Python bridge (lightweight — for IPC handlers only) ───────────────
// The full typed PythonBridge class lives in src/orchestrator/python-bridge.ts
// and is used by the Node orchestration layer. Here we keep a single shared
// worker process for IPC calls from the renderer.

/** @type {import('node:child_process').ChildProcess|null} */
let pythonWorker = null;
/** @type {Map<string, {resolve: Function, reject: Function, timer: any}>} */
const pythonPending = new Map();

function getPythonPath() {
  // Use env override or fall back to python3 on PATH.
  return process.env["CAIRN_PYTHON"] ?? "python3";
}

function getBridgeScriptPath() {
  // main.js is at <plugin>/src/orchestrator/main.js; bridge.py is at <plugin>/src/workers/bridge.py
  return path.join(__dirname, "..", "workers", "bridge.py");
}

function ensurePythonWorker() {
  if (pythonWorker && pythonWorker.exitCode === null) return;

  pythonWorker = spawn(getPythonPath(), [getBridgeScriptPath()], {
    stdio: ["pipe", "pipe", "pipe"],
  });

  const rl = createInterface({ input: pythonWorker.stdout });
  rl.on("line", (line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    let msg;
    try {
      msg = JSON.parse(trimmed);
    } catch {
      return;
    }
    const pending = pythonPending.get(msg.id);
    if (!pending) return;
    clearTimeout(pending.timer);
    pythonPending.delete(msg.id);
    pending.resolve(msg);
  });

  pythonWorker.stderr.on("data", (chunk) => {
    process.stderr.write(`[bridge.py] ${chunk.toString()}`);
  });

  pythonWorker.on("exit", () => {
    pythonWorker = null;
    for (const [, p] of pythonPending) {
      clearTimeout(p.timer);
      p.reject(new Error("Python worker exited unexpectedly"));
    }
    pythonPending.clear();
  });
}

/** @param {object} msg @returns {Promise<object>} */
function callPython(msg) {
  ensurePythonWorker();
  const id = randomUUID();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pythonPending.delete(id);
      reject(new Error("Python bridge call timed out"));
    }, 10_000);
    pythonPending.set(id, { resolve, reject, timer });
    pythonWorker.stdin.write(JSON.stringify({ ...msg, id }) + "\n");
  });
}

// WorkflowIntegration.node is a native Node addon shipped by Resolve.
// In development we copy it alongside this file via packaging/install-dev-plugin.sh.
// At runtime we tolerate its absence (dev UI still renders, Resolve calls return null).
const PLUGIN_ID = "com.aidanmascaro.cairn";

/** @type {any} */
let WorkflowIntegration = null;
try {
  WorkflowIntegration = require("./WorkflowIntegration.node");
} catch (err) {
  // Expected when running `npm run start` outside Resolve. Logged, not fatal.
  console.warn(`[cairn] WorkflowIntegration.node not available: ${err.message}`);
  console.warn("[cairn] Install the plugin into Resolve to exercise the real API.");
}

/** Cached Resolve interface objects — initialised on first use. */
let initObj = null;
let resolvePromiseObj = null;

/** @returns {Promise<object|null>} */
async function initialiseResolve() {
  if (!WorkflowIntegration) return null;
  if (initObj) return initObj;

  initObj = await WorkflowIntegration.InitializePromise(PLUGIN_ID);
  if (!initObj) {
    console.error("[cairn] WorkflowIntegration.InitializePromise returned null.");
  }
  return initObj;
}

/** @returns {Promise<object|null>} */
async function getResolve() {
  if (resolvePromiseObj) return resolvePromiseObj;
  if (!WorkflowIntegration) return null;

  if (!(await initialiseResolve())) return null;

  resolvePromiseObj = WorkflowIntegration.GetResolvePromise();
  return resolvePromiseObj;
}

/**
 * Proof-of-life: returns a small snapshot of Resolve's current state.
 * @returns {Promise<{available: boolean, projectName: string|null, timelineName: string|null, error?: string}>}
 */
async function getResolveSnapshot() {
  if (!WorkflowIntegration) {
    return {
      available: false,
      projectName: null,
      timelineName: null,
      error: "WorkflowIntegration.node not loaded — install the plugin into Resolve.",
    };
  }

  try {
    const resolve = await getResolve();
    if (!resolve) {
      return {
        available: false,
        projectName: null,
        timelineName: null,
        error: "Resolve interface did not initialise.",
      };
    }

    const projectManager = await resolve.GetProjectManager();
    const project = projectManager ? await projectManager.GetCurrentProject() : null;
    const projectName = project ? await project.GetName() : null;
    const timeline = project ? await project.GetCurrentTimeline() : null;
    const timelineName = timeline ? await timeline.GetName() : null;

    return {
      available: true,
      projectName,
      timelineName,
    };
  } catch (err) {
    return {
      available: false,
      projectName: null,
      timelineName: null,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

// ── Keychain ─────────────────────────────────────────────────────────────────

const KEYCHAIN_SERVICE = "com.aidanmascaro.cairn";

/**
 * Fire a HEAD/GET request and return the HTTP status code.
 * @param {https.RequestOptions & { timeout?: number }} options
 * @returns {Promise<{statusCode: number, error?: string}>}
 */
function httpsGet(options) {
  return new Promise((resolve) => {
    const req = https.request({ method: "GET", ...options }, (res) => {
      resolve({ statusCode: res.statusCode ?? 0 });
      res.resume();
    });
    req.on("error", (err) => resolve({ statusCode: 0, error: err.message }));
    req.on("timeout", () => {
      req.destroy();
      resolve({ statusCode: 0, error: "request timed out" });
    });
    req.end();
  });
}

/**
 * @param {"anthropic"|"assemblyai"} type
 * @param {string} key
 * @returns {Promise<{valid: boolean, error?: string}>}
 */
async function validateKey(type, key) {
  if (!key) return { valid: false, error: "No key provided" };

  if (type === "anthropic") {
    const { statusCode, error } = await httpsGet({
      hostname: "api.anthropic.com",
      path: "/v1/models",
      headers: { "x-api-key": key, "anthropic-version": "2023-06-01" },
      timeout: 8_000,
    });
    if (error) return { valid: false, error };
    if (statusCode === 200) return { valid: true };
    if (statusCode === 401) return { valid: false, error: "Invalid API key" };
    return { valid: false, error: `Unexpected status ${statusCode}` };
  }

  if (type === "assemblyai") {
    const { statusCode, error } = await httpsGet({
      hostname: "api.assemblyai.com",
      path: "/v2/account",
      headers: { Authorization: key },
      timeout: 8_000,
    });
    if (error) return { valid: false, error };
    if (statusCode === 200) return { valid: true };
    if (statusCode === 401) return { valid: false, error: "Invalid API key" };
    return { valid: false, error: `Unexpected status ${statusCode}` };
  }

  return { valid: false, error: "Unknown key type" };
}

function registerIpc() {
  ipcMain.handle("cairn:resolve:snapshot", async () => getResolveSnapshot());
  ipcMain.handle("cairn:ping", async () => ({
    ok: true,
    pluginId: PLUGIN_ID,
    electron: process.versions.electron,
    node: process.versions.node,
    chrome: process.versions.chrome,
  }));
  ipcMain.handle("cairn:echo", async (_event, payload) => {
    return callPython({ type: "echo", payload: String(payload) });
  });

  ipcMain.handle("cairn:keys:get", async () => {
    const [anthropic, assemblyai] = await Promise.all([
      keytar.getPassword(KEYCHAIN_SERVICE, "ANTHROPIC_API_KEY"),
      keytar.getPassword(KEYCHAIN_SERVICE, "ASSEMBLYAI_API_KEY"),
    ]);
    return { anthropic: anthropic ?? "", assemblyai: assemblyai ?? "" };
  });

  ipcMain.handle("cairn:keys:set", async (_event, payload) => {
    const tasks = [];
    if (typeof payload.anthropic === "string" && payload.anthropic) {
      tasks.push(keytar.setPassword(KEYCHAIN_SERVICE, "ANTHROPIC_API_KEY", payload.anthropic));
    }
    if (typeof payload.assemblyai === "string" && payload.assemblyai) {
      tasks.push(keytar.setPassword(KEYCHAIN_SERVICE, "ASSEMBLYAI_API_KEY", payload.assemblyai));
    }
    await Promise.all(tasks);
    return { ok: true };
  });

  ipcMain.handle("cairn:keys:validate", async (_event, { type, key }) => {
    return validateKey(String(type), String(key));
  });
}

function createMainWindow() {
  const win = new BrowserWindow({
    width: 1040,
    height: 720,
    useContentSize: true,
    autoHideMenuBar: true,
    title: "Cairn",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  // Load the Vite-built renderer from ../../dist/index.html
  // (main.js lives at src/orchestrator/main.js; dist is at the plugin root)
  const indexHtml = path.join(__dirname, "..", "..", "dist", "index.html");
  win.loadFile(indexHtml).catch((err) => {
    console.error(`[cairn] Failed to load ${indexHtml}: ${err.message}`);
  });

  // Open DevTools only when CAIRN_DEVTOOLS=1 in the environment.
  if (process.env.CAIRN_DEVTOOLS === "1") {
    win.webContents.openDevTools({ mode: "detach" });
  }

  win.on("close", () => {
    if (WorkflowIntegration) {
      try {
        WorkflowIntegration.CleanUp();
      } catch {
        /* noop */
      }
    }
  });
}

app.whenReady().then(() => {
  registerIpc();
  createMainWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
  });
});

app.on("window-all-closed", () => {
  if (WorkflowIntegration) {
    try {
      WorkflowIntegration.CleanUp();
    } catch {
      /* noop */
    }
  }
  if (process.platform !== "darwin") app.quit();
});

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

const { app, BrowserWindow, ipcMain, safeStorage } = require("electron");
const path = require("node:path");
const fs = require("node:fs");
const { spawn } = require("node:child_process");
const { createInterface } = require("node:readline");
const { randomUUID } = require("node:crypto");
const https = require("node:https");

// ── Inline Python bridge (lightweight — for IPC handlers only) ───────────────
// The full typed PythonBridge class lives in src/orchestrator/python-bridge.ts
// and is used by the Node orchestration layer. Here we keep a single shared
// worker process for IPC calls from the renderer.

/** @type {import('node:child_process').ChildProcess|null} */
let pythonWorker = null;
/** @type {Map<string, {resolve: Function, reject: Function, timer: any}>} */
const pythonPending = new Map();

function getPythonPath() {
  if (process.env["CAIRN_PYTHON"]) return process.env["CAIRN_PYTHON"];
  // Resolve strips PATH — check common macOS locations before falling back.
  const candidates = [
    "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3",
    "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
    "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
    "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3",
    "/usr/local/bin/python3",
    "/opt/homebrew/bin/python3",
    "python3",
  ];
  for (const p of candidates) {
    try {
      if (fs.existsSync(p)) return p;
    } catch {
      // ignore
    }
  }
  return "python3";
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
    // Progress events: call callback, keep promise alive
    if (msg.type === "ingest_progress") {
      pending.onProgress?.(msg);
      return;
    }
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

/**
 * @param {object} msg
 * @param {{ onProgress?: (e: object) => void, timeoutMs?: number }} [opts]
 * @returns {Promise<object>}
 */
function callPython(msg, opts = {}) {
  ensurePythonWorker();
  const id = randomUUID();
  const timeoutMs = opts.timeoutMs ?? 10_000;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pythonPending.delete(id);
      reject(new Error("Python bridge call timed out"));
    }, timeoutMs);
    pythonPending.set(id, { resolve, reject, timer, onProgress: opts.onProgress });
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

// ── Key storage (safeStorage + encrypted JSON file) ──────────────────────────
// safeStorage uses the OS keychain to derive an encryption key, then stores
// the encrypted bytes in a file we manage. No native addon required.

function keysFilePath() {
  return path.join(app.getPath("userData"), "keys.enc.json");
}

function readStoredKeys() {
  const filePath = keysFilePath();
  if (!fs.existsSync(filePath)) return {};
  try {
    const raw = JSON.parse(fs.readFileSync(filePath, "utf8"));
    const result = {};
    for (const [k, v] of Object.entries(raw)) {
      try {
        result[k] = safeStorage.decryptString(Buffer.from(String(v), "base64"));
      } catch {
        // corrupted entry — skip
      }
    }
    return result;
  } catch {
    return {};
  }
}

function writeStoredKey(name, value) {
  const filePath = keysFilePath();
  let current = {};
  if (fs.existsSync(filePath)) {
    try {
      current = JSON.parse(fs.readFileSync(filePath, "utf8"));
    } catch {
      // ignore parse error — overwrite below
    }
  }
  current[name] = safeStorage.encryptString(value).toString("base64");
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(current), "utf8");
}

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

  ipcMain.handle("cairn:keys:get", () => {
    const stored = readStoredKeys();
    return {
      anthropic: stored["ANTHROPIC_API_KEY"] ?? "",
      assemblyai: stored["ASSEMBLYAI_API_KEY"] ?? "",
    };
  });

  ipcMain.handle("cairn:keys:set", (_event, payload) => {
    if (typeof payload.anthropic === "string" && payload.anthropic) {
      writeStoredKey("ANTHROPIC_API_KEY", payload.anthropic);
    }
    if (typeof payload.assemblyai === "string" && payload.assemblyai) {
      writeStoredKey("ASSEMBLYAI_API_KEY", payload.assemblyai);
    }
    return { ok: true };
  });

  ipcMain.handle("cairn:keys:validate", async (_event, { type, key }) => {
    return validateKey(String(type), String(key));
  });

  ipcMain.handle("cairn:ingest", async (event, payload) => {
    return callPython(
      { type: "ingest", payload },
      {
        timeoutMs: 5 * 60 * 1_000, // 5 min — ingest can take time on large cards
        onProgress: (progressEvent) => {
          // Push progress events to the renderer without resolving the IPC call
          if (!event.sender.isDestroyed()) {
            event.sender.send("cairn:ingest:progress", progressEvent);
          }
        },
      },
    );
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

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

const { app, BrowserWindow, dialog, ipcMain, safeStorage } = require("electron");
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");
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

function getFfmpegPath() {
  const bundled = path.join(__dirname, "..", "..", "assets", "bin", "ffmpeg");
  if (fs.existsSync(bundled)) return bundled;
  return "ffmpeg";
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
    // Progress events (any *_progress type): call callback, keep promise alive
    if (msg.type.endsWith("_progress")) {
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

/** @type {import('electron').BrowserWindow|null} */
let mainWindow = null;

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

// ── Resolve media pool helpers ────────────────────────────────────────────────

const VIDEO_EXTS = new Set([".mp4", ".mov", ".mxf", ".avi", ".m2ts"]);
const PHOTO_EXTS = new Set([".arw", ".jpg", ".jpeg", ".dng", ".cr2", ".cr3", ".nef", ".raf"]);

/**
 * Return absolute paths of all files in dirPath whose extension is in exts.
 * @param {string} dirPath
 * @param {Set<string>} exts
 * @returns {string[]}
 */
function scanDir(dirPath, exts) {
  try {
    if (!fs.existsSync(dirPath)) return [];
    return fs
      .readdirSync(dirPath)
      .filter((name) => exts.has(path.extname(name).toLowerCase()))
      .map((name) => path.join(dirPath, name));
  } catch {
    return [];
  }
}

/**
 * Spawn resolve_import.py as a separate process with a hard kill timeout.
 * Returns the number of clips imported, or throws on failure/timeout.
 * @param {string} folder
 * @returns {Promise<number>}
 */
function importViaResolveScript(folder) {
  return new Promise((resolve, reject) => {
    const scriptPath = path.join(__dirname, "..", "workers", "resolve_import.py");
    const proc = require("node:child_process").spawn(getPythonPath(), [scriptPath, folder], {
      stdio: ["ignore", "pipe", "pipe"],
    });

    const timer = setTimeout(() => {
      proc.kill("SIGKILL");
      reject(new Error("resolve_import.py timed out (120 s) — Resolve scripting may be blocked"));
    }, 120_000);

    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (d) => {
      stdout += d.toString();
    });
    proc.stderr.on("data", (d) => {
      process.stderr.write(`[resolve_import] ${d}`);
      stderr += d.toString();
    });
    proc.on("exit", () => {
      clearTimeout(timer);
      try {
        const result = JSON.parse(stdout.trim());
        if (result.ok) {
          resolve(result.count);
        } else {
          // Include traceback if present so the UI can show the real cause
          const msg = result.error ?? "resolve_import.py returned ok:false";
          reject(new Error(result.traceback ? `${msg}\n${result.traceback}` : msg));
        }
      } catch {
        // stdout was not JSON — the script crashed before writing anything
        const detail =
          stderr.trim().split("\n").slice(-3).join(" | ") || stdout.trim() || "no output";
        reject(new Error(`resolve_import.py crash: ${detail}`));
      }
    });
  });
}

/**
 * Import the organised shoot folder into Resolve's media pool.
 *
 * Strategy 1 — Python Scripting API subprocess (preferred):
 *   Spawns resolve_import.py which calls mp.ImportMedia([folder]) via the
 *   full Python Scripting API. This supports recursive folder import with bin
 *   structure, identical to dragging the folder into the media pool manually.
 *   The subprocess is killed after 15 s if it hangs.
 *
 * Strategy 2 — JS flat import (fallback):
 *   If Python fails, import individual file paths directly via the JS
 *   WorkflowIntegration API into Master (no bin structure, but clips land).
 *
 * @param {object} ingestResult
 * @param {Electron.WebContents} sender
 * @param {string} msgId
 * @returns {Promise<{total: number}|null>}
 */
async function importFolderToResolve(ingestResult, sender, msgId) {
  const folder = ingestResult.folder;
  let total = 0;

  // ── Strategy 1: Python Scripting API (creates proper bin structure) ──────
  // Mirrors the drag-and-drop behavior: pass the folder to ImportMedia with
  // Master as the active bin so Resolve creates bins matching the filesystem.
  // Requires Resolve → Preferences → General → External scripting: Local.
  let pythonFailReason = null;
  try {
    total = await importViaResolveScript(folder);
  } catch (err) {
    pythonFailReason = err.message;
    process.stderr.write(`[cairn] Python Resolve import failed: ${err.message}\n`);
  }

  // ── Strategy 2: JS flat import (fallback — no bin structure) ─────────────
  // Only runs if Python failed. Clips land in Master without bins.
  // User must enable "External scripting: Local" in Resolve to get bins.
  if (total === 0) {
    const resolveObj = await getResolve();
    const pm = resolveObj ? await resolveObj.GetProjectManager() : null;
    const project = pm ? await pm.GetCurrentProject() : null;
    const mp = project ? await project.GetMediaPool() : null;

    if (mp) {
      const mediaPaths = [
        ...scanDir(path.join(folder, "01_VIDEOS", "A_CAM"), VIDEO_EXTS),
        ...scanDir(path.join(folder, "01_VIDEOS", "B_CAM"), VIDEO_EXTS),
        ...scanDir(path.join(folder, "01_VIDEOS", "C_CAM"), VIDEO_EXTS),
        ...scanDir(path.join(folder, "02_PHOTOS"), PHOTO_EXTS),
      ];
      if (mediaPaths.length) {
        const imported = await mp.ImportMedia(mediaPaths);
        total = imported ? imported.length : 0;
        if (total > 0 && pythonFailReason && sender && !sender.isDestroyed()) {
          // Tell the user WHY they got flat import instead of bins
          sender.send("cairn:ingest:progress", {
            id: msgId,
            type: "ingest_progress",
            event: "resolve_flat_import",
            reason: pythonFailReason,
            count: total,
          });
        }
      }
    }
  }

  if (sender && !sender.isDestroyed()) {
    sender.send("cairn:ingest:progress", {
      id: msgId,
      type: "ingest_progress",
      event: total > 0 ? "resolve_imported" : "resolve_skipped",
      ...(total > 0
        ? { total }
        : { reason: pythonFailReason ?? "No media files found to import." }),
    });
  }

  return { total };
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
  ipcMain.handle("cairn:dialog:open-folder", async () => {
    // Pass mainWindow as parent so the sheet appears attached to the plugin
    // window rather than hiding behind DaVinci Resolve.
    const result = await dialog.showOpenDialog(mainWindow ?? undefined, {
      properties: ["openDirectory"],
    });
    return result.canceled ? null : (result.filePaths[0] ?? null);
  });

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

  ipcMain.handle("cairn:classify:thumbnail", async (_event, { clipPath }) => {
    const ffmpegExe = getFfmpegPath();
    const tmpFile = path.join(os.tmpdir(), `cairn-thumb-${randomUUID()}.jpg`);
    try {
      await new Promise((resolve, reject) => {
        const proc = spawn(
          ffmpegExe,
          [
            "-y",
            "-ss",
            "3",
            "-i",
            clipPath,
            "-frames:v",
            "1",
            "-q:v",
            "5",
            "-vf",
            "scale=320:-1",
            tmpFile,
          ],
          { stdio: ["ignore", "ignore", "pipe"] },
        );
        let stderr = "";
        proc.stderr.on("data", (d) => {
          stderr += d.toString();
        });
        proc.on("exit", (code) => {
          if (code === 0) resolve(undefined);
          else reject(new Error(`ffmpeg ${String(code)}: ${stderr.slice(-120)}`));
        });
      });
      const data = fs.readFileSync(tmpFile);
      return { dataUrl: `data:image/jpeg;base64,${data.toString("base64")}` };
    } catch (err) {
      process.stderr.write(`[cairn] thumbnail failed for ${clipPath}: ${err.message}\n`);
      return { dataUrl: null };
    } finally {
      try {
        fs.unlinkSync(tmpFile);
      } catch {
        /* noop */
      }
    }
  });

  ipcMain.handle("cairn:classify:confirm", async (_event, payload) => {
    return callPython({ type: "classify_confirm", payload }, { timeoutMs: 30_000 });
  });

  ipcMain.handle("cairn:classify", async (event, payload) => {
    const sendProgress = (obj) => {
      if (!event.sender.isDestroyed()) event.sender.send("cairn:classify:progress", obj);
    };

    let result;
    try {
      result = await Promise.race([
        callPython(
          { type: "classify", payload },
          {
            // VAD on many clips can take several minutes; allow up to 10 min
            timeoutMs: 600_000,
            onProgress: (progressEvent) => sendProgress(progressEvent),
          },
        ),
        new Promise((_res, rej) =>
          setTimeout(() => rej(new Error("Classify timed out (10 min)")), 600_000),
        ),
      ]);
    } catch (err) {
      sendProgress({
        type: "classify_progress",
        event: "classify_error",
        reason: err instanceof Error ? err.message : String(err),
      });
      return { a_roll: 0, b_roll: 0, ambiguous: 0, skipped: 0, total: 0 };
    }

    return result ?? { a_roll: 0, b_roll: 0, ambiguous: 0, skipped: 0, total: 0 };
  });

  ipcMain.handle("cairn:transcribe:estimate", async (_event, payload) => {
    return callPython(
      { type: "transcribe_estimate", payload: { path: String(payload.path) } },
      { timeoutMs: 60_000 },
    );
  });

  ipcMain.handle("cairn:transcribe:label-speakers", async (_event, payload) => {
    return callPython({ type: "update_speaker_labels", payload }, { timeoutMs: 30_000 });
  });

  ipcMain.handle("cairn:transcribe", async (event, payload) => {
    const sendProgress = (obj) => {
      if (!event.sender.isDestroyed()) event.sender.send("cairn:transcribe:progress", obj);
    };

    const keys = readStoredKeys();
    const enrichedPayload = {
      ...payload,
      api_key: keys["ASSEMBLYAI_API_KEY"] ?? "",
    };

    let result;
    try {
      result = await Promise.race([
        callPython(
          { type: "transcribe", payload: enrichedPayload },
          {
            // Allow up to 30 min for large batches
            timeoutMs: 1_800_000,
            onProgress: (ev) => sendProgress(ev),
          },
        ),
        new Promise((_res, rej) =>
          setTimeout(() => rej(new Error("Transcribe timed out (30 min)")), 1_800_000),
        ),
      ]);
    } catch (err) {
      sendProgress({
        type: "transcribe_progress",
        event: "transcribe_error",
        reason: err instanceof Error ? err.message : String(err),
      });
      return { total: 0, transcribed: 0, skipped: 0, errors: 0 };
    }

    return result ?? { total: 0, transcribed: 0, skipped: 0, errors: 0 };
  });

  ipcMain.handle("cairn:vision:estimate", async (_event, payload) => {
    return callPython(
      { type: "vision_estimate", payload: { path: String(payload.path) } },
      { timeoutMs: 60_000 },
    );
  });

  ipcMain.handle("cairn:vision", async (event, payload) => {
    const sendProgress = (obj) => {
      if (!event.sender.isDestroyed()) event.sender.send("cairn:vision:progress", obj);
    };

    const keys = readStoredKeys();
    const enrichedPayload = {
      ...payload,
      api_key: keys["ANTHROPIC_API_KEY"] ?? "",
    };

    let result;
    try {
      result = await Promise.race([
        callPython(
          { type: "vision", payload: enrichedPayload },
          {
            // Allow up to 30 min for large B-roll batches
            timeoutMs: 1_800_000,
            onProgress: (ev) => sendProgress(ev),
          },
        ),
        new Promise((_res, rej) =>
          setTimeout(() => rej(new Error("Vision timed out (30 min)")), 1_800_000),
        ),
      ]);
    } catch (err) {
      sendProgress({
        type: "vision_progress",
        event: "vision_error",
        reason: err instanceof Error ? err.message : String(err),
      });
      return { total: 0, tagged: 0, skipped: 0, errors: 0, judge_samples: 0 };
    }

    return result ?? { total: 0, tagged: 0, skipped: 0, errors: 0, judge_samples: 0 };
  });

  ipcMain.handle("cairn:ingest", async (event, payload) => {
    // Python handles file organisation; Resolve import is done here via the JS
    // WorkflowIntegration API (Python Scripting API hangs from a plugin subprocess).
    const pythonPayload = { ...payload, resolve: false };

    const sendProgress = (obj) => {
      if (!event.sender.isDestroyed()) event.sender.send("cairn:ingest:progress", obj);
    };

    // ── Python organisation (45 s hard timeout) ───────────────────────────────
    // send2trash and rglob on large external drives can stall indefinitely.
    // If Python times out we skip organisation and still attempt Resolve import.
    let result = null;
    try {
      result = await Promise.race([
        callPython(
          { type: "ingest", payload: pythonPayload },
          {
            timeoutMs: 45_000,
            onProgress: (progressEvent) => sendProgress(progressEvent),
          },
        ),
        new Promise((_res, rej) =>
          setTimeout(
            () => rej(new Error("Organisation timed out (45 s) — skipping to Resolve import")),
            45_000,
          ),
        ),
      ]);
    } catch (err) {
      sendProgress({
        type: "ingest_progress",
        event: "org_skipped",
        reason: err instanceof Error ? err.message : String(err),
      });
    }

    // Build a minimal result from payload if Python didn't complete
    if (!result || result.type !== "ingest_complete") {
      result = {
        type: "ingest_complete",
        folder: payload.path,
        files_moved: 0,
        shells_removed: 0,
        metadata: {},
        clip_paths: { a_cam: [], c_cam: [], photos: [], assets: [] },
        resolve_counts: {},
        dry_run: payload.dry_run ?? false,
      };
    }

    // ── Resolve import (15 s hard timeout) ───────────────────────────────────
    if (!payload.dry_run) {
      const msgId = "";
      try {
        const resolveCounts = await Promise.race([
          importFolderToResolve(result, event.sender, msgId),
          new Promise((_res, rej) =>
            setTimeout(() => rej(new Error("Resolve import timed out (150 s)")), 150_000),
          ),
        ]);
        if (resolveCounts) {
          result.resolve_counts = resolveCounts;
        } else {
          sendProgress({
            type: "ingest_progress",
            event: "resolve_skipped",
            reason: "WorkflowIntegration not available — install the plugin into Resolve.",
          });
        }
      } catch (err) {
        sendProgress({
          type: "ingest_progress",
          event: "resolve_skipped",
          reason: err instanceof Error ? err.message : String(err),
        });
      }
    }

    return result;
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

  mainWindow = win;

  win.on("close", () => {
    mainWindow = null;
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

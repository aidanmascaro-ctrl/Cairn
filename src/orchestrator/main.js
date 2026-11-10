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
const selectsLearn = require("./selects-learn.js");

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

/**
 * Returns the path to the PyInstaller-bundled workers binary, or null if not
 * present (dev mode — fall back to getPythonPath() + getBridgeScriptPath()).
 *
 * Production layout inside the plugin folder:
 *   resources/workers          macOS
 *   resources/workers.exe      Windows
 *
 * Dev convenience: if `npm run package:python` was run, the binary lands at
 *   packaging/dist/workers[.exe] relative to the project root.
 */
function getWorkersPath() {
  const ext = process.platform === "win32" ? ".exe" : "";
  // Production: <plugin>/resources/workers[.exe]
  const prod = path.join(__dirname, "..", "..", "resources", `workers${ext}`);
  if (fs.existsSync(prod)) return prod;
  // Dev build: <project>/packaging/dist/workers[.exe]
  const dev = path.join(__dirname, "..", "..", "packaging", "dist", `workers${ext}`);
  if (fs.existsSync(dev)) return dev;
  return null;
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

  // Prefer the bundled PyInstaller binary (no Python runtime required).
  // Falls back to the Python script for dev mode.
  const workersExe = getWorkersPath();
  const cmd = workersExe ?? getPythonPath();
  const args = workersExe ? [] : [getBridgeScriptPath()];

  pythonWorker = spawn(cmd, args, {
    stdio: ["pipe", "pipe", "pipe"],
  });

  // Tail of recent stderr — included in the crash event so the UI can show the
  // user WHY the bridge died (missing module, syntax error, etc.) instead of
  // generic "buttons do nothing" behaviour.
  const STDERR_TAIL_LIMIT = 4000;
  let stderrTail = "";

  const rl = createInterface({ input: pythonWorker.stdout });
  rl.on("line", (line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    let msg;
    try {
      msg = JSON.parse(trimmed);
    } catch {
      // A reply that isn't JSON would otherwise surface only as a vague timeout.
      process.stderr.write(`[bridge.py] unparseable reply: ${trimmed.slice(0, 300)}\n`);
      return;
    }
    const pending = pythonPending.get(msg.id);
    if (!pending) return;
    if (msg.type.endsWith("_progress")) {
      pending.onProgress?.(msg);
      return;
    }
    clearTimeout(pending.timer);
    pythonPending.delete(msg.id);
    pending.resolve(msg);
  });

  pythonWorker.stderr.on("data", (chunk) => {
    const s = chunk.toString();
    process.stderr.write(`[bridge.py] ${s}`);
    stderrTail = (stderrTail + s).slice(-STDERR_TAIL_LIMIT);
  });

  pythonWorker.on("exit", (code, signal) => {
    pythonWorker = null;
    const unexpected = code !== 0 && code !== null;
    for (const [, p] of pythonPending) {
      clearTimeout(p.timer);
      p.reject(new Error("Python worker exited unexpectedly"));
    }
    pythonPending.clear();

    if (unexpected && mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("cairn:bridge:crash", {
        code,
        signal,
        stderrTail,
      });
    }
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

/**
 * Bridge call for Stage 5 handlers: rejects on a `{type:"error"}` reply.
 * @param {object} msg
 * @param {number} [timeoutMs]
 * @param {(e: object) => void} [onProgress]
 */
async function selectsCall(msg, timeoutMs = 30_000, onProgress) {
  // The worker handles one request at a time. During an analysis anything else
  // would queue for minutes, time out here, then still run later — refuse it now.
  if (selectsAnalysing && msg.type !== "selects_analyse") {
    throw new Error("Selects is still analysing footage. Wait for it to finish, then try again.");
  }
  const opts = onProgress ? { timeoutMs, onProgress } : { timeoutMs };
  const result = await callPython(msg, opts);
  if (!result || result.type === "error") {
    throw new Error(result?.message ?? `${msg.type} returned nothing`);
  }
  return result;
}

const SELECTS_ANALYSE_TIMEOUT_MS = 4 * 60 * 60 * 1000;
/** Bounded so a stuck request can't leave "Claude is thinking…" up for 20+ minutes. */
const CLAUDE_TIMEOUT_MS = 180_000;
let selectsAnalysing = false;

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

// ── Offload inbox ─────────────────────────────────────────────────────────────
// The Offload menu-bar app (its own repo; contract in specs/offload-handoff.md)
// copies + verifies a card, then
// appends the shoot to this inbox. Cairn lists them as "ready to sort" and drops
// the entry once the shoot has been ingested.

function offloadInboxPath() {
  return path.join(app.getPath("appData"), "Cairn", "offload-inbox.json");
}

function readOffloadInbox() {
  let raw;
  try {
    raw = fs.readFileSync(offloadInboxPath(), "utf8");
  } catch (err) {
    if (err && err.code === "ENOENT") return [];
    throw err;
  }
  const parsed = JSON.parse(raw);
  if (!Array.isArray(parsed)) throw new Error("Offload inbox is not a list.");
  return parsed.filter(
    (e) => e && typeof e.shoot_folder === "string" && typeof e.manifest === "string",
  );
}

function readJsonOrNull(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

function summariseOffloadShoot(entry) {
  // Drive unplugged → manifest unreadable. Keep the entry, just mark it unavailable.
  const manifest = readJsonOrNull(entry.manifest);
  return {
    shoot_folder: entry.shoot_folder,
    added_at: typeof entry.added_at === "string" ? entry.added_at : null,
    available: manifest !== null,
    client: manifest?.client ?? "",
    shoot_name: manifest?.shoot_name ?? path.basename(entry.shoot_folder),
    camera: manifest?.camera ?? "",
    date: manifest?.date ?? "",
    file_count: Array.isArray(manifest?.files) ? manifest.files.length : 0,
  };
}

function registerIpc() {
  ipcMain.handle("cairn:offload:inbox", () => readOffloadInbox().map(summariseOffloadShoot));

  ipcMain.handle("cairn:offload:done", (_event, shootFolder) => {
    if (typeof shootFolder !== "string" || shootFolder.length === 0) {
      throw new Error("offload:done needs a shoot folder path.");
    }
    const entries = readOffloadInbox();
    const remaining = entries.filter((e) => e.shoot_folder !== shootFolder);
    if (remaining.length === entries.length) return false;
    // ponytail: read-modify-write without a lock; Offload could append in the same
    // instant and lose that entry. Re-copying the card re-adds it. Add a lock file if it bites.
    const target = offloadInboxPath();
    const tmp = `${target}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(remaining, null, 2));
    fs.renameSync(tmp, target);
    return true;
  });

  ipcMain.handle("cairn:dialog:open-folder", async () => {
    // Pass mainWindow as parent so the sheet appears attached to the plugin
    // window rather than hiding behind DaVinci Resolve.
    const result = await dialog.showOpenDialog(mainWindow ?? undefined, {
      properties: ["openDirectory"],
    });
    return result.canceled ? null : (result.filePaths[0] ?? null);
  });

  ipcMain.handle("cairn:resolve:snapshot", async () => getResolveSnapshot());

  // Full plugin health check — verifies BOTH the WorkflowIntegration.node
  // connection (used by main.js for project/timeline reads) AND the Python
  // Scripting API connection (used by bridge.py for media-pool writes).
  // The latter is the one users get tripped up by — it requires
  // Preferences → System → General → External scripting: Local.
  ipcMain.handle("cairn:health", async () => {
    const snapshot = await getResolveSnapshot();
    let scriptingOk = false;
    let scriptingReason;
    try {
      const result = await callPython({ type: "health_check" }, { timeoutMs: 5_000 });
      scriptingOk = Boolean(result?.scripting_ok);
      scriptingReason = result?.reason ?? null;
    } catch (err) {
      scriptingReason = err instanceof Error ? err.message : String(err);
    }
    return {
      workflowIntegration: snapshot.available,
      workflowError: snapshot.error ?? null,
      scriptingOk,
      scriptingReason,
    };
  });

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

  ipcMain.handle("cairn:classify:inspect", async (_event, payload) => {
    try {
      return await callPython({ type: "classify_inspect", payload }, { timeoutMs: 30_000 });
    } catch (err) {
      return {
        ok: false,
        duration_sec: 0,
        face_ratio: 0,
        motion_score: 0,
        knn_label: null,
        knn_confidence: null,
        frames: [],
        error: err instanceof Error ? err.message : String(err),
      };
    }
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

  ipcMain.handle("cairn:timeline:build", async (_event, payload) => {
    // Timeline assembly is a single Resolve transaction — no streaming.
    // 60 s is plenty even on hundreds of clips since AppendToTimeline is bulk.
    try {
      return await callPython({ type: "build_timeline", payload }, { timeoutMs: 60_000 });
    } catch (err) {
      return {
        ok: false,
        error: err instanceof Error ? err.message : String(err),
        name: null,
        fps: null,
        resolution: null,
        a_count: 0,
        b_count: 0,
        ambiguous_count: 0,
        missing_paths: [],
        warnings: [],
      };
    }
  });

  ipcMain.handle("cairn:ingest", async (event, payload) => {
    // The single Python bridge worker does BOTH the filesystem organisation AND
    // the Resolve media-pool import (Cairn bin tree, per-clip metadata, dedup)
    // in one streamed call. It is the ONLY process that touches the Resolve
    // Scripting API — Taste Invariant #3: serialise all Resolve calls through a
    // single worker. We never spawn a second Resolve-scripting process; a
    // concurrent second connection deadlocks the non-thread-safe API from inside
    // the plugin sandbox (the old resolve_import.py fallback did exactly that).
    const sendProgress = (obj) => {
      if (!event.sender.isDestroyed()) event.sender.send("cairn:ingest:progress", obj);
    };

    // Organisation (rglob / send2trash) can stall on large external drives and
    // the per-photo ImportMedia loop adds time, so the budget is generous. The
    // bridge streams progress throughout and fails fast (resolve_skipped) when
    // Resolve is unreachable, so this hard cap only bites a genuine hang.
    try {
      const result = await callPython(
        { type: "ingest", payload: { ...payload, resolve: true } },
        {
          timeoutMs: 300_000,
          onProgress: (progressEvent) => sendProgress(progressEvent),
        },
      );
      // The bridge sends a terminal { type: "error", message } (not
      // ingest_complete) if organisation or the Resolve import threw. callPython
      // RESOLVES with that message rather than rejecting, so guard explicitly and
      // surface it as a failure instead of returning a hollow "done" result.
      if (!result || result.type !== "ingest_complete") {
        throw new Error(result?.message ?? "Ingest did not complete.");
      }
      return result;
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err);
      sendProgress({ type: "ingest_progress", event: "ingest_error", reason });
      throw err instanceof Error ? err : new Error(reason);
    }
  });

  // ── Stage 5: Selects ───────────────────────────────────────────────────────
  // Every call goes through the single bridge worker (Taste Invariant #3).
  // The inline bridge resolves `{type:"error"}` instead of rejecting, so
  // selectsCall turns it into a thrown error the renderer can show.
  ipcMain.handle("cairn:selects:presets", () => selectsCall({ type: "selects_presets" }));

  ipcMain.handle("cairn:selects:rescore", (_event, payload) =>
    selectsCall({ type: "selects_rescore", payload }, 60_000),
  );

  ipcMain.handle("cairn:selects:rate", (_event, payload) =>
    selectsCall({ type: "selects_rate", payload }),
  );

  ipcMain.handle("cairn:selects:savePreset", (_event, payload) =>
    selectsCall({ type: "selects_save_preset", payload }),
  );

  ipcMain.handle("cairn:selects:build", (_event, payload) =>
    selectsCall({ type: "selects_build_timeline", payload }, 180_000),
  );

  ipcMain.handle("cairn:selects:analyse", async (event, payload) => {
    const sendProgress = (obj) => {
      if (!event.sender.isDestroyed()) event.sender.send("cairn:selects:progress", obj);
    };
    // Decoding is the slow part (~5x real time for 4K long-GOP). Progress streams
    // the whole way, so this cap only catches a genuine hang.
    if (selectsAnalysing) {
      throw new Error("An analysis is already running.");
    }
    selectsAnalysing = true;
    try {
      return await selectsCall(
        { type: "selects_analyse", payload },
        SELECTS_ANALYSE_TIMEOUT_MS,
        sendProgress,
      );
    } finally {
      selectsAnalysing = false;
    }
  });

  ipcMain.handle("cairn:selects:learn", async (_event, payload) => {
    if (typeof payload?.path !== "string" || !payload.path) {
      return { ok: false, error: "No shoot folder was given." };
    }
    const apiKey = readStoredKeys()["ANTHROPIC_API_KEY"] || process.env["ANTHROPIC_API_KEY"];
    if (!apiKey) {
      return { ok: false, error: "Add your Anthropic API key in Settings to use taste learning." };
    }
    let Anthropic;
    try {
      Anthropic = selectsLearn.loadSdk();
    } catch (err) {
      return { ok: false, error: `Anthropic SDK missing from the plugin bundle: ${err.message}` };
    }
    const client = new Anthropic({ apiKey, timeout: CLAUDE_TIMEOUT_MS, maxRetries: 1 });
    const python = (msg) => selectsCall(msg, 60_000);
    return selectsLearn.learnTaste({ python, client, Anthropic }, payload);
  });
}

// One flat page colour from the title bar down (Lyra's look). Must match --page in src/ui/app.css,
// so the window never flashes a different colour before the renderer paints.
const PAGE_COLOUR = "#292929";

/** macOS: hide the title bar and let the page run under the floating traffic lights. */
function seamlessChrome() {
  if (process.platform !== "darwin") return {};
  return { titleBarStyle: "hidden", backgroundColor: PAGE_COLOUR };
}

function createMainWindow() {
  const win = new BrowserWindow({
    width: 1040,
    height: 720,
    minWidth: 560,
    minHeight: 480,
    useContentSize: true,
    autoHideMenuBar: true,
    title: "Cairn",
    ...seamlessChrome(),
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

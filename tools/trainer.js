#!/usr/bin/env node
/**
 * Cairn Trainer — standalone clip labeling tool.
 *
 * Generates short animated GIF previews for each clip, lets you label them
 * A-ROLL or B-ROLL (or skip), and writes confirmed examples with feature
 * vectors to ~/.cairn/ground_truth.json. The main classifier reads this file
 * to improve future classifications via KNN lookup.
 *
 * Usage:
 *   node tools/trainer.js <shoot_folder>
 *   node tools/trainer.js /Volumes/Samsung\ Evo\ 4TB/260320\ CLUBHAUUS\ promo\ 1
 *
 * Keyboard shortcuts in the browser:
 *   A = A-roll    B = B-roll    S = Skip    F = Show in Finder
 */

"use strict";

const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { execFile, execFileSync, exec } = require("node:child_process");
const { createHash } = require("node:crypto");

// ── Config ────────────────────────────────────────────────────────────────────

const PORT = 7432;
const VIDEO_EXTS = new Set([".mp4", ".mov", ".mxf", ".avi", ".m2ts"]);
const GIF_DURATION_SEC = 4;
const GIF_FPS = 8;
const GIF_WIDTH = 520;
const CAIRN_DIR = path.join(os.homedir(), ".cairn");
const GROUND_TRUTH_PATH = path.join(CAIRN_DIR, "ground_truth.json");
const CACHE_DIR = path.join(CAIRN_DIR, "trainer-cache");

// ── Binary resolution (mirrors classify.py logic) ─────────────────────────────

const PROJECT_ROOT = path.join(__dirname, "..");

function findBin(name) {
  const candidates = [
    path.join(PROJECT_ROOT, "assets", "bin", name),
    `/opt/homebrew/bin/${name}`,
    `/usr/local/bin/${name}`,
    name,
  ];
  for (const p of candidates) {
    try {
      if (fs.existsSync(p)) return p;
    } catch {
      // ignore
    }
  }
  return name;
}

const FFMPEG = findBin("ffmpeg");
const FFPROBE = findBin("ffprobe");

// ── Ground truth ──────────────────────────────────────────────────────────────

function loadGroundTruth() {
  try {
    return JSON.parse(fs.readFileSync(GROUND_TRUTH_PATH, "utf8"));
  } catch {
    return { schema_version: 1, clips: [] };
  }
}

function saveGroundTruth(gt) {
  fs.mkdirSync(CAIRN_DIR, { recursive: true });
  fs.writeFileSync(GROUND_TRUTH_PATH, JSON.stringify(gt, null, 2), "utf8");
}

function labeledPaths(gt) {
  return new Set(gt.clips.map((c) => c.clip_path));
}

// ── Clip scanning ─────────────────────────────────────────────────────────────

function scanClips(shootFolder) {
  const clips = [];
  for (const camDir of ["A_CAM", "B_CAM", "C_CAM"]) {
    const dir = path.join(shootFolder, "01_VIDEOS", camDir);
    if (!fs.existsSync(dir)) continue;
    for (const f of fs.readdirSync(dir).sort()) {
      if (VIDEO_EXTS.has(path.extname(f).toLowerCase())) {
        clips.push(path.join(dir, f));
      }
    }
  }
  return clips;
}

// ── Duration probe ────────────────────────────────────────────────────────────

function probeDuration(clipPath) {
  try {
    const out = execFileSync(
      FFPROBE,
      ["-v", "quiet", "-print_format", "json", "-show_format", clipPath],
      { timeout: 15_000 },
    );
    const data = JSON.parse(out.toString());
    return parseFloat(data?.format?.duration ?? "0") || 0;
  } catch {
    return 0;
  }
}

// ── GIF generation (two-pass for quality) ────────────────────────────────────

function clipHash(clipPath) {
  return createHash("sha256").update(clipPath).digest("hex").slice(0, 16);
}

/**
 * Generate a looping animated GIF for the clip and call back with the path.
 * Samples from 20% into the clip so blank leader is skipped.
 * Cached in CACHE_DIR — regenerated only if missing.
 */
function generateGif(clipPath, durationSec, callback) {
  fs.mkdirSync(CACHE_DIR, { recursive: true });
  const hash = clipHash(clipPath);
  const gifPath = path.join(CACHE_DIR, `${hash}.gif`);
  const palPath = path.join(CACHE_DIR, `${hash}_pal.png`);

  if (fs.existsSync(gifPath)) {
    callback(null, gifPath);
    return;
  }

  const dur = durationSec > 0 ? durationSec : 10;
  const start = (dur * 0.2).toFixed(2);
  const gifLen = Math.min(GIF_DURATION_SEC, Math.max(1, dur - parseFloat(start)));
  const vf = `fps=${GIF_FPS},scale=${GIF_WIDTH}:-1:flags=lanczos`;

  // Pass 1: build optimal colour palette from sampled frames
  execFile(
    FFMPEG,
    [
      "-ss",
      start,
      "-t",
      String(gifLen),
      "-i",
      clipPath,
      "-vf",
      `${vf},palettegen=stats_mode=diff`,
      "-y",
      palPath,
    ],
    { timeout: 45_000 },
    (err) => {
      if (err) {
        callback(err);
        return;
      }
      // Pass 2: render GIF with palette
      execFile(
        FFMPEG,
        [
          "-ss",
          start,
          "-t",
          String(gifLen),
          "-i",
          clipPath,
          "-i",
          palPath,
          "-vf",
          `${vf},paletteuse=dither=bayer:bayer_scale=3`,
          "-loop",
          "0",
          "-y",
          gifPath,
        ],
        { timeout: 45_000 },
        (err2) => {
          try {
            fs.unlinkSync(palPath);
          } catch {
            // ignore
          }
          if (err2) {
            callback(err2);
            return;
          }
          callback(null, gifPath);
        },
      );
    },
  );
}

// ── Embedded UI ───────────────────────────────────────────────────────────────

const HTML = /* html */ `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cairn Trainer</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#111113;color:#e0e0e0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh}
  .wrap{max-width:580px;margin:0 auto;padding:2rem 1.5rem}
  .topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:1.25rem}
  .logo{font-size:.8rem;color:#555;font-weight:600;letter-spacing:.1em;text-transform:uppercase}
  .prog{font-size:.82rem;color:#555}
  .prog strong{color:#888}

  .gif-wrap{
    background:#1b1b1d;border-radius:10px;overflow:hidden;
    min-height:292px;display:flex;align-items:center;justify-content:center;
    position:relative;
  }
  .gif-wrap img{width:100%;display:block;border-radius:10px}
  .gif-error{color:#555;font-size:.82rem;text-align:center;padding:2rem}

  .spinner-wrap{display:flex;flex-direction:column;align-items:center;gap:.75rem;color:#444;font-size:.82rem}
  .spinner{width:22px;height:22px;border:2px solid #2a2a2c;border-top-color:#555;border-radius:50%;animation:spin .7s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}

  .clip-info{margin:.9rem 0 1.1rem}
  .clip-name{font-family:"SF Mono",ui-monospace,monospace;font-size:.9rem;color:#c8c8cc;word-break:break-all}
  .clip-meta{font-size:.76rem;color:#555;margin-top:.3rem}

  .btns{display:flex;gap:.55rem}
  .btn{
    padding:.72rem 1rem;border:none;border-radius:8px;font-size:.95rem;
    cursor:pointer;font-weight:600;transition:filter .12s,transform .08s;
    display:flex;align-items:center;justify-content:center;gap:.45rem
  }
  .btn:hover{filter:brightness(1.15)}
  .btn:active{transform:scale(.97);filter:brightness(.9)}
  .btn-a{background:#1d3a6b;color:#7eb3ff;flex:2}
  .btn-b{background:#1a4228;color:#7dd4a0;flex:2}
  .btn-s{background:#232325;color:#666;flex:1;font-weight:500}
  .kbd{background:#1a1a1c;padding:.1rem .35rem;border-radius:3px;font-size:.68rem;
       color:#555;border:1px solid #2a2a2c;font-family:inherit}

  .row2{display:flex;gap:.55rem;margin-top:.55rem}
  .btn-finder{
    flex:1;background:transparent;border:1px solid #2a2a2c;color:#666;
    border-radius:8px;padding:.55rem 1rem;font-size:.82rem;cursor:pointer;
    display:flex;align-items:center;justify-content:center;gap:.4rem;
    transition:border-color .12s,color .12s
  }
  .btn-finder:hover{border-color:#444;color:#aaa}

  .hint{font-size:.7rem;color:#333;text-align:center;margin-top:.9rem}

  .done{text-align:center;padding:5rem 1rem}
  .done h2{color:#666;font-size:1.1rem;margin-bottom:.5rem;font-weight:500}
  .done p{color:#444;font-size:.82rem;line-height:1.6}
  .done code{font-family:"SF Mono",ui-monospace,monospace;color:#555;font-size:.78rem}
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <span class="logo">Cairn Trainer</span>
    <span class="prog" id="prog"></span>
  </div>
  <div id="app"></div>
</div>
<script>
let cur = null;

async function getState() {
  const r = await fetch('/api/state');
  return r.json();
}

async function render() {
  cur = await getState();
  const app = document.getElementById('app');
  const prog = document.getElementById('prog');

  if (cur.done) {
    prog.textContent = '';
    app.innerHTML = \`<div class="done">
      <h2>All clips labeled ✓</h2>
      <p>\${cur.totalLabeled} examples written to<br>
        <code>~/.cairn/ground_truth.json</code></p>
    </div>\`;
    return;
  }

  prog.innerHTML = \`<strong>\${cur.index + 1}</strong> / \${cur.total} &nbsp;·&nbsp; \${cur.totalLabeled} labeled\`;

  const durText = cur.duration > 0
    ? \`\${cur.duration.toFixed(1)} s\`
    : 'duration unknown';

  app.innerHTML = \`
    <div class="gif-wrap" id="gifWrap">
      <div class="spinner-wrap"><div class="spinner"></div>Generating preview…</div>
    </div>
    <div class="clip-info">
      <div class="clip-name">\${cur.clipName}</div>
      <div class="clip-meta">\${cur.camDir} &nbsp;·&nbsp; \${durText}</div>
    </div>
    <div class="btns">
      <button class="btn btn-a" onclick="doLabel('A-ROLL')">
        A-roll <span class="kbd">A</span>
      </button>
      <button class="btn btn-b" onclick="doLabel('B-ROLL')">
        B-roll <span class="kbd">B</span>
      </button>
      <button class="btn btn-s" onclick="doLabel('SKIP')">
        Skip <span class="kbd">S</span>
      </button>
    </div>
    <div class="row2">
      <button class="btn-finder" onclick="doFinder()">
        Show in Finder <span class="kbd">F</span>
      </button>
    </div>
    <p class="hint">A &nbsp;·&nbsp; B &nbsp;·&nbsp; S skip &nbsp;·&nbsp; F finder</p>
  \`;

  pollGif(cur.index);
}

function pollGif(idx) {
  fetch(\`/api/gif/\${idx}\`)
    .then(r => {
      if (r.status === 202) { setTimeout(() => pollGif(idx), 900); return null; }
      if (!r.ok) { showGifError(); return null; }
      return r.blob();
    })
    .then(blob => {
      if (!blob) return;
      if (cur.index !== idx) return; // user moved on
      const url = URL.createObjectURL(blob);
      const wrap = document.getElementById('gifWrap');
      if (wrap) wrap.innerHTML = \`<img src="\${url}" alt="clip preview">\`;
    })
    .catch(() => setTimeout(() => pollGif(idx), 1200));
}

function showGifError() {
  const wrap = document.getElementById('gifWrap');
  if (wrap) wrap.innerHTML = '<div class="gif-error">Preview unavailable — use Finder to review this clip</div>';
}

async function doLabel(label) {
  await fetch('/api/label', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ label }),
  });
  render();
}

async function doFinder() {
  if (!cur) return;
  await fetch('/api/open-finder', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ path: cur.clipPath }),
  });
}

document.addEventListener('keydown', e => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === 'a' || e.key === 'A') doLabel('A-ROLL');
  else if (e.key === 'b' || e.key === 'B') doLabel('B-ROLL');
  else if (e.key === 's' || e.key === 'S') doLabel('SKIP');
  else if (e.key === 'f' || e.key === 'F') doFinder();
});

render();
</script>
</body>
</html>`;

// ── Server ────────────────────────────────────────────────────────────────────

function main() {
  const shootFolder = process.argv[2];

  if (!shootFolder) {
    console.error("Usage: node tools/trainer.js <shoot_folder>");
    process.exit(1);
  }

  const resolved = path.resolve(shootFolder);
  if (!fs.existsSync(resolved)) {
    console.error(`Folder not found: ${resolved}`);
    process.exit(1);
  }

  const allClips = scanClips(resolved);
  if (allClips.length === 0) {
    console.error("No video clips found under 01_VIDEOS/{A_CAM,B_CAM,C_CAM}/");
    process.exit(1);
  }

  const gt = loadGroundTruth();
  const already = labeledPaths(gt);

  // Unlabeled clips only — already-labeled clips are skipped
  const queue = allClips.filter((p) => !already.has(p));

  let index = 0;

  // Duration cache (probed lazily on first access per clip)
  const durCache = new Map();
  function getDuration(clipPath) {
    if (!durCache.has(clipPath)) {
      durCache.set(clipPath, probeDuration(clipPath));
    }
    return durCache.get(clipPath);
  }

  // GIF state: 'pending' | 'ready' | 'error'
  const gifState = new Map(); // clipPath → { status, gifPath? }

  function ensureGif(clipPath) {
    if (gifState.has(clipPath)) return;
    gifState.set(clipPath, { status: "pending" });
    const dur = getDuration(clipPath);
    generateGif(clipPath, dur, (err, gifPath) => {
      gifState.set(clipPath, err ? { status: "error" } : { status: "ready", gifPath });
    });
  }

  function currentClip() {
    return queue[index] ?? null;
  }

  function send(res, status, body) {
    const json = JSON.stringify(body);
    res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
    res.end(json);
  }

  const server = http.createServer((req, res) => {
    const { pathname } = new URL(req.url, `http://localhost:${PORT}`);

    // ── UI
    if (req.method === "GET" && pathname === "/") {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(HTML);
      return;
    }

    // ── Current state
    if (req.method === "GET" && pathname === "/api/state") {
      const clip = currentClip();
      if (!clip) {
        send(res, 200, { done: true, totalLabeled: gt.clips.length });
        return;
      }
      ensureGif(clip); // kick off GIF generation if not started
      const parts = clip.split(path.sep);
      const camDir = parts[parts.length - 2] ?? "";
      send(res, 200, {
        done: false,
        index,
        total: queue.length,
        totalLabeled: gt.clips.length,
        clipPath: clip,
        clipName: path.basename(clip),
        camDir,
        duration: getDuration(clip),
      });
      return;
    }

    // ── Serve GIF (202 while pending, 200+body when ready)
    if (req.method === "GET" && pathname.startsWith("/api/gif/")) {
      const idx = parseInt(pathname.split("/").pop() ?? "-1", 10);
      const clip = queue[idx];
      if (!clip) {
        res.writeHead(404);
        res.end();
        return;
      }
      ensureGif(clip);
      const state = gifState.get(clip);
      if (!state || state.status === "pending") {
        res.writeHead(202, { "Content-Type": "application/json" });
        res.end('{"status":"pending"}');
        return;
      }
      if (state.status === "error") {
        res.writeHead(500);
        res.end();
        return;
      }
      const data = fs.readFileSync(state.gifPath);
      res.writeHead(200, {
        "Content-Type": "image/gif",
        "Content-Length": String(data.length),
        "Cache-Control": "public, max-age=3600",
      });
      res.end(data);
      return;
    }

    // ── Label a clip
    if (req.method === "POST" && pathname === "/api/label") {
      let body = "";
      req.on("data", (d) => {
        body += d;
      });
      req.on("end", () => {
        const { label } = JSON.parse(body);
        const clip = currentClip();

        if (clip && label !== "SKIP") {
          const duration = getDuration(clip);
          gt.clips.push({
            clip_path: clip,
            clip_name: path.basename(clip),
            shoot_folder: resolved,
            label,
            features: { duration_sec: duration },
            confirmed_at: new Date().toISOString(),
          });
          saveGroundTruth(gt);
        }

        index++;

        // Pre-generate GIF for the next clip so it's ready when user arrives
        const next = queue[index];
        if (next) ensureGif(next);

        send(res, 200, { ok: true });
      });
      return;
    }

    // ── Open in Finder (macOS: reveal file)
    if (req.method === "POST" && pathname === "/api/open-finder") {
      let body = "";
      req.on("data", (d) => {
        body += d;
      });
      req.on("end", () => {
        const { path: clipPath } = JSON.parse(body);
        // open -R reveals the file in Finder without launching the default app
        exec(`open -R ${JSON.stringify(clipPath)}`);
        send(res, 200, { ok: true });
      });
      return;
    }

    res.writeHead(404);
    res.end();
  });

  server.listen(PORT, "127.0.0.1", () => {
    const url = `http://localhost:${PORT}`;
    const skipped = allClips.length - queue.length;

    console.log("\nCairn Trainer");
    console.log("─".repeat(48));
    console.log(`Folder  : ${resolved}`);
    console.log(
      `Clips   : ${queue.length} to label${skipped ? ` (${skipped} already labeled)` : ""}`,
    );
    console.log(`ffmpeg  : ${FFMPEG}`);
    console.log(`ffprobe : ${FFPROBE}`);
    console.log(`Output  : ${GROUND_TRUTH_PATH}`);
    console.log("─".repeat(48));
    console.log(`\nOpening ${url} …\n`);

    exec(`open ${url}`);
  });
}

main();

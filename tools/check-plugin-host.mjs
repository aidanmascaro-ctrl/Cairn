#!/usr/bin/env node
/**
 * Smoke-test Cairn on a given Electron binary — by default the private Electron
 * that DaVinci Resolve itself uses to run Workflow Integration plugins.
 *
 * Launches the plugin's real main.js, then over the Chrome DevTools protocol
 * checks: WorkflowIntegration.node loaded, the preload bridge is exposed, the
 * Python bridge answers, and the CSP is enforced with no violations from the UI.
 *
 * It does NOT prove the plugin works from Resolve's Workspace → Workflow
 * Integrations menu. The addon's handshake only succeeds when Resolve launched
 * the process itself, so "Failed to initialize communication with host" is the
 * expected answer here and shows the addon loaded and ran. Test the menu by hand.
 *
 * Usage (macOS, after `npm run build:ui` and `npm run install-plugin`):
 *   node tools/check-plugin-host.mjs                 # Resolve's Electron
 *   node tools/check-plugin-host.mjs --app <path>    # a plugin folder or app.asar
 *   node tools/check-plugin-host.mjs --electron "$(node -p "require('electron')")"
 */

import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const RESOLVE_ELECTRON =
  "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Applications/.hidden/Electron.app/Contents/MacOS/Electron";
const DEVTOOLS_PORT = 9347;
const LOAD_TIMEOUT_MS = 20_000;
const HEALTH_TIMEOUT_MS = 15_000;
const POLL_MS = 250;
const SETTLE_MS = 1_500;

const { values: args } = parseArgs({
  options: {
    electron: { type: "string", default: RESOLVE_ELECTRON },
    app: { type: "string", default: resolve(dirname(fileURLToPath(import.meta.url)), "..") },
  },
});

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

async function findPage(port) {
  const deadline = Date.now() + LOAD_TIMEOUT_MS;
  while (Date.now() < deadline) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      const page = targets.find((t) => t.type === "page" && t.url.endsWith("/index.html"));
      if (page) return page;
    } catch {
      // Electron is still starting; the DevTools port isn't listening yet.
    }
    await sleep(POLL_MS);
  }
  throw new Error(`No Cairn window appeared within ${LOAD_TIMEOUT_MS} ms`);
}

/** Minimal CDP client: send(method, params) → result, plus a list of events received. */
async function connect(wsUrl) {
  const socket = new WebSocket(wsUrl);
  await new Promise((done, fail) => {
    socket.addEventListener("open", done, { once: true });
    socket.addEventListener("error", () => fail(new Error("DevTools socket failed")), {
      once: true,
    });
  });
  const pending = new Map();
  const events = [];
  let nextId = 1;
  socket.addEventListener("message", ({ data }) => {
    const msg = JSON.parse(data);
    if (!msg.id) {
      events.push(msg);
      return;
    }
    const { done, fail } = pending.get(msg.id);
    pending.delete(msg.id);
    if (msg.error) fail(new Error(msg.error.message));
    else done(msg.result);
  });
  const send = (method, params = {}) =>
    new Promise((done, fail) => {
      const id = nextId++;
      pending.set(id, { done, fail });
      socket.send(JSON.stringify({ id, method, params }));
    });
  return { send, events, close: () => socket.close() };
}

async function evaluate(cdp, expression) {
  const { result, exceptionDetails } = await cdp.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
    // Defaults to true, which would exempt this evaluation from the page's CSP.
    allowUnsafeEvalBlockedByCSP: false,
  });
  if (exceptionDetails) {
    throw new Error(exceptionDetails.exception?.description ?? exceptionDetails.text);
  }
  return result.value;
}

const withTimeout = (expression, ms) =>
  `Promise.race([${expression}, new Promise((_, no) => setTimeout(() => no(new Error("timed out")), ${ms}))])`;

// Each probe must be refused by the CSP. They never reach the network or run.
const CSP_PROBES = `(async () => {
  const before = window.__cspViolations.length;
  let fetchBlocked = false;
  try { await fetch("https://example.invalid/"); } catch { fetchBlocked = true; }
  let inlineRan = false;
  window.__inlineRan = () => { inlineRan = true; };
  const script = document.createElement("script");
  script.textContent = "window.__inlineRan()";
  document.head.append(script);
  let evalBlocked = false;
  try { new Function("return 1")(); } catch { evalBlocked = true; }
  await new Promise((done) => setTimeout(done, 100));
  const dataImageLoads = await new Promise((done) => {
    const img = new Image();
    img.onload = () => done(true);
    img.onerror = () => done(false);
    img.src = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";
  });
  return {
    fetchBlocked, inlineBlocked: !inlineRan, evalBlocked, dataImageLoads,
    probeViolations: window.__cspViolations.splice(before),
  };
})()`;

async function runChecks(cdp) {
  await cdp.send("Runtime.enable");
  await cdp.send("Log.enable");
  await cdp.send("Page.enable");
  await cdp.send("Page.addScriptToEvaluateOnNewDocument", {
    source: `window.__cspViolations = [];
      document.addEventListener("securitypolicyviolation", (e) =>
        window.__cspViolations.push(e.violatedDirective + " " + e.blockedURI));`,
  });
  await cdp.send("Page.reload");
  await sleep(SETTLE_MS);

  const hostVersions = await evaluate(cdp, "window.cairn.ping()");
  const health = await evaluate(cdp, withTimeout("window.cairn.health()", HEALTH_TIMEOUT_MS));
  const snapshot = await evaluate(
    cdp,
    withTimeout("window.cairn.getResolveSnapshot()", HEALTH_TIMEOUT_MS),
  );
  const pageViolations = await evaluate(cdp, "window.__cspViolations.splice(0)");
  const rendered = await evaluate(cdp, 'document.getElementById("app").childElementCount');
  const stylesLoaded = await evaluate(
    cdp,
    'getComputedStyle(document.body).backgroundColor !== "rgba(0, 0, 0, 0)"',
  );
  const csp = await evaluate(cdp, CSP_PROBES);
  const pageErrors = cdp.events
    .filter((e) => e.method === "Runtime.exceptionThrown")
    .map((e) => e.params.exceptionDetails.text);

  return {
    hostVersions,
    health,
    snapshot,
    pageViolations,
    rendered,
    stylesLoaded,
    csp,
    pageErrors,
  };
}

function verdicts(report) {
  const { csp } = report;
  return {
    "UI rendered": report.rendered > 0,
    "stylesheet applied": report.stylesLoaded,
    "no uncaught page errors": report.pageErrors.length === 0,
    "no CSP violations from the UI": report.pageViolations.length === 0,
    "CSP blocks network": csp.fetchBlocked,
    "CSP blocks inline script": csp.inlineBlocked,
    "CSP blocks eval": csp.evalBlocked,
    "CSP allows data: images (thumbnails)": csp.dataImageLoads,
    "Python bridge answers": typeof report.health?.scriptingOk === "boolean",
    "WorkflowIntegration.node loaded": !/not loaded/.test(report.snapshot?.error ?? ""),
  };
}

const child = spawn(args.electron, [`--remote-debugging-port=${DEVTOOLS_PORT}`, args.app], {
  stdio: ["ignore", "pipe", "pipe"],
});
const logs = [];
child.stdout.on("data", (d) => logs.push(d.toString()));
child.stderr.on("data", (d) => logs.push(d.toString()));
child.on("error", (err) => {
  console.error(`Could not launch ${args.electron}: ${err.message}`);
  process.exit(2);
});

let failed = true;
try {
  const page = await findPage(DEVTOOLS_PORT);
  const cdp = await connect(page.webSocketDebuggerUrl);
  const report = await runChecks(cdp);
  cdp.close();
  console.log(JSON.stringify(report, null, 2));
  const results = Object.entries(verdicts(report));
  for (const [name, ok] of results) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
  failed = results.some(([, ok]) => !ok);
} catch (err) {
  console.error(`Check failed: ${err.message}`);
} finally {
  child.kill();
}
const notable = logs
  .join("")
  .split("\n")
  .filter((l) => /\[cairn\]|error|cannot find/i.test(l));
if (notable.length) console.log(`\nMain-process log:\n${notable.join("\n")}`);
process.exit(failed ? 1 : 0);

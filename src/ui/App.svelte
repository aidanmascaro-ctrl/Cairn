<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import type {
    CairnBridge,
    ResolveSnapshot,
    PingResult,
    HealthResult,
    BridgeCrashEvent,
  } from "./types";
  import Settings from "./Settings.svelte";
  import DropZone from "./DropZone.svelte";
  import ClassifyPanel from "./ClassifyPanel.svelte";
  import TimelinePanel from "./TimelinePanel.svelte";
  import SelectsPanel from "./selects/SelectsPanel.svelte";

  // `window.cairn` is exposed by src/orchestrator/preload.js via contextBridge.
  const cairn: CairnBridge | undefined = (window as unknown as { cairn?: CairnBridge }).cairn;

  let snapshot: ResolveSnapshot | null = null;
  let ping: PingResult | null = null;
  let error: string | null = null;

  let echoResult: string | null = null;
  let echoRunning = false;

  let ingestFolder: string | null = null;
  let pendingAmbiguous = 0;

  let health: HealthResult | null = null;
  let bridgeCrash: BridgeCrashEvent | null = null;
  let unsubBridgeCrash: (() => void) | null = null;

  function handleIngestComplete(e: CustomEvent<{ folder: string }>) {
    ingestFolder = e.detail.folder;
  }

  function handlePendingChange(e: CustomEvent<{ count: number }>) {
    pendingAmbiguous = e.detail.count;
  }

  function handleFolderPicked(e: CustomEvent<{ folder: string }>) {
    ingestFolder = e.detail.folder;
  }

  onMount(async () => {
    if (!cairn) {
      error = "window.cairn is not defined. Preload script did not run — check Electron setup.";
      return;
    }
    unsubBridgeCrash = cairn.onBridgeCrash((ev) => {
      bridgeCrash = ev;
    });
    try {
      ping = await cairn.ping();
      snapshot = await cairn.getResolveSnapshot();
      health = await cairn.health();
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    }
  });

  onDestroy(() => {
    unsubBridgeCrash?.();
  });

  async function refreshHealth() {
    if (!cairn) return;
    health = await cairn.health();
    bridgeCrash = null;
  }

  async function refresh() {
    if (!cairn) return;
    snapshot = await cairn.getResolveSnapshot();
  }

  async function testEcho() {
    if (!cairn || echoRunning) return;
    echoRunning = true;
    echoResult = null;
    try {
      const res = await cairn.echo("hello from Cairn");
      echoResult = `✓ ${res.payload} — ${res.timestamp_iso}`;
    } catch (err) {
      echoResult = `✗ ${err instanceof Error ? err.message : String(err)}`;
    } finally {
      echoRunning = false;
    }
  }
</script>

<main>
  <header>
    <h1>Cairn</h1>
    <p class="tagline">
      A stack of stones on a trail. The pre-edit work is done; the creative work is yours.
    </p>
  </header>

  {#if bridgeCrash}
    <section class="banner banner-error">
      <strong>Python worker crashed</strong>
      <span class="banner-detail"
        >exit {bridgeCrash.code ?? "?"}{bridgeCrash.signal
          ? ` (signal ${bridgeCrash.signal})`
          : ""}</span
      >
      {#if bridgeCrash.stderrTail}
        <pre class="banner-trace">{bridgeCrash.stderrTail}</pre>
      {/if}
      <button class="banner-btn" on:click={refreshHealth}>Retry</button>
    </section>
  {:else if health && (!health.scriptingOk || !health.workflowIntegration)}
    <section class="banner banner-warn">
      {#if !health.workflowIntegration}
        <strong>DaVinci Resolve link is down.</strong>
        <span class="banner-detail"
          >{health.workflowError ?? "Install the plugin into Resolve."}</span
        >
      {:else if !health.scriptingOk}
        <strong>Resolve External Scripting is not reachable.</strong>
        <span class="banner-detail"
          >{health.scriptingReason ??
            "Open Resolve → Preferences → System → General and set External scripting to Local."}</span
        >
      {/if}
      <button class="banner-btn" on:click={refreshHealth}>Re-check</button>
    </section>
  {/if}

  <DropZone on:ingestComplete={handleIngestComplete} />

  <ClassifyPanel
    {ingestFolder}
    on:pendingChange={handlePendingChange}
    on:folderPicked={handleFolderPicked}
  />

  <TimelinePanel {ingestFolder} {pendingAmbiguous} />

  <SelectsPanel {ingestFolder} />

  <Settings />

  <details class="diagnostics">
    <summary>Diagnostics</summary>

    <section class="card">
      <h2>Resolve link</h2>
      {#if error}
        <p class="error">⚠ {error}</p>
      {:else if snapshot === null}
        <p>Initialising…</p>
      {:else if snapshot.available}
        <dl>
          <dt>Project</dt>
          <dd>{snapshot.projectName ?? "<unnamed>"}</dd>
          <dt>Timeline</dt>
          <dd>{snapshot.timelineName ?? "<none open>"}</dd>
        </dl>
        <button on:click={refresh}>Refresh</button>
      {:else}
        <p class="warn">Workflow Integration not available.</p>
        {#if snapshot.error}
          <p class="details">{snapshot.error}</p>
        {/if}
        <button on:click={refresh}>Retry</button>
      {/if}
    </section>

    <section class="card">
      <h2>Python bridge</h2>
      <button on:click={testEcho} disabled={echoRunning}>
        {echoRunning ? "Sending…" : "Echo test"}
      </button>
      {#if echoResult}
        <p class="echo-result">{echoResult}</p>
      {/if}
    </section>

    {#if ping}
      <section class="card versions">
        <h2>Runtime</h2>
        <dl>
          <dt>Plugin ID</dt>
          <dd><code>{ping.pluginId}</code></dd>
          <dt>Electron</dt>
          <dd>{ping.electron}</dd>
          <dt>Chromium</dt>
          <dd>{ping.chrome}</dd>
          <dt>Node</dt>
          <dd>{ping.node}</dd>
        </dl>
      </section>
    {/if}
  </details>
</main>

<style>
  main {
    max-width: 720px;
    margin: 0 auto;
    padding: 2.5rem 1.5rem;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    color: #e6e6e6;
  }
  header {
    margin-bottom: 2rem;
  }
  header h1 {
    font-size: 2.25rem;
    margin: 0;
    letter-spacing: -0.02em;
  }
  header .tagline {
    color: #9a9a9a;
    margin-top: 0.25rem;
    font-style: italic;
  }
  .card {
    background: #1f1f21;
    border: 1px solid #2a2a2c;
    border-radius: 12px;
    padding: 1.5rem;
    margin-bottom: 1.25rem;
  }
  .card h2 {
    margin: 0 0 0.75rem 0;
    font-size: 1.05rem;
    color: #b9b9b9;
    font-weight: 600;
    letter-spacing: 0.02em;
    text-transform: uppercase;
  }
  dl {
    display: grid;
    grid-template-columns: 120px 1fr;
    row-gap: 0.4rem;
    margin: 0;
  }
  dt {
    color: #7f7f82;
    font-size: 0.9rem;
  }
  dd {
    margin: 0;
    font-size: 0.95rem;
  }
  code {
    font-family: "SF Mono", ui-monospace, monospace;
    font-size: 0.85rem;
    color: #c7d5ff;
  }
  button {
    margin-top: 1rem;
    background: #3a3a3d;
    color: inherit;
    border: 1px solid #4a4a4d;
    border-radius: 6px;
    padding: 0.4rem 0.9rem;
    font-size: 0.9rem;
    cursor: pointer;
  }
  button:hover {
    background: #46464a;
  }
  .error {
    color: #f28b82;
  }
  .warn {
    color: #f5c877;
  }
  .details {
    color: #9a9a9a;
    font-size: 0.88rem;
    margin-top: 0.25rem;
  }
  .echo-result {
    margin-top: 0.75rem;
    font-size: 0.88rem;
    font-family: "SF Mono", ui-monospace, monospace;
    color: #a8d8a8;
    word-break: break-all;
  }
  .banner {
    border-radius: 8px;
    padding: 0.75rem 1rem;
    margin-bottom: 1rem;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    align-items: center;
    line-height: 1.45;
    font-size: 0.88rem;
  }
  .banner-error {
    background: rgba(242, 139, 130, 0.08);
    border: 1px solid rgba(242, 139, 130, 0.3);
    color: #f28b82;
  }
  .banner-warn {
    background: rgba(240, 190, 70, 0.08);
    border: 1px solid rgba(240, 190, 70, 0.3);
    color: #f0c850;
  }
  .banner strong {
    font-weight: 700;
  }
  .banner-detail {
    color: #c0c0c3;
    flex: 1;
    min-width: 0;
  }
  .banner-trace {
    flex-basis: 100%;
    background: #131315;
    color: #a0a0a3;
    border-radius: 4px;
    padding: 0.5rem 0.6rem;
    margin: 0.4rem 0 0 0;
    font-family: "SF Mono", ui-monospace, monospace;
    font-size: 0.72rem;
    max-height: 140px;
    overflow: auto;
    white-space: pre-wrap;
  }
  .banner-btn {
    background: transparent;
    color: inherit;
    border: 1px solid currentColor;
    border-radius: 4px;
    padding: 0.2rem 0.6rem;
    font-size: 0.78rem;
    cursor: pointer;
    margin: 0;
  }
  .banner-btn:hover {
    background: rgba(255, 255, 255, 0.06);
  }
  .diagnostics {
    margin-top: 2rem;
  }
  .diagnostics > summary {
    cursor: pointer;
    color: #7f7f82;
    font-size: 0.82rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 0.4rem 0;
    border-top: 1px solid #2a2a2c;
    user-select: none;
    list-style: none;
  }
  .diagnostics > summary::-webkit-details-marker {
    display: none;
  }
  .diagnostics > summary::before {
    content: "▸ ";
    display: inline-block;
    transition: transform 0.15s ease;
  }
  .diagnostics[open] > summary::before {
    content: "▾ ";
  }
  .diagnostics > summary:hover {
    color: #b9b9b9;
  }
  .diagnostics[open] {
    padding-bottom: 1rem;
  }
  .diagnostics > .card:first-of-type {
    margin-top: 1rem;
  }
</style>

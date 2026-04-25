<script lang="ts">
  import { onMount } from "svelte";
  import type { CairnBridge, ResolveSnapshot, PingResult } from "./types";
  import Settings from "./Settings.svelte";
  import DropZone from "./DropZone.svelte";
  import ClassifyPanel from "./ClassifyPanel.svelte";
  import TranscribePanel from "./TranscribePanel.svelte";
  import VisionPanel from "./VisionPanel.svelte";
  import BriefPanel from "./BriefPanel.svelte";
  import SoundbitesPanel from "./SoundbitesPanel.svelte";
  import MatchingPanel from "./MatchingPanel.svelte";
  import SidebarPanel from "./SidebarPanel.svelte";

  // `window.cairn` is exposed by src/orchestrator/preload.js via contextBridge.
  const cairn: CairnBridge | undefined = (window as unknown as { cairn?: CairnBridge }).cairn;

  let snapshot: ResolveSnapshot | null = null;
  let ping: PingResult | null = null;
  let error: string | null = null;

  let echoResult: string | null = null;
  let echoRunning = false;

  let ingestFolder: string | null = null;

  function handleIngestComplete(e: CustomEvent<{ folder: string }>) {
    ingestFolder = e.detail.folder;
  }

  onMount(async () => {
    if (!cairn) {
      error = "window.cairn is not defined. Preload script did not run — check Electron setup.";
      return;
    }
    try {
      ping = await cairn.ping();
      snapshot = await cairn.getResolveSnapshot();
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    }
  });

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

  <DropZone on:ingestComplete={handleIngestComplete} />

  <ClassifyPanel {ingestFolder} />

  <TranscribePanel {ingestFolder} />

  <VisionPanel {ingestFolder} />

  <BriefPanel {ingestFolder} />

  <SoundbitesPanel {ingestFolder} />

  <MatchingPanel {ingestFolder} />

  <SidebarPanel {ingestFolder} />

  <Settings />

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
</style>

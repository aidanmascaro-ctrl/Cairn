<script lang="ts">
  import { afterUpdate, createEventDispatcher, onDestroy, onMount } from "svelte";
  import type { CairnBridge, IngestProgressEvent, IngestResult, OffloadShoot } from "./types";

  const dispatch = createEventDispatcher<{ ingestComplete: { folder: string } }>();

  const cairn: CairnBridge | undefined = (window as unknown as { cairn?: CairnBridge }).cairn;

  type State = "idle" | "running" | "done" | "error";

  let state: State = "idle";
  let folderPath = "";
  let isDragOver = false;
  let dryRun = false;
  let progressLines: string[] = [];
  let result: IngestResult | null = null;
  let errorMsg = "";

  let progressLogEl: HTMLDivElement | undefined;

  // ── Offload inbox: shoots copied off a card by the Offload menu-bar app ────
  const OFFLOAD_POLL_MS = 10_000;
  let offloadShoots: OffloadShoot[] = [];
  let offloadError = "";
  let offloadTimer: ReturnType<typeof setInterval> | undefined;

  async function refreshOffload() {
    if (!cairn) return;
    try {
      offloadShoots = await cairn.offload.list();
      offloadError = "";
    } catch (err) {
      offloadError = err instanceof Error ? err.message : String(err);
    }
  }

  async function sortOffloadShoot(shoot: OffloadShoot) {
    if (!cairn || state === "running") return;
    folderPath = shoot.shoot_folder;
    dryRun = false;
    await runIngest();
    // Keep the shoot in the list unless it actually reached Resolve (a closed
    // Resolve or failed import reports done with no counts).
    if (state !== "done" || !result || Object.keys(result.resolve_counts).length === 0) return;
    try {
      await cairn.offload.done(shoot.shoot_folder);
    } catch (err) {
      errorMsg = `Imported, but couldn't clear it from the Offload list: ${err instanceof Error ? err.message : String(err)}`;
    }
    await refreshOffload();
  }

  const onWindowFocus = () => void refreshOffload();

  onMount(() => {
    onWindowFocus();
    offloadTimer = setInterval(onWindowFocus, OFFLOAD_POLL_MS);
    window.addEventListener("focus", onWindowFocus);
  });

  onDestroy(() => {
    clearInterval(offloadTimer);
    window.removeEventListener("focus", onWindowFocus);
  });

  afterUpdate(() => {
    if (progressLogEl !== undefined) {
      progressLogEl.scrollTop = progressLogEl.scrollHeight;
    }
  });

  function handleDragOver(e: DragEvent) {
    e.preventDefault();
    isDragOver = true;
    if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
  }

  function handleDragLeave() {
    isDragOver = false;
  }

  function handleDrop(e: DragEvent) {
    e.preventDefault();
    isDragOver = false;
    const files = e.dataTransfer?.files;
    if (!cairn || !files || files.length === 0) return;
    // In sandboxed renderers File.path is hidden, so resolve via the preload
    // bridge (webUtils.getPathForFile). Returns "" if the source isn't a real
    // file on disk — e.g. a synthetic drag payload from another app.
    const first = files.item(0);
    const resolved = first ? cairn.getPathForFile(first) : "";
    if (resolved) {
      folderPath = resolved;
      errorMsg = "";
    } else {
      errorMsg = "Could not resolve a path for the dropped item — try the Choose folder button.";
      state = "error";
    }
  }

  async function pickFolder() {
    if (!cairn) return;
    const picked = await cairn.openFolder();
    if (picked) folderPath = picked;
  }

  async function runIngest() {
    if (!cairn || !folderPath || state === "running") return;
    state = "running";
    progressLines = [];
    result = null;
    errorMsg = "";

    const unsub = cairn.ingest.onProgress((ev: IngestProgressEvent) => {
      progressLines = [...progressLines, formatProgress(ev)];
    });

    try {
      result = await cairn.ingest.run({ path: folderPath, dry_run: dryRun });
      state = "done";
      if (!result.dry_run) dispatch("ingestComplete", { folder: result.folder });
    } catch (err) {
      errorMsg = err instanceof Error ? err.message : String(err);
      state = "error";
    } finally {
      unsub();
    }
  }

  function formatProgress(ev: IngestProgressEvent): string {
    switch (ev.event) {
      case "file_moved":
        return `→ ${ev.dst ?? ev.src ?? ""}`;
      case "shells_removed":
        return `✓ Removed ${String(ev.count ?? 0)} empty shell folder(s)`;
      case "sidecars_written":
        return `✓ Wrote ${String(ev.count ?? 0)} sidecar JSON file(s)`;
      case "resolve_imported":
        return `✓ Imported to Resolve`;
      case "metadata_refused":
        return `⚠ Resolve wouldn't store clip metadata: ${(ev.fields ?? []).join(", ")}`;
      case "resolve_skipped":
        return `⚠ Resolve skipped: ${ev.reason ?? ""}`;
      case "org_skipped":
        return `⚠ Organisation skipped: ${ev.reason ?? ""}`;
      case "ingest_error":
        return `✗ Ingest failed: ${ev.reason ?? ""}`;
      default:
        return ev.event;
    }
  }

  function reset() {
    state = "idle";
    folderPath = "";
    progressLines = [];
    result = null;
    errorMsg = "";
  }
</script>

<section class="card">
  <h2>Ingest</h2>

  {#if state === "idle" && offloadShoots.length > 0}
    <div class="offload-inbox" role="list" aria-label="Shoots ready from Offload">
      <p class="offload-title">Ready from Offload</p>
      {#each offloadShoots as shoot (shoot.shoot_folder)}
        <div class="offload-row" class:unavailable={!shoot.available} role="listitem">
          <div class="offload-meta">
            <span class="offload-name">
              {shoot.client ? `${shoot.client} — ` : ""}{shoot.shoot_name}
            </span>
            <span class="offload-detail">
              {shoot.available
                ? `${shoot.file_count} files · ${shoot.camera} · ${shoot.date}`
                : "Drive not plugged in"}
            </span>
          </div>
          <button
            class="sort-btn"
            on:click={() => void sortOffloadShoot(shoot)}
            disabled={!shoot.available || !cairn}
          >
            Sort
          </button>
        </div>
      {/each}
    </div>
  {/if}
  {#if offloadError}
    <p class="offload-error">⚠ Couldn't read the Offload inbox: {offloadError}</p>
  {/if}

  {#if state === "idle"}
    <div
      class="drop-zone"
      class:drag-over={isDragOver}
      on:dragover={handleDragOver}
      on:dragleave={handleDragLeave}
      on:drop={handleDrop}
      role="region"
      aria-label="Drop shoot folder here"
    >
      <p class="drop-hint">Drop shoot folder here</p>
      <span class="drop-or">or</span>
      <button class="pick-btn" on:click={pickFolder}>Choose folder…</button>
    </div>

    {#if folderPath}
      <p class="folder-path">{folderPath}</p>
    {/if}

    <div class="controls">
      <label class="dry-run-label">
        <input type="checkbox" bind:checked={dryRun} />
        Dry run (no files moved)
      </label>
      <button class="ingest-btn" on:click={runIngest} disabled={!folderPath || !cairn}>
        {dryRun ? "Preview ingest" : "Run ingest"}
      </button>
    </div>
  {:else if state === "running"}
    <p class="running-hint">Ingesting<span class="ellipsis">…</span></p>
  {:else if state === "done" && result !== null}
    <div class="result-summary">
      <p class="result-ok">
        ✓ Ingest complete{result.dry_run ? " (dry run — no files moved)" : ""}
      </p>
      <dl>
        <dt>Folder</dt>
        <dd class="path-dd">{result.folder}</dd>
        <dt>Files moved</dt>
        <dd>{result.files_moved}</dd>
        {#if (result.move_failures ?? 0) > 0}
          <dt>Move failures</dt>
          <dd class="warn-dd">{result.move_failures}</dd>
        {/if}
        {#if result.layout === "offload" && result.camera_clips}
          {#each Object.entries(result.camera_clips) as [camera, clips] (camera)}
            <dt>{camera}</dt>
            <dd>{clips.length}</dd>
          {/each}
        {:else}
          <dt>A-cam clips</dt>
          <dd>{result.clip_paths.a_cam.length}</dd>
          {#if result.clip_paths.c_cam.length > 0}
            <dt>C-cam (drone)</dt>
            <dd>{result.clip_paths.c_cam.length}</dd>
          {/if}
        {/if}
        <dt>Photos</dt>
        <dd>{result.clip_paths.photos.length}</dd>
      </dl>
      <button on:click={reset}>Ingest another folder</button>
    </div>
  {:else if state === "error"}
    <div class="result-error">
      <p class="error-msg">✗ {errorMsg}</p>
      <button on:click={reset}>Try again</button>
    </div>
  {/if}

  {#if progressLines.length > 0}
    <div class="progress-log" bind:this={progressLogEl}>
      {#each progressLines as line, i (i)}
        <div class="log-line">{line}</div>
      {/each}
    </div>
  {/if}
</section>

<style>
  .card {
    background: var(--surface);
    border: 1px solid var(--hairline);
    border-radius: 12px;
    padding: 1.5rem;
    margin-bottom: 1.25rem;
  }
  .card h2 {
    margin: 0 0 1rem 0;
    font-size: 11px;
    color: var(--text-tertiary);
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .drop-zone {
    border: 2px dashed var(--control);
    border-radius: 8px;
    padding: 2rem 1.5rem;
    text-align: center;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.5rem;
    transition:
      border-color 0.15s,
      background 0.15s;
  }
  .drop-zone.drag-over {
    border-color: #5a8cff;
    background: rgba(90, 140, 255, 0.06);
  }
  .drop-hint {
    color: var(--text-tertiary);
    font-size: 0.95rem;
    margin: 0;
  }
  .drop-or {
    color: var(--control-border);
    font-size: 0.8rem;
  }
  .pick-btn {
    background: #2e2e31;
    color: #c0c0c3;
    border: 1px solid var(--control);
    border-radius: 6px;
    padding: 0.35rem 0.8rem;
    font-size: 0.88rem;
    cursor: pointer;
  }
  .offload-inbox {
    margin-bottom: 1rem;
    border: 1px solid #2a4a8a;
    border-radius: 8px;
    background: rgba(90, 140, 255, 0.05);
    padding: 0.75rem 0.9rem;
  }
  .offload-title {
    margin: 0 0 0.5rem 0;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #8fb0ff;
  }
  .offload-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.45rem 0;
    border-top: 1px solid var(--hairline);
  }
  .offload-row:first-of-type {
    border-top: none;
  }
  .offload-row.unavailable {
    opacity: 0.5;
  }
  .offload-meta {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-width: 0;
  }
  .offload-name {
    color: #d8d8db;
    font-size: 0.9rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .offload-detail {
    color: var(--text-tertiary);
    font-size: 0.78rem;
  }
  .sort-btn {
    background: #2a4a8a;
    color: #d8e8ff;
    border: 1px solid #3a5a9a;
    border-radius: 6px;
    padding: 0.35rem 0.9rem;
    cursor: pointer;
  }
  .sort-btn:hover:not(:disabled),
  .sort-btn:focus-visible {
    background: #335aaa;
  }
  .sort-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .offload-error {
    color: #e0b070;
    font-size: 0.8rem;
    margin: 0 0 0.75rem 0;
  }
  .pick-btn:hover {
    background: var(--control);
  }
  .folder-path {
    margin: 0.75rem 0 0 0;
    font-size: 0.82rem;
    font-family: "SF Mono", ui-monospace, monospace;
    color: #9a9a9f;
    word-break: break-all;
  }
  .controls {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-top: 0.9rem;
    flex-wrap: wrap;
  }
  .dry-run-label {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.88rem;
    color: var(--text-tertiary);
    cursor: pointer;
    user-select: none;
  }
  .dry-run-label input[type="checkbox"] {
    accent-color: #5a8cff;
  }
  .ingest-btn {
    background: #2a4a8a;
    color: #d8e8ff;
    border: 1px solid #3a5a9a;
    border-radius: 6px;
    padding: 0.4rem 1rem;
    font-size: 0.9rem;
    cursor: pointer;
    margin-left: auto;
  }
  .ingest-btn:hover:not(:disabled) {
    background: #335aaa;
  }
  .ingest-btn:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .running-hint {
    color: #9a9a9f;
    font-size: 0.9rem;
    margin: 0 0 0.75rem 0;
  }
  .ellipsis {
    display: inline-block;
  }
  .progress-log {
    margin-top: 0.75rem;
    background: var(--inset);
    border: 1px solid var(--hairline);
    border-radius: 6px;
    padding: 0.6rem 0.75rem;
    max-height: 200px;
    overflow-y: auto;
    font-family: "SF Mono", ui-monospace, monospace;
    font-size: 0.78rem;
  }
  .log-line {
    color: #a0a0a3;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-all;
  }
  .result-summary {
    margin-top: 0.75rem;
  }
  .result-ok {
    color: #a8d8a8;
    font-size: 0.9rem;
    margin: 0 0 0.75rem 0;
  }
  dl {
    display: grid;
    grid-template-columns: 110px 1fr;
    row-gap: 0.35rem;
    margin: 0 0 0.75rem 0;
  }
  dt {
    color: var(--text-tertiary);
    font-size: 0.85rem;
  }
  dd {
    margin: 0;
    font-size: 0.9rem;
  }
  .path-dd {
    font-family: "SF Mono", ui-monospace, monospace;
    font-size: 0.78rem;
    word-break: break-all;
    color: #9a9a9f;
  }
  .warn-dd {
    color: #f0c850;
    font-weight: 600;
  }
  .result-error {
    margin-top: 0.75rem;
  }
  .error-msg {
    color: #f28b82;
    font-size: 0.9rem;
    margin: 0 0 0.75rem 0;
    word-break: break-all;
  }
  button {
    background: var(--control);
    color: inherit;
    border: 1px solid var(--control-border);
    border-radius: 6px;
    padding: 0.4rem 0.9rem;
    font-size: 0.9rem;
    cursor: pointer;
  }
  button:hover:not(:disabled) {
    background: var(--control-hover);
  }
  button:disabled {
    opacity: 0.4;
    cursor: default;
  }
</style>

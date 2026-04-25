<script lang="ts">
  import { afterUpdate, onMount } from "svelte";
  import type { CairnBridge, IngestProgressEvent, IngestResult } from "./types";

  const cairn: CairnBridge | undefined = (window as unknown as { cairn?: CairnBridge }).cairn;

  type State = "idle" | "running" | "done" | "error";

  let state: State = "idle";
  let folderPath = "";
  let isDragOver = false;
  let dryRun = false;
  let progressLines: string[] = [];
  let result: IngestResult | null = null;
  let errorMsg = "";

  let folderInput: HTMLInputElement;
  let progressLogEl: HTMLDivElement | undefined;

  onMount(() => {
    // webkitdirectory is not a standard HTML attribute — set via DOM to avoid TS errors
    folderInput.setAttribute("webkitdirectory", "");
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
    if (!files || files.length === 0) return;
    // Electron exposes an absolute `path` property on dropped File objects
    const f = files[0] as File & { path?: string };
    if (f.path) folderPath = f.path;
  }

  function handleFolderPick() {
    const files = folderInput.files;
    if (!files || files.length === 0) return;
    const first = files[0] as File & { path?: string };
    if (first.path && first.webkitRelativePath) {
      // Derive chosen directory from first file's absolute path and relative path
      // first.path = "/parent/chosen/sub/file.ext"
      // first.webkitRelativePath = "chosen/sub/file.ext"
      const prefix = first.path.slice(0, first.path.length - first.webkitRelativePath.length);
      const topDir = first.webkitRelativePath.split("/")[0] ?? "";
      folderPath = prefix + topDir;
    }
    folderInput.value = "";
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
      case "resolve_skipped":
        return `⚠ Resolve skipped: ${ev.reason ?? ""}`;
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

<!-- Hidden file input — webkitdirectory set in onMount -->
<input bind:this={folderInput} type="file" class="hidden-input" on:change={handleFolderPick} />

<section class="card">
  <h2>Ingest</h2>

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
      <button
        class="pick-btn"
        on:click={() => {
          folderInput.click();
        }}>Choose folder…</button
      >
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
        <dt>A-cam clips</dt>
        <dd>{result.clip_paths.a_cam.length}</dd>
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
  .hidden-input {
    display: none;
  }
  .card {
    background: #1f1f21;
    border: 1px solid #2a2a2c;
    border-radius: 12px;
    padding: 1.5rem;
    margin-bottom: 1.25rem;
  }
  .card h2 {
    margin: 0 0 1rem 0;
    font-size: 1.05rem;
    color: #b9b9b9;
    font-weight: 600;
    letter-spacing: 0.02em;
    text-transform: uppercase;
  }
  .drop-zone {
    border: 2px dashed #3a3a3d;
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
    color: #7f7f82;
    font-size: 0.95rem;
    margin: 0;
  }
  .drop-or {
    color: #4a4a4d;
    font-size: 0.8rem;
  }
  .pick-btn {
    background: #2e2e31;
    color: #c0c0c3;
    border: 1px solid #3a3a3d;
    border-radius: 6px;
    padding: 0.35rem 0.8rem;
    font-size: 0.88rem;
    cursor: pointer;
  }
  .pick-btn:hover {
    background: #3a3a3d;
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
    color: #7f7f82;
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
    background: #131315;
    border: 1px solid #2a2a2c;
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
    color: #7f7f82;
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
    background: #3a3a3d;
    color: inherit;
    border: 1px solid #4a4a4d;
    border-radius: 6px;
    padding: 0.4rem 0.9rem;
    font-size: 0.9rem;
    cursor: pointer;
  }
  button:hover:not(:disabled) {
    background: #46464a;
  }
  button:disabled {
    opacity: 0.4;
    cursor: default;
  }
</style>

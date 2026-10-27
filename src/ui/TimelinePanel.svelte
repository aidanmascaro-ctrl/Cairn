<script lang="ts">
  import type { CairnBridge, BuildTimelineResult } from "./types";

  export let ingestFolder: string | null = null;
  export let pendingAmbiguous: number = 0;

  const cairn: CairnBridge | undefined = (window as unknown as { cairn?: CairnBridge }).cairn;

  type State = "idle" | "running" | "done" | "error";

  let state: State = "idle";
  let result: BuildTimelineResult | null = null;
  let errorMsg = "";
  let timelineName = "";

  $: defaultName = ingestFolder
    ? `${ingestFolder.split("/").pop() ?? "Cairn"} — Cairn Assembly`
    : "";
  $: canBuild = !!cairn && !!ingestFolder && state !== "running";

  async function build() {
    if (!cairn || !ingestFolder || state === "running") return;
    state = "running";
    result = null;
    errorMsg = "";

    try {
      const payload: { path: string; name?: string } = { path: ingestFolder };
      const trimmed = timelineName.trim();
      if (trimmed) payload.name = trimmed;

      const res = await cairn.timeline.build(payload);
      result = res;
      if (res.ok) {
        state = "done";
      } else {
        state = "error";
        errorMsg = res.error ?? "Unknown error";
      }
    } catch (err) {
      state = "error";
      errorMsg = err instanceof Error ? err.message : String(err);
    }
  }

  function reset() {
    state = "idle";
    result = null;
    errorMsg = "";
  }
</script>

<section class="card">
  <h2>Assembly timeline</h2>

  {#if !ingestFolder}
    <p class="hint">Run ingest first to point Cairn at a shoot folder.</p>
  {:else if state === "idle"}
    <p class="hint">
      Builds a new Resolve timeline at the source resolution and frame rate. A-roll clips land on
      V1/A1; B-roll on V2/A2.
      {#if pendingAmbiguous > 0}
        <span class="warn">
          {pendingAmbiguous} ambiguous clip{pendingAmbiguous === 1 ? "" : "s"} will be skipped — confirm
          them above to include.
        </span>
      {/if}
    </p>

    <label class="name-label">
      Timeline name (optional)
      <input type="text" bind:value={timelineName} placeholder={defaultName} spellcheck="false" />
    </label>

    <button class="primary" on:click={build} disabled={!canBuild}> Build assembly timeline </button>
  {:else if state === "running"}
    <p class="hint">Building<span class="ellipsis">…</span></p>
  {:else if state === "done" && result !== null && result.ok}
    <div class="result-summary">
      <p class="result-ok">✓ Timeline ready in Resolve</p>
      <dl>
        <dt>Name</dt>
        <dd>{result.name}</dd>
        <dt>Resolution</dt>
        <dd>{result.resolution}</dd>
        <dt>Frame rate</dt>
        <dd>{result.fps} fps</dd>
        <dt>A-roll on V1</dt>
        <dd>{result.a_count}</dd>
        <dt>B-roll on V2</dt>
        <dd>{result.b_count}</dd>
        {#if result.ambiguous_count > 0}
          <dt>Skipped (ambiguous)</dt>
          <dd>{result.ambiguous_count}</dd>
        {/if}
        {#if result.missing_paths.length > 0}
          <dt>Not in media pool</dt>
          <dd>{result.missing_paths.length}</dd>
        {/if}
      </dl>
      {#if result.warnings.length > 0}
        <ul class="warnings">
          {#each result.warnings as w (w)}
            <li>⚠ {w}</li>
          {/each}
        </ul>
      {/if}
      <button on:click={reset}>Build another</button>
    </div>
  {:else if state === "error"}
    <div class="result-error">
      <p class="error-msg">✗ {errorMsg}</p>
      <button on:click={reset}>Try again</button>
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
  .hint {
    color: #9a9a9f;
    font-size: 0.9rem;
    margin: 0 0 0.9rem 0;
    line-height: 1.5;
  }
  .warn {
    color: #f5c877;
    display: block;
    margin-top: 0.4rem;
  }
  .name-label {
    display: block;
    color: var(--text-tertiary);
    font-size: 0.85rem;
    margin-bottom: 0.9rem;
  }
  .name-label input {
    display: block;
    width: 100%;
    margin-top: 0.35rem;
    background: var(--inset);
    border: 1px solid var(--hairline);
    color: #d8d8d8;
    border-radius: 6px;
    padding: 0.45rem 0.6rem;
    font-size: 0.9rem;
    font-family: inherit;
    box-sizing: border-box;
  }
  .name-label input:focus {
    outline: none;
    border-color: #5a8cff;
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
  button.primary {
    background: #2a4a8a;
    color: #d8e8ff;
    border-color: #3a5a9a;
  }
  button.primary:hover:not(:disabled) {
    background: #335aaa;
  }
  .ellipsis {
    display: inline-block;
  }
  .result-summary {
    margin-top: 0.25rem;
  }
  .result-ok {
    color: #a8d8a8;
    font-size: 0.9rem;
    margin: 0 0 0.75rem 0;
  }
  dl {
    display: grid;
    grid-template-columns: 160px 1fr;
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
  .warnings {
    margin: 0 0 0.75rem 0;
    padding-left: 1.1rem;
    color: #f5c877;
    font-size: 0.85rem;
  }
  .warnings li {
    margin-bottom: 0.2rem;
  }
  .result-error {
    margin-top: 0.5rem;
  }
  .error-msg {
    color: #f28b82;
    font-size: 0.9rem;
    margin: 0 0 0.75rem 0;
    word-break: break-all;
  }
</style>

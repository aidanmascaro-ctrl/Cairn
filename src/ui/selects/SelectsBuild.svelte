<script lang="ts">
  // Build the selects timeline straight into the open Resolve project.
  import { createEventDispatcher } from "svelte";
  import type { SelectsBuildResult, SelectsLayout, SelectsOrder } from "../types";

  export let defaultName = "";
  export let selectCount = 0;
  export let busy = false;
  export let result: SelectsBuildResult | null = null;

  const dispatch = createEventDispatcher<{
    build: {
      layout: SelectsLayout;
      order: SelectsOrder;
      include_markers: boolean;
      clip_markers: boolean;
      name?: string;
    };
  }>();

  let layout: SelectsLayout = "context";
  let order: SelectsOrder = "timeline";
  let includeMarkers = true;
  let clipMarkers = true;
  let name = "";

  const ORDERS: { value: SelectsOrder; label: string }[] = [
    { value: "timeline", label: "Shot order" },
    { value: "score_desc", label: "Best first" },
    { value: "score_asc", label: "Weakest first" },
    { value: "duration_desc", label: "Longest first" },
    { value: "duration_asc", label: "Shortest first" },
  ];

  function submit(): void {
    const trimmed = name.trim();
    dispatch("build", {
      layout,
      order,
      include_markers: includeMarkers,
      clip_markers: clipMarkers,
      ...(trimmed ? { name: trimmed } : {}),
    });
  }
</script>

<div class="build">
  <div class="layouts" role="radiogroup" aria-label="Timeline layout">
    <label class="layout" class:active={layout === "context"}>
      <input type="radio" bind:group={layout} value="context" />
      <span class="diagram" aria-hidden="true">
        <span class="lane top"
          ><i style="left:18%;width:20%"></i><i style="left:62%;width:14%"></i></span
        >
        <span class="lane"
          ><i class="grey" style="left:0;width:18%"></i><i class="grey" style="left:38%;width:24%"
          ></i><i class="grey" style="left:76%;width:24%"></i></span
        >
      </span>
      <span class="text"
        ><b>Selects over the full clips</b> Selects on V2, the rest of each clip on V1 underneath.</span
      >
    </label>
    <label class="layout" class:active={layout === "selects_only"}>
      <input type="radio" bind:group={layout} value="selects_only" />
      <span class="diagram" aria-hidden="true">
        <span class="lane top"></span>
        <span class="lane"
          ><i style="left:0;width:20%"></i><i style="left:20%;width:14%"></i><i
            style="left:34%;width:26%"
          ></i></span
        >
      </span>
      <span class="text"><b>Selects only</b> Packed back to back on V1.</span>
    </label>
  </div>

  <div class="options">
    {#if layout === "selects_only"}
      <label
        >Order
        <select bind:value={order}>
          {#each ORDERS as o (o.value)}<option value={o.value}>{o.label}</option>{/each}
        </select>
      </label>
    {/if}
    <label class="check"
      ><input type="checkbox" bind:checked={includeMarkers} /> Score markers on the timeline</label
    >
    <label class="check"
      ><input type="checkbox" bind:checked={clipMarkers} /> Same markers on the source clips</label
    >
    <label class="name"
      >Timeline name
      <input type="text" bind:value={name} placeholder={defaultName} spellcheck="false" />
    </label>
  </div>

  <button class="primary" disabled={busy || selectCount === 0} on:click={submit}>
    {busy
      ? "Building in Resolve…"
      : `Build timeline with ${selectCount} select${selectCount === 1 ? "" : "s"}`}
  </button>

  {#if result}
    {#if result.ok}
      <div class="result">
        <p class="ok">✓ “{result.name}” is open in Resolve</p>
        <p class="facts">
          {result.select_count} selects · {result.resolution} · {result.fps} fps · {result.markers_added}
          timeline markers{result.clip_markers_added
            ? ` · ${result.clip_markers_added} clip markers`
            : ""}
        </p>
        {#if result.missing_paths.length}
          <p class="warn">
            {result.missing_paths.length} analysed clip{result.missing_paths.length === 1
              ? " isn't"
              : "s aren't"}
            in the media pool, so they were left out. Run Ingest to import them.
          </p>
        {/if}
        {#each result.warnings as w (w)}<p class="warn">⚠ {w}</p>{/each}
      </div>
    {:else}
      <p class="error" role="alert">✗ {result.error}</p>
    {/if}
  {/if}
</div>

<style>
  .layouts {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.6rem;
  }
  .layout {
    display: grid;
    grid-template-columns: 4.5rem 1fr;
    gap: 0.7rem;
    align-items: center;
    border: 1px solid var(--sel-line);
    border-radius: 9px;
    padding: 0.6rem 0.7rem;
    cursor: pointer;
    transition: border-color 120ms ease;
  }
  .layout:hover {
    border-color: var(--sel-line-strong);
  }
  .layout.active {
    border-color: var(--sel-accent);
    background: var(--sel-accent-soft);
  }
  .layout:focus-within {
    outline: 2px solid var(--sel-accent);
    outline-offset: 2px;
  }
  .layout input {
    position: absolute;
    opacity: 0;
    pointer-events: none;
  }
  .diagram {
    display: grid;
    gap: 3px;
  }
  .lane {
    position: relative;
    height: 9px;
    background: var(--sel-well);
    border-radius: 2px;
  }
  .lane i {
    position: absolute;
    top: 0;
    bottom: 0;
    background: hsl(120 50% 50%);
    border-radius: 2px;
  }
  .lane i.grey {
    background: var(--sel-line-strong);
  }
  .text {
    font-size: 0.78rem;
    color: var(--sel-muted);
    line-height: 1.4;
  }
  .text b {
    display: block;
    color: var(--sel-text);
    font-weight: 600;
    font-size: 0.84rem;
  }
  .options {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1.2rem;
    align-items: center;
    margin: 0.9rem 0;
    font-size: 0.82rem;
    color: var(--sel-muted);
  }
  .check {
    display: flex;
    gap: 0.4rem;
    align-items: center;
  }
  .name {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    flex: 1 1 260px;
  }
  .name input,
  select {
    background: var(--sel-well);
    border: 1px solid var(--sel-line);
    color: var(--sel-text);
    border-radius: 6px;
    padding: 0.3rem 0.5rem;
    font-size: 0.82rem;
  }
  .name input {
    flex: 1;
  }
  button.primary {
    background: var(--sel-accent-soft);
    border: 1px solid var(--sel-accent);
    color: var(--sel-accent-text);
    border-radius: 6px;
    padding: 0.45rem 1rem;
    font-size: 0.88rem;
    cursor: pointer;
  }
  button.primary:disabled {
    opacity: 0.45;
    cursor: default;
  }
  button:focus-visible,
  input:focus-visible,
  select:focus-visible {
    outline: 2px solid var(--sel-accent);
    outline-offset: 2px;
  }
  .result {
    margin-top: 0.8rem;
  }
  .ok {
    color: var(--sel-keep);
    margin: 0;
    font-size: 0.9rem;
  }
  .facts {
    color: var(--sel-muted);
    font-size: 0.8rem;
    margin: 0.2rem 0 0;
  }
  .warn {
    color: var(--sel-warn);
    font-size: 0.8rem;
    margin: 0.3rem 0 0;
  }
  .error {
    color: var(--sel-reject);
    font-size: 0.86rem;
    margin-top: 0.8rem;
    word-break: break-word;
  }
</style>

<script lang="ts" context="module">
  const BEAT_COLORS: Record<string, string> = {
    "origin-story": "#2d6a3f",
    "emotional-peak": "#7a2020",
    factual: "#1e3a5f",
    hook: "#5a4800",
    quote: "#1a4a4a",
    "process-detail": "#3a1a5f",
    humour: "#5f1a4a",
    reflection: "#2a2050",
    technical: "#0d3050",
    tension: "#4a005a",
    resolution: "#0d3a30",
  };

  function beatColor(beatType: string): string {
    return BEAT_COLORS[beatType] ?? "#333";
  }
</script>

<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import type { CairnBridge, SoundbiteEntry, SoundbitesProgressEvent } from "./types";

  export let ingestFolder: string | null = null;

  const cairn: CairnBridge | undefined = (window as unknown as { cairn?: CairnBridge }).cairn;

  type State = "idle" | "estimating" | "ready" | "running" | "done" | "error";

  let state: State = "idle";
  let estimateClips = 0;
  let estimateCost = 0;
  let errorMsg = "";
  let progressLines: string[] = [];
  let processedClips = 0;
  let totalClips = 0;
  let totalBites = 0;
  let flaggedBites: SoundbiteEntry[] = [];
  let unsub: (() => void) | null = null;

  onMount(() => {
    if (ingestFolder && cairn) void doEstimate();
  });

  onDestroy(() => {
    unsub?.();
  });

  $: if (ingestFolder && cairn) {
    void doEstimate();
  }

  async function doEstimate() {
    if (!cairn || !ingestFolder || state !== "idle") return;
    state = "estimating";
    try {
      const result = await cairn.soundbites.estimate({ path: ingestFolder });
      estimateClips = result.clip_count;
      estimateCost = result.estimated_cost_usd;
      state = estimateClips === 0 ? "idle" : "ready";
    } catch (err) {
      errorMsg = err instanceof Error ? err.message : String(err);
      state = "error";
    }
  }

  async function runSoundbites() {
    if (!cairn || !ingestFolder || state !== "ready") return;
    state = "running";
    progressLines = [];
    processedClips = 0;
    flaggedBites = [];

    unsub = cairn.soundbites.onProgress((ev: SoundbitesProgressEvent) => {
      if (ev.event === "soundbites_started") {
        totalClips = ev.total_clips ?? estimateClips;
        progressLines = [...progressLines, `Starting — ${totalClips} A-roll clips`];
      } else if (ev.event === "clip_processed") {
        processedClips += 1;
        const n = ev.soundbite_count ?? 0;
        const f = ev.flagged_count ?? 0;
        const name = ev.file ? (ev.file.split("/").pop() ?? ev.file) : "";
        progressLines = [
          ...progressLines,
          `✓ ${name} — ${n} soundbite${n !== 1 ? "s" : ""}${f > 0 ? ` (${f} flagged)` : ""}`,
        ];
        if (ev.soundbites) {
          const newFlagged = ev.soundbites.filter((b) => b.flagged_for_review);
          flaggedBites = [...flaggedBites, ...newFlagged];
        }
      } else if (ev.event === "clip_error") {
        const name = ev.file ? (ev.file.split("/").pop() ?? ev.file) : "";
        progressLines = [...progressLines, `✗ ${name} — ${ev.reason ?? "error"}`];
      } else if (ev.event === "soundbites_error") {
        errorMsg = ev.reason ?? "Unknown error";
        state = "error";
      }
    });

    try {
      const result = await cairn.soundbites.run({ path: ingestFolder });
      totalBites = result.total_bites;
      state = "done";
    } catch (err) {
      errorMsg = err instanceof Error ? err.message : String(err);
      state = "error";
    } finally {
      // unsub is non-null here — TypeScript narrows after the assignment above
      unsub();
      unsub = null;
    }
  }

  $: progressPct = totalClips > 0 ? Math.round((processedClips / totalClips) * 100) : 0;
</script>

{#if ingestFolder !== null}
  <section class="card">
    <h2>Soundbites</h2>

    {#if state === "idle" || state === "estimating"}
      <p class="hint">
        {state === "estimating" ? "Scanning A-roll clips…" : "No A-roll clips ready."}
      </p>
    {:else if state === "error"}
      <p class="error-msg">✗ {errorMsg}</p>
      <button
        class="secondary-btn"
        on:click={() => {
          state = "idle";
          void doEstimate();
        }}>Retry</button
      >
    {:else if state === "ready"}
      <p class="hint">
        {estimateClips} A-roll clip{estimateClips !== 1 ? "s" : ""} ready · est.
        <span class="cost">${estimateCost.toFixed(3)}</span>
      </p>
      <button class="primary-btn" on:click={runSoundbites}>Identify soundbites</button>
    {:else if state === "running"}
      <div class="progress-row">
        <span class="progress-label">{processedClips}/{totalClips}</span>
        <div class="bar-wrap">
          <div class="bar-fill" style="width:{progressPct}%"></div>
        </div>
        <span class="pct-label">{progressPct}%</span>
      </div>
      <div class="log">
        {#each progressLines as line, i (i)}
          <p class="log-line">{line}</p>
        {/each}
      </div>
    {:else if state === "done"}
      <p class="done-label">
        ✓ {totalBites} soundbite{totalBites !== 1 ? "s" : ""} identified across {processedClips} clip{processedClips !==
        1
          ? "s"
          : ""}
      </p>

      {#if flaggedBites.length > 0}
        <div class="flagged-block">
          <p class="flagged-header">
            {flaggedBites.length} bite{flaggedBites.length !== 1 ? "s" : ""} flagged for review
          </p>
          {#each flaggedBites as bite (bite.id)}
            <div class="flagged-row">
              <span class="beat-badge" style="background:{beatColor(bite.beat_type)}"
                >{bite.beat_type}</span
              >
              <span class="bite-label">{bite.label}</span>
              <span class="bite-tc">{bite.in_tc} – {bite.out_tc}</span>
              <span class="bite-score"
                >judge: {bite.judge_score != null ? bite.judge_score.toFixed(2) : "—"}</span
              >
            </div>
          {/each}
        </div>
      {/if}

      <div class="log">
        {#each progressLines as line, i (i)}
          <p class="log-line">{line}</p>
        {/each}
      </div>
    {/if}
  </section>
{/if}

<style>
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
  .hint {
    font-size: 0.88rem;
    color: #7f7f82;
    margin: 0 0 0.75rem 0;
  }
  .cost {
    color: #a8d8a8;
    font-weight: 600;
  }
  .primary-btn {
    background: #2a4a8a;
    color: #d8e8ff;
    border: 1px solid #3a5a9a;
    border-radius: 6px;
    padding: 0.45rem 1rem;
    font-size: 0.9rem;
    cursor: pointer;
    font-family: inherit;
    margin-top: 0.5rem;
  }
  .primary-btn:hover {
    background: #335aaa;
  }
  .secondary-btn {
    background: #3a3a3d;
    color: inherit;
    border: 1px solid #4a4a4d;
    border-radius: 6px;
    padding: 0.4rem 0.9rem;
    font-size: 0.9rem;
    cursor: pointer;
    font-family: inherit;
    margin-top: 0.5rem;
  }
  .secondary-btn:hover {
    background: #46464a;
  }
  .progress-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
  }
  .progress-label,
  .pct-label {
    font-size: 0.78rem;
    color: #7f7f82;
    flex-shrink: 0;
  }
  .bar-wrap {
    flex: 1;
    height: 4px;
    background: #2a2a2c;
    border-radius: 2px;
    overflow: hidden;
  }
  .bar-fill {
    height: 100%;
    background: #5a8cff;
    border-radius: 2px;
    transition: width 0.3s ease;
  }
  .log {
    background: #131315;
    border: 1px solid #2a2a2c;
    border-radius: 6px;
    padding: 0.6rem 0.75rem;
    max-height: 140px;
    overflow-y: auto;
    margin-top: 0.75rem;
  }
  .log-line {
    margin: 0;
    padding: 0.15rem 0;
    font-size: 0.8rem;
    font-family: "SF Mono", ui-monospace, monospace;
    color: #9a9a9e;
    line-height: 1.5;
  }
  .done-label {
    font-size: 0.9rem;
    color: #50c87a;
    margin: 0 0 0.75rem 0;
  }
  .flagged-block {
    background: #1a1510;
    border: 1px solid #3a2a10;
    border-radius: 8px;
    padding: 0.75rem;
    margin-bottom: 0.75rem;
  }
  .flagged-header {
    font-size: 0.8rem;
    color: #f0c850;
    font-weight: 600;
    margin: 0 0 0.5rem 0;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .flagged-row {
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
    padding: 0.25rem 0;
    font-size: 0.82rem;
    border-top: 1px solid #2a2a2c;
  }
  .flagged-row:first-of-type {
    border-top: none;
  }
  .beat-badge {
    font-size: 0.7rem;
    padding: 0.1rem 0.35rem;
    border-radius: 3px;
    color: #ccc;
    flex-shrink: 0;
    font-family: "SF Mono", ui-monospace, monospace;
  }
  .bite-label {
    flex: 1;
    color: #c8c8cc;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .bite-tc {
    font-family: "SF Mono", ui-monospace, monospace;
    font-size: 0.75rem;
    color: #7f7f82;
    flex-shrink: 0;
  }
  .bite-score {
    font-size: 0.75rem;
    color: #f0c850;
    flex-shrink: 0;
  }
  .error-msg {
    color: #f28b82;
    font-size: 0.9rem;
    margin: 0 0 0.75rem 0;
  }
</style>

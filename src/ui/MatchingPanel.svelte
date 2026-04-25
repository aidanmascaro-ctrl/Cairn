<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import type { CairnBridge, MatchingProgressEvent } from "./types";

  export let ingestFolder: string | null = null;

  const cairn: CairnBridge | undefined = (window as unknown as { cairn?: CairnBridge }).cairn;

  type State = "idle" | "estimating" | "ready" | "running" | "done" | "error";

  let state: State = "idle";
  let estimateBites = 0;
  let estimateBroll = 0;
  let estimateCost = 0;
  let errorMsg = "";
  let progressLines: string[] = [];
  let processedBites = 0;
  let totalBites = 0;
  let totalMatches = 0;
  let rejectedMatches = 0;
  let uncoveredBites: string[] = [];
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
      const result = await cairn.matching.estimate({ path: ingestFolder });
      estimateBites = result.soundbite_count;
      estimateBroll = result.broll_clip_count;
      estimateCost = result.estimated_cost_usd;
      state = estimateBites === 0 ? "idle" : "ready";
    } catch (err) {
      errorMsg = err instanceof Error ? err.message : String(err);
      state = "error";
    }
  }

  async function runMatching() {
    if (!cairn || !ingestFolder || state !== "ready") return;
    state = "running";
    progressLines = [];
    processedBites = 0;
    uncoveredBites = [];

    const cleanup = (unsub = cairn.matching.onProgress((ev: MatchingProgressEvent) => {
      if (ev.event === "matching_started") {
        totalBites = ev.total_soundbites ?? estimateBites;
        progressLines = [
          ...progressLines,
          `Starting — ${totalBites} soundbites across ${ev.broll_clips ?? estimateBroll} B-roll clips`,
        ];
      } else if (ev.event === "soundbite_matched") {
        processedBites += 1;
        const a = ev.accepted ?? 0;
        const r = ev.rejected ?? 0;
        const sid = ev.soundbite_id ?? "";
        progressLines = [
          ...progressLines,
          `✓ ${sid} — ${a} match${a !== 1 ? "es" : ""}${r > 0 ? ` (${r} dropped)` : ""}`,
        ];
      } else if (ev.event === "soundbite_error") {
        const sid = ev.soundbite_id ?? "";
        progressLines = [...progressLines, `✗ ${sid} — ${ev.reason ?? "error"}`];
      } else if (ev.event === "matching_error") {
        errorMsg = ev.reason ?? "Unknown error";
        state = "error";
      }
    }));

    try {
      const result = await cairn.matching.run({ path: ingestFolder });
      totalMatches = result.total_matches;
      rejectedMatches = result.rejected;
      uncoveredBites = result.uncovered_soundbites;
      state = "done";
    } catch (err) {
      errorMsg = err instanceof Error ? err.message : String(err);
      state = "error";
    } finally {
      cleanup();
      unsub = null;
    }
  }

  $: progressPct = totalBites > 0 ? Math.round((processedBites / totalBites) * 100) : 0;
</script>

{#if ingestFolder !== null}
  <section class="card">
    <h2>B-roll Matching</h2>

    {#if state === "idle" || state === "estimating"}
      <p class="hint">
        {state === "estimating" ? "Scanning clips…" : "No soundbites ready for matching."}
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
        {estimateBites} soundbite{estimateBites !== 1 ? "s" : ""} × {estimateBroll} B-roll clip{estimateBroll !==
        1
          ? "s"
          : ""} · est. <span class="cost">${estimateCost.toFixed(3)}</span>
      </p>
      <button class="primary-btn" on:click={runMatching}>Match B-roll</button>
    {:else if state === "running"}
      <div class="progress-row">
        <span class="progress-label">{processedBites}/{totalBites}</span>
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
        ✓ {totalMatches} match{totalMatches !== 1 ? "es" : ""} across {processedBites} soundbite{processedBites !==
        1
          ? "s"
          : ""}
        {rejectedMatches > 0 ? `· ${rejectedMatches} dropped by judge` : ""}
      </p>

      {#if uncoveredBites.length > 0}
        <div class="uncovered-block">
          <p class="uncovered-header">
            {uncoveredBites.length} soundbite{uncoveredBites.length !== 1 ? "s" : ""} with no B-roll match
          </p>
          {#each uncoveredBites as bid (bid)}
            <p class="uncovered-id">{bid}</p>
          {/each}
          <p class="uncovered-hint">Consider shooting additional B-roll for these beats.</p>
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
  .uncovered-block {
    background: #1a1015;
    border: 1px solid #3a1020;
    border-radius: 8px;
    padding: 0.75rem;
    margin-bottom: 0.75rem;
  }
  .uncovered-header {
    font-size: 0.8rem;
    color: #f28b82;
    font-weight: 600;
    margin: 0 0 0.4rem 0;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .uncovered-id {
    margin: 0.1rem 0;
    font-size: 0.8rem;
    font-family: "SF Mono", ui-monospace, monospace;
    color: #c8a0a0;
  }
  .uncovered-hint {
    margin: 0.5rem 0 0 0;
    font-size: 0.78rem;
    color: #7f7f82;
  }
  .error-msg {
    color: #f28b82;
    font-size: 0.9rem;
    margin: 0 0 0.75rem 0;
  }
</style>

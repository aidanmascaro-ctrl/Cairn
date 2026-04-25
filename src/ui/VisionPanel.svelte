<script lang="ts">
  import { afterUpdate, onDestroy } from "svelte";
  import type {
    CairnBridge,
    VisionProgressEvent,
    VisionResult,
    VisionEstimateResult,
    VisionJudgeResult,
  } from "./types";

  export let ingestFolder: string | null = null;

  const cairn: CairnBridge | undefined = (window as unknown as { cairn?: CairnBridge }).cairn;

  type State = "idle" | "estimating" | "ready" | "running" | "done" | "error";

  let state: State = "idle";
  let estimate: VisionEstimateResult | null = null;
  let cloudAllowed = true;
  let total = 0;
  let processed = 0;
  let progressLines: string[] = [];
  let visionResult: VisionResult | null = null;
  let errorMsg = "";

  interface FlaggedClip {
    file: string;
    judge: VisionJudgeResult;
    lowFields: string[];
  }
  let flaggedClips: FlaggedClip[] = [];

  let progressLogEl: HTMLDivElement | undefined;
  let unsubProgress: (() => void) | null = null;

  afterUpdate(() => {
    if (progressLogEl !== undefined) {
      progressLogEl.scrollTop = progressLogEl.scrollHeight;
    }
  });

  onDestroy(() => {
    unsubProgress?.();
  });

  $: progressPct = total > 0 ? Math.round((processed / total) * 100) : 0;

  async function loadEstimate() {
    if (!cairn || !ingestFolder || state === "estimating") return;
    state = "estimating";
    estimate = null;
    try {
      estimate = await cairn.vision.estimate({ path: ingestFolder });
      state = "ready";
    } catch (err) {
      errorMsg = err instanceof Error ? err.message : String(err);
      state = "error";
    }
  }

  async function runVision() {
    if (!cairn || !ingestFolder || state === "running") return;
    state = "running";
    total = estimate?.clip_count ?? 0;
    processed = 0;
    progressLines = [];
    visionResult = null;
    flaggedClips = [];
    errorMsg = "";

    unsubProgress = cairn.vision.onProgress((ev: VisionProgressEvent) => {
      if (ev.event === "vision_started") {
        total = ev.total_clips ?? total;
      } else if (
        ev.event === "clip_tagged" ||
        ev.event === "clip_skipped" ||
        ev.event === "clip_error"
      ) {
        processed++;
        progressLines = [...progressLines, formatProgress(ev)];
      } else if (ev.event === "judge_flagged" && ev.file && ev.judge) {
        flaggedClips = [
          ...flaggedClips,
          { file: ev.file, judge: ev.judge, lowFields: ev.low_fields ?? [] },
        ];
      }
    });

    try {
      visionResult = await cairn.vision.run({
        path: ingestFolder,
        cloud_allowed: cloudAllowed,
      });
      state = "done";
    } catch (err) {
      errorMsg = err instanceof Error ? err.message : String(err);
      state = "error";
    } finally {
      unsubProgress();
      unsubProgress = null;
    }
  }

  function formatProgress(ev: VisionProgressEvent): string {
    const filename = ev.file ? (ev.file.split("/").pop() ?? ev.file) : "";
    if (ev.event === "clip_tagged") {
      const tags = ev.tags;
      const summary = tags
        ? `${tags.shot_type}  ${tags.subjects.slice(0, 2).join(", ")}  conf:${tags.confidence.toFixed(2)}`
        : "";
      return `✓ ${filename}  ${summary}`;
    }
    if (ev.event === "clip_skipped") return `⏭ ${filename}  (already tagged)`;
    if (ev.event === "clip_error") return `✗ ${filename}  ${ev.reason ?? ""}`;
    return ev.event;
  }

  function reset() {
    state = "idle";
    estimate = null;
    progressLines = [];
    visionResult = null;
    flaggedClips = [];
    errorMsg = "";
    total = 0;
    processed = 0;
  }

  $: costLabel =
    estimate && cloudAllowed
      ? `~$${estimate.estimated_cost_usd.toFixed(2)} USD`
      : "Local — not available in v1";
</script>

{#if ingestFolder !== null}
  <section class="card">
    <h2>Index B-roll</h2>

    {#if state === "idle"}
      <button class="secondary-btn" on:click={loadEstimate} disabled={!cairn}>
        Check B-roll clips…
      </button>
    {:else if state === "estimating"}
      <p class="hint">Scanning for B-roll clips…</p>
    {:else if state === "ready" && estimate !== null}
      {#if estimate.clip_count === 0}
        <p class="hint">No untagged B-roll clips found — run Classify first.</p>
        <button class="secondary-btn" on:click={reset}>Back</button>
      {:else}
        <div class="estimate-block">
          <p class="estimate-line">
            <strong>{estimate.clip_count}</strong> B-roll clip{estimate.clip_count !== 1 ? "s" : ""}
            &nbsp;·&nbsp;
            <span class="cost">{costLabel}</span>
          </p>
          <label class="toggle-label">
            <input type="checkbox" bind:checked={cloudAllowed} />
            Use Claude vision (cloud)
          </label>
          {#if !cloudAllowed}
            <p class="local-note">Local vision not available in v1 — enable cloud to proceed.</p>
          {/if}
        </div>
        <div class="btn-row">
          <button class="secondary-btn" on:click={reset}>Cancel</button>
          <button class="vision-btn" on:click={runVision} disabled={!cairn || !cloudAllowed}>
            Tag with Claude
          </button>
        </div>
      {/if}
    {:else if state === "running"}
      <p class="running-hint">
        Tagging clip {processed} of {total > 0 ? String(total) : "?"}…
      </p>
      <div class="progress-bar-wrap">
        <div class="progress-bar-fill" style="width: {progressPct}%"></div>
      </div>
      {#if progressLines.length > 0}
        <div class="progress-log" bind:this={progressLogEl}>
          {#each progressLines as line, i (i)}
            <div class="log-line">{line}</div>
          {/each}
        </div>
      {/if}
    {:else if state === "done" && visionResult !== null}
      <div class="summary">
        <span class="chip ok">{visionResult.tagged} tagged</span>
        {#if visionResult.skipped > 0}
          <span class="chip skipped">{visionResult.skipped} skipped</span>
        {/if}
        {#if visionResult.errors > 0}
          <span class="chip error">{visionResult.errors} errors</span>
        {/if}
        {#if visionResult.judge_samples > 0}
          <span class="chip judge">{visionResult.judge_samples} judge-reviewed</span>
        {/if}
      </div>

      {#if flaggedClips.length > 0}
        <div class="flagged-block">
          <h3>Low-confidence tags</h3>
          <p class="hint">
            The judge reviewer flagged these clips — check that tags reflect what's visible.
          </p>
          {#each flaggedClips as fc (fc.file)}
            <div class="flag-row">
              <span class="flag-file">{fc.file.split("/").pop()}</span>
              <span class="flag-score">
                overall {fc.judge.overall_score.toFixed(2)}
              </span>
              {#if fc.lowFields.length > 0}
                <span class="flag-fields">low: {fc.lowFields.join(", ")}</span>
              {/if}
            </div>
          {/each}
        </div>
      {/if}

      <button class="secondary-btn reset-btn" on:click={reset}>Re-index</button>
    {:else if state === "error"}
      <p class="error-msg">✗ {errorMsg}</p>
      <button class="secondary-btn" on:click={reset}>Try again</button>
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
    line-height: 1.5;
  }
  .estimate-block {
    margin-bottom: 1rem;
  }
  .estimate-line {
    font-size: 0.9rem;
    color: #c8c8cc;
    margin: 0 0 0.6rem 0;
  }
  .cost {
    color: #f0c850;
  }
  .toggle-label {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.88rem;
    color: #b0b0b3;
    cursor: pointer;
    user-select: none;
  }
  .toggle-label input[type="checkbox"] {
    accent-color: #5a8cff;
  }
  .local-note {
    font-size: 0.82rem;
    color: #7f7f82;
    margin: 0.4rem 0 0 1.4rem;
  }
  .btn-row {
    display: flex;
    gap: 0.5rem;
    align-items: center;
  }
  .vision-btn {
    background: #2a4a2a;
    color: #a8d8a8;
    border: 1px solid #3a6a3a;
    border-radius: 6px;
    padding: 0.4rem 1rem;
    font-size: 0.9rem;
    cursor: pointer;
  }
  .vision-btn:hover:not(:disabled) {
    background: #335533;
  }
  .vision-btn:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .secondary-btn {
    background: #3a3a3d;
    color: inherit;
    border: 1px solid #4a4a4d;
    border-radius: 6px;
    padding: 0.4rem 0.9rem;
    font-size: 0.9rem;
    cursor: pointer;
  }
  .secondary-btn:hover:not(:disabled) {
    background: #46464a;
  }
  .secondary-btn:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .running-hint {
    color: #9a9a9f;
    font-size: 0.88rem;
    margin: 0 0 0.6rem 0;
  }
  .progress-bar-wrap {
    height: 6px;
    background: #2a2a2c;
    border-radius: 3px;
    overflow: hidden;
    margin-bottom: 0.75rem;
  }
  .progress-bar-fill {
    height: 100%;
    background: #50c87a;
    border-radius: 3px;
    transition: width 0.2s ease;
  }
  .progress-log {
    background: #131315;
    border: 1px solid #2a2a2c;
    border-radius: 6px;
    padding: 0.6rem 0.75rem;
    max-height: 180px;
    overflow-y: auto;
    font-family: "SF Mono", ui-monospace, monospace;
    font-size: 0.77rem;
  }
  .log-line {
    color: #a0a0a3;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-all;
  }
  .summary {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
    margin-bottom: 1rem;
  }
  .chip {
    padding: 0.25rem 0.65rem;
    border-radius: 99px;
    font-size: 0.82rem;
    font-weight: 600;
  }
  .chip.ok {
    background: rgba(80, 200, 120, 0.12);
    color: #7dd4a0;
    border: 1px solid rgba(80, 200, 120, 0.25);
  }
  .chip.skipped {
    background: rgba(160, 160, 160, 0.1);
    color: #7f7f82;
    border: 1px solid #3a3a3d;
  }
  .chip.error {
    background: rgba(242, 139, 130, 0.12);
    color: #f28b82;
    border: 1px solid rgba(242, 139, 130, 0.25);
  }
  .chip.judge {
    background: rgba(160, 120, 200, 0.12);
    color: #c8a8e8;
    border: 1px solid rgba(160, 120, 200, 0.25);
  }
  .flagged-block {
    background: #181819;
    border: 1px solid #3a2a2a;
    border-radius: 8px;
    padding: 0.9rem 1rem;
    margin-bottom: 1rem;
  }
  .flagged-block h3 {
    margin: 0 0 0.35rem 0;
    font-size: 0.92rem;
    color: #e8c888;
    font-weight: 500;
  }
  .flag-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-top: 0.5rem;
    font-size: 0.82rem;
  }
  .flag-file {
    font-family: "SF Mono", ui-monospace, monospace;
    color: #c8c8cc;
    flex-shrink: 0;
    max-width: 200px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .flag-score {
    color: #f0c850;
    flex-shrink: 0;
  }
  .flag-fields {
    color: #f28b82;
    font-size: 0.78rem;
  }
  .reset-btn {
    margin-top: 0.25rem;
  }
  .error-msg {
    color: #f28b82;
    font-size: 0.9rem;
    margin: 0 0 0.75rem 0;
    word-break: break-all;
  }
  button {
    font-family: inherit;
  }
</style>

<script lang="ts">
  import { afterUpdate } from "svelte";
  import type { CairnBridge, ClassifyProgressEvent, ClassifyResult, ClassifyLabel } from "./types";

  export let ingestFolder: string | null = null;

  const cairn: CairnBridge | undefined = (window as unknown as { cairn?: CairnBridge }).cairn;

  type State = "idle" | "running" | "done" | "error";

  let state: State = "idle";
  let total = 0;
  let processed = 0;
  let progressLines: string[] = [];
  let classifyResult: ClassifyResult | null = null;
  let errorMsg = "";

  interface AmbiguousClip {
    path: string;
    filename: string;
    durationSec: number;
    faceRatio: number;
    thumbnailUrl: string | null;
    confirmed: ClassifyLabel | null;
    confirming: boolean;
  }

  let ambiguousClips: AmbiguousClip[] = [];

  let progressLogEl: HTMLDivElement | undefined;

  afterUpdate(() => {
    if (progressLogEl !== undefined) {
      progressLogEl.scrollTop = progressLogEl.scrollHeight;
    }
  });

  $: progressPct = total > 0 ? Math.round((processed / total) * 100) : 0;
  $: pendingReview = ambiguousClips.filter((c) => c.confirmed === null).length;

  async function runClassify() {
    if (!cairn || !ingestFolder || state === "running") return;
    state = "running";
    total = 0;
    processed = 0;
    progressLines = [];
    classifyResult = null;
    ambiguousClips = [];
    errorMsg = "";

    const unsub = cairn.classify.onProgress((ev: ClassifyProgressEvent) => {
      if (ev.event === "classify_started") {
        total = ev.total_clips ?? 0;
      } else if (ev.event === "clip_classified" || ev.event === "clip_skipped") {
        processed++;
        if (ev.event === "clip_classified" && ev.label === "AMBIGUOUS" && ev.file) {
          ambiguousClips = [
            ...ambiguousClips,
            {
              path: ev.file,
              filename: ev.file.split("/").pop() ?? ev.file,
              durationSec: ev.duration_sec ?? 0,
              faceRatio: ev.face_ratio ?? 0,
              thumbnailUrl: null,
              confirmed: null,
              confirming: false,
            },
          ];
        }
        progressLines = [...progressLines, formatProgress(ev)];
      }
    });

    try {
      classifyResult = await cairn.classify.run({ path: ingestFolder });
      state = "done";
      void loadThumbnails();
    } catch (err) {
      errorMsg = err instanceof Error ? err.message : String(err);
      state = "error";
    } finally {
      unsub();
    }
  }

  async function loadThumbnails() {
    if (!cairn) return;
    for (let i = 0; i < ambiguousClips.length; i++) {
      try {
        const res = await cairn.classify.thumbnail(ambiguousClips[i].path);
        if (res.dataUrl) {
          ambiguousClips[i] = { ...ambiguousClips[i], thumbnailUrl: res.dataUrl };
          ambiguousClips = [...ambiguousClips];
        }
      } catch {
        // thumbnail failed — show placeholder
      }
    }
  }

  async function confirmClip(clipPath: string, label: ClassifyLabel) {
    if (!cairn || !ingestFolder) return;
    const idx = ambiguousClips.findIndex((c) => c.path === clipPath);
    if (idx === -1) return;
    ambiguousClips[idx] = { ...ambiguousClips[idx], confirming: true };
    ambiguousClips = [...ambiguousClips];
    try {
      await cairn.classify.confirm({ clip_path: clipPath, label, shoot_folder: ingestFolder });
      ambiguousClips[idx] = { ...ambiguousClips[idx], confirmed: label, confirming: false };
    } catch {
      ambiguousClips[idx] = { ...ambiguousClips[idx], confirming: false };
    }
    ambiguousClips = [...ambiguousClips];
  }

  function formatProgress(ev: ClassifyProgressEvent): string {
    const filename = ev.file ? (ev.file.split("/").pop() ?? ev.file) : "";
    if (ev.event === "clip_classified") {
      const dur = ev.duration_sec != null ? `${ev.duration_sec.toFixed(0)}s`.padStart(5) : "   —";
      const face =
        ev.face_ratio != null
          ? `face ${(ev.face_ratio * 100).toFixed(0)}%`.padStart(9)
          : "        ";
      const badge = (ev.label ?? "").padEnd(9);
      return `${badge} ${dur} ${face}  ${filename}`;
    }
    if (ev.event === "clip_skipped") {
      return `SKIPPED                  ${filename}`;
    }
    return ev.event;
  }

  function reset() {
    state = "idle";
    progressLines = [];
    classifyResult = null;
    ambiguousClips = [];
    errorMsg = "";
    total = 0;
    processed = 0;
  }

  function labelColor(label: ClassifyLabel | null): string {
    if (label === "A-ROLL") return "badge-aroll";
    if (label === "B-ROLL") return "badge-broll";
    if (label === "AMBIGUOUS") return "badge-ambiguous";
    return "";
  }
</script>

{#if ingestFolder !== null}
  <section class="card">
    <h2>Classify</h2>

    {#if state === "idle"}
      <p class="hint">Ready to classify video clips in<br /><code>{ingestFolder}</code></p>
      <button class="classify-btn" on:click={runClassify} disabled={!cairn}>Classify clips</button>
    {:else if state === "running"}
      <p class="running-hint">
        Classifying clip {processed} of {total > 0 ? String(total) : "?"}…
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
    {:else if state === "done" && classifyResult !== null}
      <div class="summary">
        <span class="chip aroll">{classifyResult.a_roll} A-roll</span>
        <span class="chip broll">{classifyResult.b_roll} B-roll</span>
        <span class="chip ambiguous">{classifyResult.ambiguous} Ambiguous</span>
        {#if classifyResult.skipped > 0}
          <span class="chip skipped">{classifyResult.skipped} skipped</span>
        {/if}
      </div>

      {#if ambiguousClips.length > 0}
        <div class="review-header">
          <h3>
            Review ambiguous clips {pendingReview > 0
              ? `— ${String(pendingReview)} remaining`
              : "— all resolved"}
          </h3>
        </div>
        <div class="review-list">
          {#each ambiguousClips as clip (clip.path)}
            <div class="review-item" class:resolved={clip.confirmed !== null}>
              <div class="thumb-wrap">
                {#if clip.thumbnailUrl}
                  <img src={clip.thumbnailUrl} alt={clip.filename} class="thumb" />
                {:else}
                  <div class="thumb-placeholder">▶</div>
                {/if}
              </div>
              <div class="clip-info">
                <p class="clip-name">{clip.filename}</p>
                <p class="clip-meta">
                  {clip.durationSec.toFixed(0)}s · face {(clip.faceRatio * 100).toFixed(0)}%
                </p>
                {#if clip.confirmed !== null}
                  <span class="badge {labelColor(clip.confirmed)}">{clip.confirmed}</span>
                {:else}
                  <div class="confirm-btns">
                    <button
                      class="btn-aroll"
                      disabled={clip.confirming}
                      on:click={() => confirmClip(clip.path, "A-ROLL")}
                    >
                      A-roll ▲
                    </button>
                    <button
                      class="btn-broll"
                      disabled={clip.confirming}
                      on:click={() => confirmClip(clip.path, "B-ROLL")}
                    >
                      B-roll ▼
                    </button>
                  </div>
                {/if}
              </div>
            </div>
          {/each}
        </div>
      {/if}

      <button class="reset-btn" on:click={reset}>Re-classify</button>
    {:else if state === "error"}
      <p class="error-msg">✗ {errorMsg}</p>
      <button on:click={reset}>Try again</button>
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
    margin: 0 0 0.9rem 0;
    line-height: 1.5;
  }
  code {
    font-family: "SF Mono", ui-monospace, monospace;
    font-size: 0.78rem;
    color: #9a9aaf;
    word-break: break-all;
  }
  .classify-btn {
    background: #2a4a8a;
    color: #d8e8ff;
    border: 1px solid #3a5a9a;
    border-radius: 6px;
    padding: 0.4rem 1rem;
    font-size: 0.9rem;
    cursor: pointer;
  }
  .classify-btn:hover:not(:disabled) {
    background: #335aaa;
  }
  .classify-btn:disabled {
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
    background: #5a8cff;
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
    white-space: pre;
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
  .chip.aroll {
    background: rgba(90, 140, 255, 0.15);
    color: #8ab4ff;
    border: 1px solid rgba(90, 140, 255, 0.3);
  }
  .chip.broll {
    background: rgba(80, 200, 120, 0.12);
    color: #7dd4a0;
    border: 1px solid rgba(80, 200, 120, 0.25);
  }
  .chip.ambiguous {
    background: rgba(240, 190, 70, 0.12);
    color: #f0c850;
    border: 1px solid rgba(240, 190, 70, 0.25);
  }
  .chip.skipped {
    background: rgba(160, 160, 160, 0.1);
    color: #7f7f82;
    border: 1px solid #3a3a3d;
  }
  .review-header h3 {
    margin: 0 0 0.75rem 0;
    font-size: 0.92rem;
    color: #b9b9b9;
    font-weight: 500;
  }
  .review-list {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    margin-bottom: 1rem;
  }
  .review-item {
    display: flex;
    gap: 0.75rem;
    align-items: flex-start;
    background: #181819;
    border: 1px solid #2a2a2c;
    border-radius: 8px;
    padding: 0.6rem 0.75rem;
    transition: border-color 0.15s;
  }
  .review-item.resolved {
    border-color: #1e3a1e;
    opacity: 0.7;
  }
  .thumb-wrap {
    flex-shrink: 0;
    width: 80px;
    height: 48px;
    border-radius: 4px;
    overflow: hidden;
    background: #2a2a2c;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .thumb {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .thumb-placeholder {
    color: #4a4a4d;
    font-size: 1.2rem;
  }
  .clip-info {
    flex: 1;
    min-width: 0;
  }
  .clip-name {
    margin: 0 0 0.2rem 0;
    font-size: 0.85rem;
    font-family: "SF Mono", ui-monospace, monospace;
    color: #c8c8cc;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .clip-meta {
    margin: 0 0 0.4rem 0;
    font-size: 0.8rem;
    color: #7f7f82;
  }
  .confirm-btns {
    display: flex;
    gap: 0.4rem;
  }
  .confirm-btns button {
    padding: 0.25rem 0.6rem;
    font-size: 0.8rem;
    border-radius: 5px;
    cursor: pointer;
    border: 1px solid;
  }
  .btn-aroll {
    background: rgba(90, 140, 255, 0.15);
    color: #8ab4ff;
    border-color: rgba(90, 140, 255, 0.35) !important;
  }
  .btn-aroll:hover:not(:disabled) {
    background: rgba(90, 140, 255, 0.28);
  }
  .btn-broll {
    background: rgba(80, 200, 120, 0.12);
    color: #7dd4a0;
    border-color: rgba(80, 200, 120, 0.3) !important;
  }
  .btn-broll:hover:not(:disabled) {
    background: rgba(80, 200, 120, 0.22);
  }
  .confirm-btns button:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .badge {
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.04em;
  }
  .badge-aroll {
    background: rgba(90, 140, 255, 0.2);
    color: #8ab4ff;
  }
  .badge-broll {
    background: rgba(80, 200, 120, 0.18);
    color: #7dd4a0;
  }
  .badge-ambiguous {
    background: rgba(240, 190, 70, 0.18);
    color: #f0c850;
  }
  .reset-btn,
  button {
    background: #3a3a3d;
    color: inherit;
    border: 1px solid #4a4a4d;
    border-radius: 6px;
    padding: 0.4rem 0.9rem;
    font-size: 0.9rem;
    cursor: pointer;
  }
  .reset-btn:hover,
  button:hover:not(:disabled) {
    background: #46464a;
  }
  button:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .error-msg {
    color: #f28b82;
    font-size: 0.9rem;
    margin: 0 0 0.75rem 0;
    word-break: break-all;
  }
</style>

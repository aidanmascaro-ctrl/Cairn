<script lang="ts">
  import { afterUpdate, createEventDispatcher } from "svelte";
  import type { CairnBridge, ClassifyProgressEvent, ClassifyResult, ClassifyLabel } from "./types";
  import ClassifyInspector from "./ClassifyInspector.svelte";

  export let ingestFolder: string | null = null;

  const dispatch = createEventDispatcher<{
    pendingChange: { count: number };
    folderPicked: { folder: string };
  }>();

  const cairn: CairnBridge | undefined = (window as unknown as { cairn?: CairnBridge }).cairn;

  // activeFolder may be set by the parent (after ingest) or by the user via folder picker.
  let activeFolder: string | null = null;
  $: if (ingestFolder !== null) activeFolder = ingestFolder;

  async function pickFolder() {
    if (!cairn) return;
    const picked = await cairn.openFolder();
    if (picked) {
      activeFolder = picked;
      dispatch("folderPicked", { folder: picked });
    }
  }

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
    motionScore: number;
    thumbnailUrl: string | null;
    thumbnailLoading: boolean;
    thumbnailError: boolean;
    confirmed: ClassifyLabel | null;
    confirming: boolean;
  }

  let ambiguousClips: AmbiguousClip[] = [];

  interface ClipError {
    file: string;
    reason: string;
  }
  let clipErrors: ClipError[] = [];
  let resolveFailCount = 0;
  let sidecarFailCount = 0;
  let showErrorDetail = false;

  let progressLogEl: HTMLDivElement | undefined;

  afterUpdate(() => {
    if (progressLogEl !== undefined) {
      progressLogEl.scrollTop = progressLogEl.scrollHeight;
    }
  });

  $: progressPct = total > 0 ? Math.round((processed / total) * 100) : 0;
  $: pendingReview = ambiguousClips.length;
  $: dispatch("pendingChange", { count: pendingReview });

  async function runClassify() {
    if (!cairn || !activeFolder || state === "running") return;
    state = "running";
    total = 0;
    processed = 0;
    progressLines = [];
    classifyResult = null;
    ambiguousClips = [];
    errorMsg = "";
    clipErrors = [];
    resolveFailCount = 0;
    sidecarFailCount = 0;
    showErrorDetail = false;
    resolvedCount = 0;

    const unsub = cairn.classify.onProgress((ev: ClassifyProgressEvent) => {
      if (ev.event === "classify_started") {
        total = ev.total_clips ?? 0;
      } else if (ev.event === "clip_classified" || ev.event === "clip_skipped") {
        processed++;
        if (ev.event === "clip_classified") {
          if (ev.resolve_ok === false) resolveFailCount++;
          if (ev.sidecar_ok === false) sidecarFailCount++;
          if (ev.label === "AMBIGUOUS" && ev.file) {
            ambiguousClips = [
              ...ambiguousClips,
              {
                path: ev.file,
                filename: ev.file.split("/").pop() ?? ev.file,
                durationSec: ev.duration_sec ?? 0,
                faceRatio: ev.face_ratio ?? 0,
                motionScore: ev.motion_score ?? 0,
                thumbnailUrl: null,
                thumbnailLoading: false,
                thumbnailError: false,
                confirmed: null,
                confirming: false,
              },
            ];
          }
        }
        progressLines = [...progressLines, formatProgress(ev)];
      } else if (ev.event === "clip_error") {
        processed++;
        const filename = ev.file ? (ev.file.split("/").pop() ?? ev.file) : "unknown";
        clipErrors = [...clipErrors, { file: filename, reason: ev.reason ?? "unknown error" }];
        progressLines = [...progressLines, formatProgress(ev)];
      }
    });

    try {
      classifyResult = await cairn.classify.run({ path: activeFolder });
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
    const bridge = cairn;
    if (!bridge) return;
    ambiguousClips = ambiguousClips.map((c) => ({ ...c, thumbnailLoading: true }));
    const results = await Promise.allSettled(
      ambiguousClips.map((c) => bridge.classify.thumbnail(c.path)),
    );
    ambiguousClips = ambiguousClips.map((c, i) => {
      const r = results[i];
      if (r && r.status === "fulfilled" && r.value.dataUrl) {
        return { ...c, thumbnailLoading: false, thumbnailUrl: r.value.dataUrl };
      }
      return { ...c, thumbnailLoading: false, thumbnailError: true };
    });
  }

  async function confirmClip(clipPath: string, label: ClassifyLabel) {
    if (!cairn || !activeFolder) return;
    const idx = ambiguousClips.findIndex((c) => c.path === clipPath);
    if (idx === -1) return;
    ambiguousClips = ambiguousClips.map((c, i) => (i === idx ? { ...c, confirming: true } : c));
    try {
      const result = await cairn.classify.confirm({
        clip_path: clipPath,
        label,
        shoot_folder: activeFolder,
      });
      ambiguousClips = ambiguousClips.filter((c) => c.path !== clipPath);
      resolvedCount++;
      if (classifyResult) {
        const key = label === "A-ROLL" ? "a_roll" : "b_roll";
        classifyResult = {
          ...classifyResult,
          [key]: classifyResult[key] + 1,
          ambiguous: classifyResult.ambiguous - 1,
        };
      }
      if (!result.resolve_ok) resolveFailCount++;
    } catch {
      ambiguousClips = ambiguousClips.map((c, i) => (i === idx ? { ...c, confirming: false } : c));
    }
  }

  function formatProgress(ev: ClassifyProgressEvent): string {
    const filename = ev.file ? (ev.file.split("/").pop() ?? ev.file) : "";
    if (ev.event === "clip_classified") {
      const dur = ev.duration_sec != null ? `${ev.duration_sec.toFixed(0)}s`.padStart(5) : "   —";
      const face =
        ev.face_ratio != null
          ? `face ${(ev.face_ratio * 100).toFixed(0)}%`.padStart(9)
          : "        ";
      const motion = ev.motion_score != null ? `  motion ${ev.motion_score.toFixed(3)}` : "";
      const badge = (ev.label ?? "").padEnd(9);
      return `${badge} ${dur} ${face}${motion}  ${filename}`;
    }
    if (ev.event === "clip_skipped") {
      return `SKIPPED                  ${filename}`;
    }
    if (ev.event === "clip_error") {
      return `ERROR                    ${filename}  ${ev.reason ?? ""}`;
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
    clipErrors = [];
    resolveFailCount = 0;
    sidecarFailCount = 0;
    showErrorDetail = false;
    resolvedCount = 0;
  }

  let resolvedCount = 0;
  let inspectingPath: string | null = null;

  function toggleInspect(path: string): void {
    inspectingPath = inspectingPath === path ? null : path;
  }
</script>

<section class="card">
  <h2>Classify</h2>

  {#if activeFolder === null}
    <p class="hint">Select an organised shoot folder to classify its video clips.</p>
    <button class="classify-btn" on:click={pickFolder} disabled={!cairn}>Browse folder…</button>
  {:else if state === "idle"}
    <p class="hint">Ready to classify video clips in<br /><code>{activeFolder}</code></p>
    <div class="folder-row">
      <button class="classify-btn" on:click={runClassify} disabled={!cairn}>Classify clips</button>
      <button class="change-folder-btn" on:click={pickFolder} disabled={!cairn}
        >Change folder</button
      >
    </div>
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

    {#if clipErrors.length > 0 || resolveFailCount > 0 || sidecarFailCount > 0}
      <div class="warn-summary">
        {#if clipErrors.length > 0}
          <span class="chip chip-error">{clipErrors.length} unreadable</span>
        {/if}
        {#if resolveFailCount > 0}
          <span class="chip chip-warn">{resolveFailCount} Resolve write failed</span>
        {/if}
        {#if sidecarFailCount > 0}
          <span class="chip chip-warn">{sidecarFailCount} sidecar write failed</span>
        {/if}
        {#if clipErrors.length > 0}
          <button
            class="details-btn"
            on:click={() => {
              showErrorDetail = !showErrorDetail;
            }}>{showErrorDetail ? "Hide details ▲" : "Show details ▼"}</button
          >
        {/if}
      </div>
      {#if showErrorDetail}
        <div class="error-detail">
          {#each clipErrors as e (e.file)}
            <div class="error-row">
              <span class="err-file">{e.file}</span>
              <span class="err-reason">{e.reason}</span>
            </div>
          {/each}
        </div>
      {/if}
    {/if}

    {#if ambiguousClips.length > 0}
      <div class="review-header">
        <h3>Review ambiguous clips — {String(pendingReview)} remaining</h3>
      </div>
      <div class="review-list">
        {#each ambiguousClips as clip (clip.path)}
          <div class="review-item">
            <div class="thumb-wrap">
              {#if clip.thumbnailUrl}
                <img src={clip.thumbnailUrl} alt={clip.filename} class="thumb" />
              {:else if clip.thumbnailLoading}
                <div class="thumb-placeholder thumb-loading">…</div>
              {:else if clip.thumbnailError}
                <div class="thumb-placeholder thumb-error">✕</div>
              {:else}
                <div class="thumb-placeholder">▶</div>
              {/if}
            </div>
            <div class="clip-info">
              <p class="clip-name">{clip.filename}</p>
              <p class="clip-meta">
                {clip.durationSec.toFixed(0)}s · face {(clip.faceRatio * 100).toFixed(0)}% · motion {clip.motionScore.toFixed(
                  3,
                )}
              </p>
              <div class="clip-actions">
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
                <button
                  class="inspect-toggle-btn"
                  on:click={() => {
                    toggleInspect(clip.path);
                  }}
                  title="Show classifier signals for this clip"
                >
                  {inspectingPath === clip.path ? "Close ▲" : "Inspect ▼"}
                </button>
              </div>
              {#if inspectingPath === clip.path}
                <ClassifyInspector clipPath={clip.path} />
              {/if}
            </div>
          </div>
        {/each}
      </div>
    {:else if resolvedCount > 0}
      <p class="all-resolved">✓ All {String(resolvedCount)} ambiguous clips resolved</p>
    {/if}

    <button class="reset-btn" on:click={reset}>Re-classify</button>
  {:else if state === "error"}
    <p class="error-msg">✗ {errorMsg}</p>
    <button on:click={reset}>Try again</button>
  {/if}
</section>

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
  .chip.chip-error {
    background: rgba(242, 139, 130, 0.12);
    color: #f28b82;
    border: 1px solid rgba(242, 139, 130, 0.3);
  }
  .chip.chip-warn {
    background: rgba(240, 190, 70, 0.1);
    color: #c8a030;
    border: 1px solid rgba(240, 190, 70, 0.22);
  }
  .warn-summary {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
    align-items: center;
    margin-bottom: 0.75rem;
  }
  .details-btn {
    background: transparent;
    color: #7f7f82;
    border: 1px solid #3a3a3d;
    border-radius: 4px;
    padding: 0.15rem 0.5rem;
    font-size: 0.75rem;
    cursor: pointer;
  }
  .details-btn:hover {
    color: #b9b9b9;
    background: #2a2a2c;
  }
  .error-detail {
    background: #131315;
    border: 1px solid rgba(242, 139, 130, 0.2);
    border-radius: 6px;
    padding: 0.5rem 0.75rem;
    margin-bottom: 0.75rem;
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }
  .error-row {
    display: flex;
    gap: 0.75rem;
    font-size: 0.78rem;
    font-family: "SF Mono", ui-monospace, monospace;
  }
  .err-file {
    color: #f28b82;
    flex-shrink: 0;
    max-width: 40%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .err-reason {
    color: #7f7f82;
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

  .btn-aroll,
  .btn-broll {
    padding: 0.25rem 0.6rem;
    font-size: 0.8rem;
    border-radius: 5px;
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

  .all-resolved {
    font-size: 0.88rem;
    color: #7dd4a0;
    margin: 0 0 1rem 0;
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
  .folder-row {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    flex-wrap: wrap;
  }
  .change-folder-btn {
    background: transparent;
    color: #7f7f82;
    border: 1px solid #3a3a3d;
    border-radius: 6px;
    padding: 0.4rem 0.75rem;
    font-size: 0.82rem;
    cursor: pointer;
  }
  .change-folder-btn:hover:not(:disabled) {
    background: #2a2a2c;
    color: #b9b9b9;
  }
  .thumb-loading {
    color: #5a8cff;
    font-size: 0.9rem;
    animation: pulse 1s infinite;
  }
  .thumb-error {
    color: #f28b82;
    font-size: 1rem;
  }
  @keyframes pulse {
    0%,
    100% {
      opacity: 1;
    }
    50% {
      opacity: 0.4;
    }
  }
  .clip-actions {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    flex-wrap: wrap;
  }
  .inspect-toggle-btn {
    background: transparent;
    color: #5a8cff;
    border: 1px solid rgba(90, 140, 255, 0.3);
    border-radius: 4px;
    padding: 0.18rem 0.5rem;
    font-size: 0.72rem;
    cursor: pointer;
    letter-spacing: 0.02em;
  }
  .inspect-toggle-btn:hover {
    background: rgba(90, 140, 255, 0.1);
  }
</style>

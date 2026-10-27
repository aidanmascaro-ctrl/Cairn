<script lang="ts">
  import type { CairnBridge, ClassifyInspectResult } from "./types";

  export let clipPath: string;

  const cairn: CairnBridge | undefined = (window as unknown as { cairn?: CairnBridge }).cairn;

  let result: ClassifyInspectResult | null = null;
  let loading = false;
  let errorMsg: string | null = null;

  $: if (clipPath) void loadInspect(clipPath);

  async function loadInspect(path: string) {
    if (!cairn) return;
    loading = true;
    errorMsg = null;
    result = null;
    try {
      const res = await cairn.classify.inspect({ clip_path: path });
      if (!res.ok) {
        errorMsg = res.error ?? "Inspect failed";
      } else {
        result = res;
      }
    } catch (err) {
      errorMsg = err instanceof Error ? err.message : String(err);
    } finally {
      loading = false;
    }
  }

  function motionColor(norm: number): string {
    if (norm < 0.33) return "#3ab87a";
    if (norm < 0.67) return "#f0c850";
    return "#f28b82";
  }
</script>

<div class="inspector">
  {#if loading}
    <div class="state-row">
      <span class="spin">◌</span>
      <span class="state-text">Analysing frames…</span>
    </div>
  {:else if errorMsg}
    <div class="state-row error-row">
      <span>✕ {errorMsg}</span>
    </div>
  {:else if result !== null}
    <!-- Filmstrip -->
    <div class="filmstrip">
      {#each result.frames as frame (frame.frame_idx)}
        <div class="film-frame">
          <div class="frame-img-wrap">
            <img src={frame.data_url} alt="Frame at {frame.timestamp_sec}s" class="frame-img" />
            {#if frame.face_count > 0}
              <span class="face-pip" title="{frame.face_count} face(s) detected">●</span>
            {/if}
          </div>
          <div class="frame-footer">
            <span class="ts">{frame.timestamp_sec}s</span>
            <span
              class="motion-dot"
              style="background: {motionColor(
                result.frames.length > 1
                  ? frame.motion_diff / Math.max(...result.frames.map((f) => f.motion_diff), 0.001)
                  : 0,
              )}"
              title="motion diff: {frame.motion_diff}"
            />
          </div>
        </div>
      {/each}
    </div>

    <!-- Signal bars -->
    <div class="signals">
      <div class="sig-row">
        <span class="sig-label">Face</span>
        <div class="sig-track">
          <div class="sig-fill face-fill" style="width: {result.face_ratio * 100}%" />
        </div>
        <span class="sig-value">{(result.face_ratio * 100).toFixed(0)}%</span>
      </div>
      <div class="sig-row">
        <span class="sig-label">Motion</span>
        <div class="sig-track">
          <div
            class="sig-fill motion-fill"
            style="width: {Math.min(result.motion_score * 500, 100)}%"
          />
        </div>
        <span class="sig-value">{result.motion_score.toFixed(3)}</span>
      </div>
    </div>

    <!-- KNN decision -->
    <div class="knn-row">
      <span class="knn-prefix">KNN →</span>
      {#if result.knn_label !== null}
        <span class="knn-badge knn-{result.knn_label.toLowerCase().replace('-', '')}"
          >{result.knn_label}</span
        >
        <span class="knn-conf">{((result.knn_confidence ?? 0) * 100).toFixed(0)}% confidence</span>
      {:else}
        <span class="knn-badge knn-ambiguous">AMBIGUOUS</span>
        <span class="knn-conf">low confidence or &lt;5 training examples</span>
      {/if}
    </div>
  {/if}
</div>

<style>
  .inspector {
    margin-top: 0.6rem;
    border-top: 1px solid var(--hairline);
    padding-top: 0.6rem;
  }
  .state-row {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.8rem;
    color: var(--text-tertiary);
    padding: 0.25rem 0;
  }
  .error-row {
    color: #f28b82;
  }
  .spin {
    display: inline-block;
    animation: spin 1s linear infinite;
    color: #5a8cff;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  .state-text {
    font-style: italic;
  }

  /* Filmstrip */
  .filmstrip {
    display: flex;
    gap: 5px;
    overflow-x: auto;
    margin-bottom: 0.65rem;
    padding-bottom: 2px;
  }
  .film-frame {
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .frame-img-wrap {
    position: relative;
    width: 114px;
    height: 68px;
    border-radius: 3px;
    overflow: hidden;
    border: 1px solid var(--hairline);
    background: var(--inset);
  }
  .frame-img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }
  .face-pip {
    position: absolute;
    top: 3px;
    right: 4px;
    font-size: 0.55rem;
    color: #f0c850;
    line-height: 1;
  }
  .frame-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 2px;
  }
  .ts {
    font-size: 0.68rem;
    font-family: "SF Mono", ui-monospace, monospace;
    color: var(--text-tertiary);
  }
  .motion-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  /* Signal bars */
  .signals {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    margin-bottom: 0.55rem;
  }
  .sig-row {
    display: flex;
    align-items: center;
    gap: 0.45rem;
  }
  .sig-label {
    width: 44px;
    font-size: 0.72rem;
    color: var(--text-tertiary);
    flex-shrink: 0;
  }
  .sig-track {
    flex: 1;
    height: 5px;
    background: var(--hairline);
    border-radius: 3px;
    overflow: hidden;
  }
  .sig-fill {
    height: 100%;
    border-radius: 3px;
    transition: width 0.35s ease;
  }
  .face-fill {
    background: linear-gradient(to right, #3a7aff, #8ab4ff);
  }
  .motion-fill {
    background: linear-gradient(to right, #3ab87a, #f0c850, #f28b82);
  }
  .sig-value {
    width: 34px;
    font-size: 0.7rem;
    font-family: "SF Mono", ui-monospace, monospace;
    color: var(--text-secondary);
    text-align: right;
    flex-shrink: 0;
  }

  /* KNN decision */
  .knn-row {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    font-size: 0.75rem;
  }
  .knn-prefix {
    color: var(--text-tertiary);
  }
  .knn-badge {
    font-weight: 700;
    font-size: 0.7rem;
    letter-spacing: 0.04em;
    padding: 0.1rem 0.42rem;
    border-radius: 4px;
  }
  .knn-aroll {
    color: #8ab4ff;
    background: rgba(90, 140, 255, 0.2);
  }
  .knn-broll {
    color: #7dd4a0;
    background: rgba(80, 200, 120, 0.18);
  }
  .knn-ambiguous {
    color: #f0c850;
    background: rgba(240, 190, 70, 0.18);
  }
  .knn-conf {
    color: var(--text-tertiary);
  }
</style>

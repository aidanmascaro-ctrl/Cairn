<script lang="ts">
  import { afterUpdate, onDestroy } from "svelte";
  import type {
    CairnBridge,
    TranscribeProgressEvent,
    TranscribeResult,
    TranscribeEstimateResult,
  } from "./types";

  export let ingestFolder: string | null = null;

  const cairn: CairnBridge | undefined = (window as unknown as { cairn?: CairnBridge }).cairn;

  type State = "idle" | "estimating" | "ready" | "running" | "done" | "error";

  let state: State = "idle";
  let cloudAllowed = true;
  let estimate: TranscribeEstimateResult | null = null;
  let total = 0;
  let processed = 0;
  let progressLines: string[] = [];
  let transcribeResult: TranscribeResult | null = null;
  let errorMsg = "";

  // Speaker labelling
  interface SpeakerEntry {
    id: string;
    label: string;
  }
  let speakers: SpeakerEntry[] = [];
  let speakersDirty = false;
  let savingSpeakers = false;

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
      estimate = await cairn.transcribe.estimate({ path: ingestFolder });
      state = "ready";
    } catch (err) {
      errorMsg = err instanceof Error ? err.message : String(err);
      state = "error";
    }
  }

  async function runTranscribe() {
    if (!cairn || !ingestFolder || state === "running") return;
    state = "running";
    total = estimate?.clip_count ?? 0;
    processed = 0;
    progressLines = [];
    transcribeResult = null;
    speakers = [];
    errorMsg = "";

    unsubProgress = cairn.transcribe.onProgress((ev: TranscribeProgressEvent) => {
      if (ev.event === "transcribe_started") {
        total = ev.total_clips ?? total;
      } else if (
        ev.event === "clip_transcribed" ||
        ev.event === "clip_skipped" ||
        ev.event === "clip_error"
      ) {
        processed++;
        progressLines = [...progressLines, formatProgress(ev)];
      }
    });

    try {
      transcribeResult = await cairn.transcribe.run({
        path: ingestFolder,
        cloud_allowed: cloudAllowed,
      });
      state = "done";
      loadSpeakers();
    } catch (err) {
      errorMsg = err instanceof Error ? err.message : String(err);
      state = "error";
    } finally {
      // TypeScript control-flow proves unsubProgress is non-null here (assigned above)
      unsubProgress();
      unsubProgress = null;
    }
  }

  function loadSpeakers() {
    if (!cairn || !ingestFolder) return;
    // Collect unique speaker IDs from sidecars by re-estimating
    // (sidecars now have transcript.speakers populated)
    // For simplicity, we ask the user to label speakers A, B, C, D
    // and show inputs for whatever appeared in the run.
    const speakerIds = new Set<string>();
    progressLines.forEach((line) => {
      const m = line.match(/Speaker ([A-Z])/g);
      if (m) m.forEach((s) => speakerIds.add(s.replace("Speaker ", "")));
    });
    // Common case: A + B for two-person interviews
    if (speakerIds.size === 0) speakerIds.add("A");
    speakers = [...speakerIds].sort().map((id) => ({ id, label: "" }));
  }

  async function saveSpeakerLabels() {
    if (!cairn || !ingestFolder || savingSpeakers) return;
    const labels: Record<string, string> = {};
    for (const s of speakers) {
      if (s.label.trim()) labels[s.id] = s.label.trim();
    }
    if (Object.keys(labels).length === 0) return;
    savingSpeakers = true;
    try {
      await cairn.transcribe.labelSpeakers({ shoot_folder: ingestFolder, speaker_labels: labels });
      speakersDirty = false;
    } catch {
      // non-fatal — labels are in UI state
    } finally {
      savingSpeakers = false;
    }
  }

  function formatProgress(ev: TranscribeProgressEvent): string {
    const filename = ev.file ? (ev.file.split("/").pop() ?? ev.file) : "";
    if (ev.event === "clip_transcribed") {
      const words = ev.word_count != null ? `${String(ev.word_count)}w` : "";
      const utts = ev.utterance_count != null ? `${String(ev.utterance_count)} utts` : "";
      return `✓ ${filename}  ${words}  ${utts}`;
    }
    if (ev.event === "clip_skipped") return `⏭ ${filename}  (already transcribed)`;
    if (ev.event === "clip_error") return `✗ ${filename}  ${ev.reason ?? ""}`;
    return ev.event;
  }

  function reset() {
    state = "idle";
    estimate = null;
    progressLines = [];
    transcribeResult = null;
    speakers = [];
    speakersDirty = false;
    errorMsg = "";
    total = 0;
    processed = 0;
  }

  $: costLabel =
    estimate && cloudAllowed
      ? `~$${estimate.estimated_cost_usd.toFixed(2)} USD (${estimate.estimated_minutes.toFixed(1)} min)`
      : "Local — no API cost";
</script>

{#if ingestFolder !== null}
  <section class="card">
    <h2>Transcribe</h2>

    {#if state === "idle"}
      <button class="secondary-btn" on:click={loadEstimate} disabled={!cairn}>
        Check A-roll clips…
      </button>
    {:else if state === "estimating"}
      <p class="hint">Scanning for A-roll clips…</p>
    {:else if state === "ready" && estimate !== null}
      {#if estimate.clip_count === 0}
        <p class="hint">No A-roll clips found — run Classify first.</p>
        <button class="secondary-btn" on:click={reset}>Back</button>
      {:else}
        <div class="estimate-block">
          <p class="estimate-line">
            <strong>{estimate.clip_count}</strong> A-roll clip{estimate.clip_count !== 1 ? "s" : ""}
            &nbsp;·&nbsp;
            <span class="cost">{costLabel}</span>
          </p>
          <label class="toggle-label">
            <input type="checkbox" bind:checked={cloudAllowed} />
            Use AssemblyAI (speaker-diarised, cloud)
          </label>
          {#if !cloudAllowed}
            <p class="local-note">Local faster-whisper — no speaker labels, no API cost.</p>
          {/if}
        </div>
        <div class="btn-row">
          <button class="secondary-btn" on:click={reset}>Cancel</button>
          <button class="transcribe-btn" on:click={runTranscribe} disabled={!cairn}>
            {cloudAllowed ? "Transcribe with AssemblyAI" : "Transcribe locally"}
          </button>
        </div>
      {/if}
    {:else if state === "running"}
      <p class="running-hint">
        Transcribing clip {processed} of {total > 0 ? String(total) : "?"}…
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
    {:else if state === "done" && transcribeResult !== null}
      <div class="summary">
        <span class="chip ok">{transcribeResult.transcribed} transcribed</span>
        {#if transcribeResult.skipped > 0}
          <span class="chip skipped">{transcribeResult.skipped} skipped</span>
        {/if}
        {#if transcribeResult.errors > 0}
          <span class="chip error">{transcribeResult.errors} errors</span>
        {/if}
      </div>

      {#if speakers.length > 0}
        <div class="speakers-block">
          <h3>Label speakers</h3>
          <p class="hint">Assign names so captions show real names instead of "Speaker A".</p>
          {#each speakers as spk (spk.id)}
            <div class="speaker-row">
              <span class="speaker-id">Speaker {spk.id}</span>
              <input
                type="text"
                class="speaker-input"
                placeholder="e.g. Lauren"
                bind:value={spk.label}
                on:input={() => {
                  speakersDirty = true;
                }}
              />
            </div>
          {/each}
          <button
            class="save-btn"
            disabled={!speakersDirty || savingSpeakers}
            on:click={saveSpeakerLabels}
          >
            {savingSpeakers ? "Saving…" : "Save speaker names"}
          </button>
        </div>
      {/if}

      <button class="secondary-btn reset-btn" on:click={reset}>Re-transcribe</button>
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
  .transcribe-btn {
    background: #2a4a8a;
    color: #d8e8ff;
    border: 1px solid #3a5a9a;
    border-radius: 6px;
    padding: 0.4rem 1rem;
    font-size: 0.9rem;
    cursor: pointer;
  }
  .transcribe-btn:hover:not(:disabled) {
    background: #335aaa;
  }
  .transcribe-btn:disabled {
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
  .speakers-block {
    background: #181819;
    border: 1px solid #2a2a2c;
    border-radius: 8px;
    padding: 0.9rem 1rem;
    margin-bottom: 1rem;
  }
  .speakers-block h3 {
    margin: 0 0 0.35rem 0;
    font-size: 0.92rem;
    color: #b9b9b9;
    font-weight: 500;
  }
  .speaker-row {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    margin-top: 0.5rem;
  }
  .speaker-id {
    font-family: "SF Mono", ui-monospace, monospace;
    font-size: 0.82rem;
    color: #7f7f82;
    width: 72px;
    flex-shrink: 0;
  }
  .speaker-input {
    flex: 1;
    background: #232325;
    border: 1px solid #3a3a3d;
    border-radius: 5px;
    padding: 0.3rem 0.6rem;
    font-size: 0.88rem;
    color: #e6e6e6;
    outline: none;
  }
  .speaker-input:focus {
    border-color: #5a8cff;
  }
  .save-btn {
    margin-top: 0.75rem;
    background: #2a3a5a;
    color: #a8c8ff;
    border: 1px solid #3a4a6a;
    border-radius: 6px;
    padding: 0.35rem 0.8rem;
    font-size: 0.85rem;
    cursor: pointer;
  }
  .save-btn:hover:not(:disabled) {
    background: #354a72;
  }
  .save-btn:disabled {
    opacity: 0.4;
    cursor: default;
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

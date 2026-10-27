<script lang="ts">
  // Stage 5 — Selects. Analyse once, tune instantly, rate, learn, build.
  // Container: owns state and every bridge call; children only render and dispatch.
  import { onDestroy } from "svelte";
  import type {
    CairnBridge,
    SelectRatingValue,
    SelectSegment,
    SelectsBuildResult,
    SelectsClip,
    SelectsLayout,
    SelectsLearnResult,
    SelectsOrder,
    SelectsPreset,
    SelectsProgressEvent,
    SelectsRescoreResult,
    SelectsSettings,
  } from "../types";
  import SelectsTuning from "./SelectsTuning.svelte";
  import SelectsChart from "./SelectsChart.svelte";
  import SelectsClipList from "./SelectsClipList.svelte";
  import SelectsLearn from "./SelectsLearn.svelte";
  import SelectsBuild from "./SelectsBuild.svelte";
  import { SAMPLING_RATES, debounce, fileName, formatDuration } from "./score";

  export let ingestFolder: string | null = null;

  const cairn: CairnBridge | undefined = (window as unknown as { cairn?: CairnBridge }).cairn;
  const RESCORE_DEBOUNCE_MS = 140;

  let folder: string | null = null;
  // React to the ingest folder *changing*, not to `folder` — otherwise picking a
  // different folder would snap straight back to the ingested one.
  let lastIngest: string | null = null;
  function ingestChanged(next: string | null): next is string {
    if (next === null || next === lastIngest) return false;
    lastIngest = next;
    return true;
  }
  // Checked after `analysing`, so a change that lands mid-analysis opens once it ends.
  $: if (!analysing && ingestChanged(ingestFolder)) void openFolder(ingestFolder);

  let presets: SelectsPreset[] = [];
  let settings: SelectsSettings | null = null;
  let scored: SelectsRescoreResult | null = null;
  let error = "";

  let samplingRate = 4;
  let analysing = false;
  let progress = { done: 0, total: 0, failed: 0, current: "", fraction: 0 };
  let unsubProgress: (() => void) | null = null;
  // Kept apart from `error`: a re-score after analysis must not wipe the bad news.
  let analyseNotice = "";
  let clipErrors: { file: string; reason: string }[] = [];

  let rescoring = false;
  let rescoreSeq = 0;
  let selectsOnly = false;
  let focusFile: string | null = null;

  let learning = false;
  let learnResult: SelectsLearnResult | null = null;
  let savedPreset: string | null = null;

  let building = false;
  let buildResult: SelectsBuildResult | null = null;

  $: clips = scored?.clips ?? [];
  $: summary = scored?.summary ?? null;
  $: analysedAny = (summary?.analysed_count ?? 0) > 0;
  $: noClips = summary !== null && summary.clip_count === 0;
  $: defaultName = folder ? `${fileName(folder)} — Cairn Selects` : "";
  $: progressPct = progress.total
    ? Math.round((100 * (progress.done + progress.fraction)) / progress.total)
    : 0;

  /** Read through functions: these can change while a bridge call is in flight. */
  function currentScored(): SelectsRescoreResult | null {
    return scored;
  }
  function currentFolder(): string | null {
    return folder;
  }

  function message(err: unknown): string {
    return err instanceof Error ? err.message : String(err);
  }

  const rescoreSoon = debounce((next: SelectsSettings) => void rescore(next), RESCORE_DEBOUNCE_MS);

  async function openFolder(path: string): Promise<void> {
    rescoreSoon.cancel();
    rescoreSeq += 1; // anything still in flight belongs to the old folder
    folder = path;
    settings = null;
    scored = null;
    learnResult = null;
    buildResult = null;
    analyseNotice = "";
    clipErrors = [];
    error = "";
    if (!cairn) return;
    try {
      const list = (await cairn.selects.presets()).presets;
      if (currentFolder() !== path) return;
      presets = list;
      await rescore();
    } catch (err) {
      if (currentFolder() === path) error = message(err);
    }
  }

  async function pickFolder(): Promise<void> {
    if (analysing) return;
    const picked = await cairn?.openFolder();
    if (picked) await openFolder(picked);
  }

  /**
   * Re-score from stored measurements. Only the newest request may apply its
   * reply, so a late answer for an older slider position or folder is dropped.
   */
  async function rescore(next?: SelectsSettings, presetId?: string): Promise<void> {
    if (!cairn || !folder || analysing) return;
    const f = folder;
    const seq = ++rescoreSeq;
    rescoring = true;
    try {
      const payload = next
        ? { path: f, settings: next }
        : presetId
          ? { path: f, preset_id: presetId }
          : { path: f };
      const res = await cairn.selects.rescore(payload);
      if (seq !== rescoreSeq || currentFolder() !== f) return;
      scored = res;
      settings = res.settings;
      error = "";
    } catch (err) {
      if (seq === rescoreSeq) error = message(err);
    } finally {
      if (seq === rescoreSeq) rescoring = false;
    }
  }

  function onChange(e: CustomEvent<SelectsSettings>): void {
    settings = e.detail;
    buildResult = null;
    rescoreSeq += 1; // a reply for the previous position must not move the slider back
    rescoreSoon(e.detail);
  }

  function onPreset(e: CustomEvent<string>): void {
    if (analysing) return;
    rescoreSoon.cancel();
    buildResult = null;
    void rescore(undefined, e.detail);
  }

  function onApply(e: CustomEvent<SelectsSettings>): void {
    rescoreSoon.cancel();
    learnResult = null;
    void rescore(e.detail);
  }

  function onFocus(e: CustomEvent<string | null>): void {
    focusFile = e.detail;
  }

  function handleProgress(ev: SelectsProgressEvent): void {
    if (ev.event === "analyse_started") {
      progress = { done: 0, total: ev.total_clips ?? 0, failed: 0, current: "", fraction: 0 };
    } else if (ev.event === "clip_started" || ev.event === "clip_progress") {
      progress = { ...progress, current: fileName(ev.file ?? ""), fraction: ev.fraction ?? 0 };
    } else if (ev.event === "clip_analysed" || ev.event === "clip_skipped") {
      progress = { ...progress, done: progress.done + 1, fraction: 0 };
    } else {
      progress = { ...progress, done: progress.done + 1, failed: progress.failed + 1, fraction: 0 };
      clipErrors = [
        ...clipErrors,
        { file: fileName(ev.file ?? ""), reason: ev.reason ?? "unreadable" },
      ];
    }
  }

  async function analyse(force = false): Promise<void> {
    if (!cairn || !folder || analysing) return;
    const f = folder;
    rescoreSoon.cancel();
    analysing = true;
    error = "";
    analyseNotice = "";
    clipErrors = [];
    const unsub = cairn.selects.onProgress(handleProgress);
    unsubProgress = unsub;
    try {
      const res = await cairn.selects.analyse({ path: f, sampling_rate: samplingRate, force });
      if (res.failed > 0) {
        analyseNotice = `${res.failed} of ${res.total} clip${res.total === 1 ? "" : "s"} couldn't be analysed and were left out.`;
      } else if (res.total === 0) {
        analyseNotice = "No video clips found in this folder.";
      }
    } catch (err) {
      analyseNotice = `Analysis stopped: ${message(err)}`;
    } finally {
      unsub();
      unsubProgress = null;
      analysing = false;
    }
    if (currentFolder() === f) await rescore(settings ?? undefined);
  }

  async function onRate(
    e: CustomEvent<{
      clip: SelectsClip;
      segment: SelectSegment;
      rating: SelectRatingValue | null;
      source: "select" | "near_miss";
    }>,
  ): Promise<void> {
    if (!cairn || !folder || !scored || analysing) return;
    const f = folder;
    const { clip, segment, rating, source } = e.detail;
    try {
      const res = await cairn.selects.rate({
        path: f,
        clip_path: clip.file,
        start_sec: segment.start_sec,
        end_sec: segment.end_sec,
        rating,
        source,
      });
      const latest = currentScored();
      if (!latest || currentFolder() !== f) return;
      scored = {
        ...latest,
        clips: latest.clips.map((c) =>
          c.file === res.clip_path ? { ...c, ratings: res.ratings } : c,
        ),
      };
    } catch (err) {
      error = `Couldn't save that rating: ${message(err)}`;
    }
  }

  async function learn(): Promise<void> {
    if (!cairn || !folder || !settings || learning || analysing) return;
    const f = folder;
    learning = true;
    learnResult = null;
    savedPreset = null;
    try {
      const res = await cairn.selects.learn({ path: f, settings });
      if (currentFolder() === f) learnResult = res;
    } catch (err) {
      if (currentFolder() === f) learnResult = { ok: false, error: message(err) };
    } finally {
      learning = false;
    }
  }

  async function savePreset(
    e: CustomEvent<{
      name: string;
      settings: SelectsSettings;
      rationale: string;
      accuracy: number;
    }>,
  ): Promise<void> {
    if (!cairn || analysing) return;
    try {
      const { preset } = await cairn.selects.savePreset({
        name: e.detail.name,
        settings: e.detail.settings,
        description: "Learned from your ratings",
        rationale: e.detail.rationale,
        accuracy: e.detail.accuracy,
      });
      presets = (await cairn.selects.presets()).presets;
      savedPreset = preset.name;
      rescoreSoon.cancel();
      await rescore(undefined, preset.id);
    } catch (err) {
      error = `Couldn't save the preset: ${message(err)}`;
    }
  }

  function failedBuild(reason: string): SelectsBuildResult {
    return {
      ok: false,
      error: reason,
      name: null,
      layout: null,
      fps: null,
      resolution: null,
      select_count: 0,
      piece_count: 0,
      linked_count: 0,
      markers_added: 0,
      clip_markers_added: 0,
      tracks: {},
      missing_paths: [],
      warnings: [],
    };
  }

  async function build(
    e: CustomEvent<{
      layout: SelectsLayout;
      order: SelectsOrder;
      include_markers: boolean;
      clip_markers: boolean;
      name?: string;
    }>,
  ): Promise<void> {
    if (!cairn || !folder || !settings || building || analysing) return;
    const f = folder;
    building = true;
    buildResult = null;
    try {
      const res = await cairn.selects.build({ path: f, settings, ...e.detail });
      if (currentFolder() === f) buildResult = res;
    } catch (err) {
      if (currentFolder() === f) buildResult = failedBuild(message(err));
    } finally {
      building = false;
    }
  }

  onDestroy(() => {
    rescoreSoon.cancel();
    unsubProgress?.();
  });
</script>

<section class="card selects" aria-labelledby="selects-heading">
  <header>
    <h2 id="selects-heading">Selects</h2>
    {#if folder}
      <button
        class="link"
        on:click={pickFolder}
        disabled={analysing}
        title="Change shoot folder ({folder})">{fileName(folder)}</button
      >
    {/if}
  </header>

  {#if !cairn}
    <p class="error" role="alert">
      The plugin bridge didn't load, so Selects can't reach the worker. Restart Resolve and reopen
      Cairn.
    </p>
  {:else if !folder}
    <p class="hint">
      Scores every shot for focus, exposure and motion, then pulls the strongest stretches into a
      selects timeline.
    </p>
    <button class="primary" on:click={pickFolder}>Choose shoot folder</button>
  {:else}
    <div class="analyse">
      {#if analysing}
        <div class="progress">
          <div
            class="bar"
            role="progressbar"
            aria-label="Analysis progress"
            aria-valuemin="0"
            aria-valuemax="100"
            aria-valuenow={progressPct}
            aria-valuetext="{progress.done} of {progress.total} clips"
          >
            <span style="width:{progressPct}%"></span>
          </div>
          <span class="status"
            >Analysing {progress.done}/{progress.total}{progress.current
              ? ` · ${progress.current}`
              : ""}</span
          >
        </div>
      {:else}
        <label class="rate"
          >Sampling
          <select bind:value={samplingRate}>
            {#each SAMPLING_RATES as r (r.value)}<option value={r.value}>{r.label}</option>{/each}
          </select>
        </label>
        <button class:primary={!analysedAny} on:click={() => analyse(false)}>
          {analysedAny ? "Analyse new clips" : "Analyse footage"}
        </button>
        {#if analysedAny}
          <button
            class="ghost"
            on:click={() => analyse(true)}
            title="Re-measure every clip at this rate">Re-analyse all</button
          >
        {/if}
      {/if}
    </div>

    {#if analyseNotice}
      <div class="notice" role="alert">
        <p>{analyseNotice}</p>
        {#if clipErrors.length}
          <ul>
            {#each clipErrors as c, i (i)}<li><code>{c.file}</code> {c.reason}</li>{/each}
          </ul>
        {/if}
      </div>
    {/if}
    {#if error}<p class="error" role="alert">{error}</p>{/if}
    {#each scored?.warnings ?? [] as w (w)}<p class="warn-line">⚠ {w}</p>{/each}

    {#if analysedAny && settings && summary}
      <div class="summary" class:busy={rescoring}>
        <span class="figure">{summary.select_count}</span><span class="unit">selects</span>
        <span class="figure">{formatDuration(summary.selected_sec)}</span><span class="unit"
          >of {formatDuration(summary.footage_sec)}</span
        >
        <span class="figure">{summary.selected_pct}%</span><span class="unit">kept</span>
        <label class="only"><input type="checkbox" bind:checked={selectsOnly} /> Selects only</label
        >
      </div>

      <SelectsChart {clips} threshold={settings.score_threshold_lo} {focusFile} />

      <h3>Tune</h3>
      <SelectsTuning
        {settings}
        {presets}
        disabled={analysing}
        on:change={onChange}
        on:preset={onPreset}
      />

      <h3>Clips</h3>
      <SelectsClipList
        {clips}
        {selectsOnly}
        disabled={analysing}
        on:rate={onRate}
        on:focus={onFocus}
      />

      <h3>Learn my taste</h3>
      <SelectsLearn
        {clips}
        busy={learning || analysing}
        result={learnResult}
        saved={savedPreset}
        on:learn={learn}
        on:apply={onApply}
        on:save={savePreset}
        on:dismiss={() => (learnResult = null)}
      />

      <h3>Build in Resolve</h3>
      <SelectsBuild
        {defaultName}
        selectCount={summary.select_count}
        busy={building || analysing}
        result={buildResult}
        on:build={build}
      />
    {:else if noClips && !analysing}
      <p class="hint">
        No video clips found. Selects looks in <code>01_VIDEOS/</code> (or <code>Footage/</code> for an
        Offload shoot).
      </p>
    {:else if !analysing}
      <p class="hint">
        Analysis decodes each clip once (about five times faster than real time for 4K). After that,
        every change to the settings updates instantly.
      </p>
    {/if}
  {/if}
</section>

<style>
  .selects {
    --sel-text: var(--text, #e6e6e6);
    --sel-muted: #a3a3a8;
    --sel-faint: #6f6f75;
    --sel-line: #2c2c30;
    --sel-line-strong: #45454b;
    --sel-well: #151517;
    --sel-hover: rgba(255, 255, 255, 0.03);
    --sel-accent: #5a8cff;
    --sel-accent-soft: rgba(90, 140, 255, 0.14);
    --sel-accent-text: #d8e4ff;
    --sel-keep: #7fcf93;
    --sel-reject: #f28b82;
    --sel-warn: #f0c850;
  }
  .card {
    background: var(--surface, #1f1f21);
    border: 1px solid var(--hairline, #2a2a2c);
    border-radius: 12px;
    padding: 1.5rem;
    margin-bottom: 1.25rem;
  }
  header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 0.9rem;
  }
  h2 {
    margin: 0;
    font-size: 1.05rem;
    color: var(--text-secondary, #b9b9b9);
    font-weight: 600;
    letter-spacing: 0.02em;
    text-transform: uppercase;
  }
  h3 {
    margin: 1.4rem 0 0.6rem;
    font-size: 0.74rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--sel-faint);
    font-weight: 600;
  }
  .hint {
    color: var(--text-tertiary, #7f7f82);
    font-size: 0.88rem;
    line-height: 1.5;
    margin: 0 0 0.9rem;
  }
  button {
    background: var(--sel-well);
    color: var(--sel-text);
    border: 1px solid var(--sel-line-strong);
    border-radius: 6px;
    padding: 0.4rem 0.9rem;
    font-size: 0.86rem;
    cursor: pointer;
  }
  button:hover {
    border-color: var(--sel-accent);
  }
  button:focus-visible,
  select:focus-visible,
  input:focus-visible {
    outline: 2px solid var(--sel-accent);
    outline-offset: 2px;
  }
  button.primary {
    background: #2a4a8a;
    border-color: #3a5a9a;
    color: #d8e8ff;
  }
  button.ghost {
    background: none;
    border-color: transparent;
    color: var(--sel-muted);
  }
  button.link {
    background: none;
    border: 0;
    padding: 0;
    color: var(--sel-muted);
    font-family: "SF Mono", ui-monospace, monospace;
    font-size: 0.78rem;
    max-width: 60%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  button.link:hover {
    color: var(--sel-text);
  }
  .analyse {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.6rem;
  }
  .rate {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.84rem;
    color: var(--sel-muted);
  }
  select {
    background: var(--sel-well);
    color: var(--sel-text);
    border: 1px solid var(--sel-line);
    border-radius: 6px;
    padding: 0.32rem 0.5rem;
    font-size: 0.82rem;
  }
  .progress {
    flex: 1;
  }
  .bar {
    height: 6px;
    background: var(--sel-well);
    border-radius: 3px;
    overflow: hidden;
  }
  .bar span {
    display: block;
    height: 100%;
    background: var(--sel-accent);
    transition: width 300ms ease;
  }
  .status {
    display: block;
    margin-top: 0.35rem;
    font-size: 0.8rem;
    color: var(--sel-muted);
    font-variant-numeric: tabular-nums;
  }
  .summary {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 0.25rem 0.4rem;
    margin: 1.1rem 0 0.4rem;
    transition: opacity 150ms ease;
  }
  .summary.busy {
    opacity: 0.6;
  }
  .figure {
    font-size: 1.5rem;
    font-weight: 600;
    letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
  }
  .unit {
    color: var(--sel-muted);
    font-size: 0.82rem;
    margin-right: 0.9rem;
  }
  .only {
    margin-left: auto;
    font-size: 0.8rem;
    color: var(--sel-muted);
    display: flex;
    align-items: center;
    gap: 0.35rem;
  }
  .warn-line {
    color: var(--sel-warn);
    font-size: 0.82rem;
    margin: 0.5rem 0 0;
  }
  .notice {
    margin: 0.7rem 0 0;
    padding: 0.55rem 0.75rem;
    border: 1px solid rgba(240, 200, 80, 0.35);
    border-radius: 8px;
    color: var(--sel-warn);
    font-size: 0.84rem;
  }
  .notice p {
    margin: 0;
  }
  .notice ul {
    margin: 0.35rem 0 0;
    padding-left: 1.1rem;
    color: var(--sel-muted);
    font-size: 0.78rem;
  }
  .notice code,
  .hint code {
    font-family: "SF Mono", ui-monospace, monospace;
    font-size: 0.78rem;
  }
  button:disabled {
    opacity: 0.45;
    cursor: default;
  }
  .error {
    color: var(--sel-reject);
    font-size: 0.86rem;
    margin: 0.7rem 0 0;
  }
</style>

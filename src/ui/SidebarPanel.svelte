<script lang="ts" context="module">
  const BEAT_TYPES = [
    { key: "origin-story", label: "Origin" },
    { key: "emotional-peak", label: "Peak" },
    { key: "factual", label: "Factual" },
    { key: "hook", label: "Hook" },
    { key: "quote", label: "Quote" },
    { key: "process-detail", label: "Process" },
    { key: "humour", label: "Humour" },
    { key: "reflection", label: "Reflect" },
    { key: "technical", label: "Tech" },
    { key: "tension", label: "Tension" },
    { key: "resolution", label: "Resolve" },
  ] as const;

  const BEAT_DOT: Record<string, string> = {
    "origin-story": "#50c87a",
    "emotional-peak": "#f28b82",
    factual: "#5a8cff",
    hook: "#f5c877",
    quote: "#4dd0e1",
    "process-detail": "#b39ddb",
    humour: "#f48fb1",
    reflection: "#9fa8da",
    technical: "#81d4fa",
    tension: "#ce93d8",
    resolution: "#80cbc4",
  };

  function dotColor(beatType: string): string {
    return BEAT_DOT[beatType] ?? "#888";
  }

  function basename(p: string): string {
    return p.replace(/.*[/\\]/, "");
  }

  function fmtTc(tc: string): string {
    // Drop trailing :00 frames for readability when zero
    return tc.replace(/:00$/, "");
  }

  function fmtDuration(inSec: number, outSec: number): string {
    const s = Math.round(outSec - inSec);
    return `${s}s`;
  }
</script>

<script lang="ts">
  import { onDestroy } from "svelte";
  import type { CairnBridge, SidebarLoadResult, SidecarRecord, SoundbiteEntry } from "./types";

  export let ingestFolder: string | null = null;

  const cairn: CairnBridge | undefined = (window as unknown as { cairn?: CairnBridge }).cairn;

  interface SoundbiteCtx extends SoundbiteEntry {
    clip_path: string;
    clip_filename: string;
    sidecar_clip_id: string;
    speaker_labels: Record<string, string>;
  }

  type State = "idle" | "loading" | "ready" | "empty" | "error";

  let state: State = "idle";
  let projectState: SidebarLoadResult | null = null;
  let loadedFolder: string | null = null;
  let errorMsg = "";
  let selectedIdx = -1;
  let filterBeatTypes = new Set<string>();
  let filterSearch = "";
  let filterFlagged = false;
  let filterUncovered = false;
  let jumpFeedback: string | null = null;
  let jumpTimer: ReturnType<typeof setTimeout> | null = null;
  let searchEl: HTMLInputElement | null = null;
  let unsub: (() => void) | null = null;

  // ── Derived data ─────────────────────────────────────────────────────────────

  $: allBites = buildAllBites(projectState);
  $: brollById = buildBrollMap(projectState);
  $: filteredBites = applyFilters(
    allBites,
    filterBeatTypes,
    filterSearch,
    filterFlagged,
    filterUncovered,
  );
  $: selectedBite =
    selectedIdx >= 0 && selectedIdx < filteredBites.length
      ? (filteredBites[selectedIdx] ?? null)
      : null;
  $: arollCount = projectState?.sidecars.filter((s) => s.classification === "A-ROLL").length ?? 0;
  $: brollCount = projectState?.sidecars.filter((s) => s.classification === "B-ROLL").length ?? 0;
  $: avgJudge = computeAvgJudge(allBites);

  function buildAllBites(ps: SidebarLoadResult | null): SoundbiteCtx[] {
    if (!ps) return [];
    const result: SoundbiteCtx[] = [];
    for (const sc of ps.sidecars) {
      if (sc.classification !== "A-ROLL" || !sc.soundbites?.length) continue;
      const filename = sc.source_path ? basename(sc.source_path) : sc.clip_id;
      for (const bite of sc.soundbites) {
        result.push({
          ...bite,
          clip_path: sc.source_path ?? "",
          clip_filename: filename,
          sidecar_clip_id: sc.clip_id,
          speaker_labels: sc.speaker_labels ?? {},
        });
      }
    }
    return result.sort((a, b) => a.in_sec - b.in_sec);
  }

  function buildBrollMap(ps: SidebarLoadResult | null): Map<string, SidecarRecord> {
    const m = new Map<string, SidecarRecord>();
    if (!ps) return m;
    for (const sc of ps.sidecars) {
      if (sc.classification === "B-ROLL" && sc.clip_id) m.set(sc.clip_id, sc);
    }
    return m;
  }

  function applyFilters(
    bites: SoundbiteCtx[],
    beatTypes: Set<string>,
    search: string,
    flagged: boolean,
    uncovered: boolean,
  ): SoundbiteCtx[] {
    const q = search.trim().toLowerCase();
    return bites.filter((b) => {
      if (beatTypes.size > 0 && !beatTypes.has(b.beat_type)) return false;
      if (flagged && !b.flagged_for_review && (b.judge_score === undefined || b.judge_score >= 0.6))
        return false;
      if (uncovered && (b.matches?.length ?? 0) > 0) return false;
      if (q) {
        const hay = `${b.label} ${b.extract} ${b.why_usable}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }

  function computeAvgJudge(bites: SoundbiteCtx[]): number | null {
    const scores = bites.map((b) => b.judge_score).filter((s): s is number => s !== undefined);
    if (!scores.length) return null;
    return scores.reduce((a, c) => a + c, 0) / scores.length;
  }

  function brollSubjectsStr(sc: SidecarRecord | undefined): string | null {
    if (!sc || !sc.vision) return null;
    const subs = sc.vision.tags.subjects;
    return subs.length ? subs.join(", ") : null;
  }

  function brollShotTypeStr(sc: SidecarRecord | undefined): string | null {
    if (!sc || !sc.vision) return null;
    return sc.vision.tags.shot_type || null;
  }

  // ── Loading ───────────────────────────────────────────────────────────────────

  $: if (ingestFolder && cairn) void loadProject(ingestFolder);

  async function loadProject(folder: string) {
    if (!cairn) return;
    if (loadedFolder === folder) return;
    if (unsub) {
      unsub();
      unsub = null;
    }
    if (loadedFolder) void cairn.sidebar.unwatch({ path: loadedFolder });

    loadedFolder = folder;
    state = "loading";

    try {
      const result = await cairn.sidebar.load({ path: folder });
      projectState = result;
      selectedIdx = -1;
      state = result.sidecars.length === 0 ? "empty" : "ready";

      await cairn.sidebar.watch({ path: folder });
      unsub = cairn.sidebar.onSidecarChange(({ path: changedPath }) => {
        if (changedPath === folder) {
          void cairn.sidebar.load({ path: folder }).then((fresh) => {
            projectState = fresh;
          });
        }
      });
    } catch (err) {
      errorMsg = err instanceof Error ? err.message : String(err);
      state = "error";
    }
  }

  onDestroy(() => {
    unsub?.();
    if (loadedFolder && cairn) void cairn.sidebar.unwatch({ path: loadedFolder });
    if (jumpTimer) clearTimeout(jumpTimer);
  });

  // ── Actions ───────────────────────────────────────────────────────────────────

  function selectBite(idx: number) {
    selectedIdx = idx;
  }

  async function jumpToSelected() {
    if (!cairn || !selectedBite) return;
    const result = await cairn.resolveExt.jump({ in_tc: selectedBite.in_tc });
    if (jumpTimer) clearTimeout(jumpTimer);
    jumpFeedback = result.ok
      ? `Jumped to ${selectedBite.in_tc}`
      : `Jump failed: ${result.reason ?? "unknown"}`;
    jumpTimer = setTimeout(() => {
      jumpFeedback = null;
    }, 3000);
  }

  async function toggleUsed() {
    if (!cairn || !ingestFolder || !selectedBite) return;
    const used = !(selectedBite.user_state?.used ?? false);
    await cairn.sidebar.markUsed({
      path: ingestFolder,
      soundbite_id: selectedBite.id,
      used,
    });
    // Reload to reflect change
    const fresh = await cairn.sidebar.load({ path: ingestFolder });
    projectState = fresh;
  }

  async function rejectMatch(brollClipId: string) {
    if (!cairn || !ingestFolder || !selectedBite) return;
    await cairn.sidebar.rejectMatch({
      path: ingestFolder,
      broll_clip_id: brollClipId,
      soundbite_id: selectedBite.id,
    });
    const fresh = await cairn.sidebar.load({ path: ingestFolder });
    projectState = fresh;
  }

  function toggleBeatType(key: string) {
    if (filterBeatTypes.has(key)) {
      filterBeatTypes = new Set([...filterBeatTypes].filter((k) => k !== key));
    } else {
      filterBeatTypes = new Set([...filterBeatTypes, key]);
    }
    selectedIdx = -1;
  }

  // ── Keyboard ──────────────────────────────────────────────────────────────────

  function handleKeydown(e: KeyboardEvent) {
    const target = e.target as HTMLElement;
    const inInput = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;

    if (inInput) {
      if (e.key === "Escape") {
        target.blur();
        filterSearch = "";
      }
      return;
    }

    if (state !== "ready") return;

    switch (e.key) {
      case "j":
      case "J":
        e.preventDefault();
        selectedIdx = Math.min(filteredBites.length - 1, selectedIdx + 1);
        break;
      case "k":
      case "K":
        e.preventDefault();
        selectedIdx = Math.max(0, selectedIdx - 1);
        break;
      case "Enter":
        e.preventDefault();
        void jumpToSelected();
        break;
      case "m":
      case "M":
        e.preventDefault();
        void toggleUsed();
        break;
      case "/":
        e.preventDefault();
        searchEl?.focus();
        break;
      case "Escape":
        filterSearch = "";
        filterBeatTypes = new Set();
        filterFlagged = false;
        filterUncovered = false;
        selectedIdx = -1;
        break;
      default:
        if (/^[1-9]$/.test(e.key)) {
          e.preventDefault();
          const bt = BEAT_TYPES[parseInt(e.key) - 1];
          if (bt) toggleBeatType(bt.key);
        }
    }
  }
</script>

<svelte:window on:keydown={handleKeydown} />

{#if ingestFolder !== null}
  <section class="card cockpit">
    <h2>Story Map</h2>

    {#if state === "loading"}
      <p class="hint">Loading project…</p>
    {:else if state === "error"}
      <p class="error-msg">✗ {errorMsg}</p>
    {:else if state === "empty"}
      <p class="hint">No soundbites found. Run the full pipeline first.</p>
    {:else if state === "ready" && projectState !== null}
      <!-- Stats row -->
      <div class="stats-row">
        <span>{arollCount} A-roll</span>
        <span class="sep">·</span>
        <span>{brollCount} B-roll</span>
        <span class="sep">·</span>
        <span>{allBites.length} bite{allBites.length !== 1 ? "s" : ""}</span>
        {#if avgJudge !== null}
          <span class="sep">·</span>
          <span class="avg-judge">⌀ {avgJudge.toFixed(2)}</span>
        {/if}
      </div>

      <!-- Filter bar -->
      <div class="filter-bar">
        <div class="chips">
          {#each BEAT_TYPES as bt, i (bt.key)}
            <button
              class="chip"
              class:active={filterBeatTypes.has(bt.key)}
              style="--dot:{dotColor(bt.key)}"
              on:click={() => {
                toggleBeatType(bt.key);
              }}
              title={`${i + 1}: ${bt.key}`}>{bt.label}</button
            >
          {/each}
        </div>
        <div class="search-row">
          <input
            bind:this={searchEl}
            bind:value={filterSearch}
            class="search-box"
            type="text"
            placeholder="/ search label or extract…"
            spellcheck="false"
          />
          <button
            class="chip"
            class:active={filterFlagged}
            on:click={() => {
              filterFlagged = !filterFlagged;
              selectedIdx = -1;
            }}>Flagged</button
          >
          <button
            class="chip"
            class:active={filterUncovered}
            on:click={() => {
              filterUncovered = !filterUncovered;
              selectedIdx = -1;
            }}>Uncovered</button
          >
        </div>
      </div>

      <!-- Story map list -->
      {#if filteredBites.length === 0}
        <p class="hint" style="margin-top:0.75rem">No bites match the current filter.</p>
      {:else}
        <div class="story-map" role="listbox" aria-label="Soundbites">
          {#each filteredBites as bite, idx (bite.id)}
            <button
              class="bite-row"
              class:selected={idx === selectedIdx}
              class:used={bite.user_state?.used}
              on:click={() => {
                selectBite(idx);
              }}
              role="option"
              aria-selected={idx === selectedIdx}
            >
              <span class="dot" style="background:{dotColor(bite.beat_type)}"></span>
              <div class="bite-info">
                <div class="bite-label">
                  {#if bite.flagged_for_review}<span class="review-tag">REVIEW</span>{/if}
                  {#if bite.user_state?.used}<span class="used-tag">USED</span>{/if}
                  {bite.label}
                </div>
                <div class="bite-meta">
                  {bite.clip_filename} · {fmtTc(bite.in_tc)} → {fmtTc(bite.out_tc)}
                  ({fmtDuration(bite.in_sec, bite.out_sec)})
                  {#if bite.matches?.length}
                    · <span class="match-count">{bite.matches.length}✦</span>
                  {/if}
                </div>
              </div>
              {#if bite.judge_score !== undefined}
                <span
                  class="judge-chip"
                  class:judge-hi={bite.judge_score >= 0.8}
                  class:judge-mid={bite.judge_score >= 0.6 && bite.judge_score < 0.8}
                  class:judge-lo={bite.judge_score < 0.6}>{bite.judge_score.toFixed(2)}</span
                >
              {/if}
            </button>
          {/each}
        </div>
      {/if}

      <!-- Detail panel for selected bite -->
      {#if selectedBite !== null}
        <div class="detail-panel">
          <div class="detail-header">
            <span class="dot-lg" style="background:{dotColor(selectedBite.beat_type)}"></span>
            <span class="detail-title">{selectedBite.label}</span>
            <span class="detail-beat">{selectedBite.beat_type}</span>
          </div>

          <blockquote class="extract">"{selectedBite.extract}"</blockquote>

          <p class="why">{selectedBite.why_usable}</p>

          <div class="detail-meta-row">
            <span>Confidence: {selectedBite.confidence.toFixed(2)}</span>
            {#if selectedBite.judge_score !== undefined}
              <span class="sep">·</span>
              <span>Judge: {selectedBite.judge_score.toFixed(2)}</span>
            {/if}
            <span class="sep">·</span>
            <span>{selectedBite.in_tc} → {selectedBite.out_tc}</span>
          </div>

          <!-- B-roll matches -->
          {#if selectedBite.matches?.length}
            <div class="matches-section">
              <p class="matches-header">
                B-ROLL MATCHES ({selectedBite.matches.length})
              </p>
              {#each selectedBite.matches as m (m.clip_id + m.kind)}
                {@const brollSc = brollById.get(m.clip_id)}
                <div class="match-row">
                  <span class="kind-badge" class:literal={m.kind === "literal"}
                    >{m.kind === "literal" ? "LIT" : "THM"}</span
                  >
                  <div class="match-info">
                    <span class="match-name"
                      >{brollSc?.source_path ? basename(brollSc.source_path) : m.clip_id}</span
                    >
                    {#if brollSubjectsStr(brollSc) !== null}
                      <span class="match-tags">{brollSubjectsStr(brollSc)}</span>
                    {/if}
                    {#if brollShotTypeStr(brollSc) !== null}
                      <span class="match-tags">· {brollShotTypeStr(brollSc)}</span>
                    {/if}
                  </div>
                  <span class="match-conf">{m.confidence.toFixed(2)}</span>
                  <button
                    class="reject-btn"
                    title="Reject this match"
                    on:click|stopPropagation={() => {
                      void rejectMatch(m.clip_id);
                    }}>✕</button
                  >
                </div>
              {/each}
            </div>
          {:else}
            <p class="hint" style="margin-top:0.5rem;font-size:0.8rem">
              No B-roll matches for this bite.
            </p>
          {/if}

          <!-- Actions -->
          <div class="action-row">
            <button class="primary-btn" on:click={jumpToSelected}> ↵ Jump to clip </button>
            <button
              class="secondary-btn"
              class:used-btn={selectedBite.user_state?.used}
              on:click={toggleUsed}
            >
              {selectedBite.user_state?.used ? "✓ Used" : "M Mark as used"}
            </button>
          </div>

          {#if jumpFeedback}
            <p class="jump-feedback">{jumpFeedback}</p>
          {/if}
        </div>
      {/if}

      <!-- Keyboard shortcut hint -->
      <p class="kb-hint">J/K navigate · Enter jump · M mark · / search · Esc clear · 1–9 filter</p>
    {/if}
  </section>
{/if}

<style>
  .cockpit {
    background: #1a1a1c;
    border: 1px solid #2a2a2c;
    border-radius: 12px;
    padding: 1.5rem;
    margin-bottom: 1.25rem;
  }
  .cockpit h2 {
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
  .error-msg {
    color: #f28b82;
    font-size: 0.9rem;
    margin: 0 0 0.75rem 0;
  }

  /* Stats */
  .stats-row {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.82rem;
    color: #9a9a9e;
    margin-bottom: 0.75rem;
  }
  .sep {
    color: #4a4a4d;
  }
  .avg-judge {
    color: #a8d8a8;
  }

  /* Filter bar */
  .filter-bar {
    margin-bottom: 0.75rem;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
    margin-bottom: 0.4rem;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 0.3em;
    background: #252527;
    border: 1px solid #3a3a3d;
    border-radius: 12px;
    padding: 0.2rem 0.6rem;
    font-size: 0.75rem;
    color: #9a9a9e;
    cursor: pointer;
    font-family: inherit;
    transition: background 0.15s;
  }
  .chip::before {
    content: "";
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--dot, #666);
  }
  .chip.active {
    background: #2a2a3a;
    border-color: #5a5aaa;
    color: #c8c8ff;
  }
  .search-row {
    display: flex;
    align-items: center;
    gap: 0.4rem;
  }
  .search-box {
    flex: 1;
    background: #131315;
    border: 1px solid #2a2a2c;
    border-radius: 6px;
    padding: 0.3rem 0.6rem;
    font-size: 0.82rem;
    color: #e6e6e6;
    font-family: inherit;
    outline: none;
  }
  .search-box:focus {
    border-color: #5a5aaa;
  }

  /* Story map */
  .story-map {
    max-height: 320px;
    overflow-y: auto;
    border: 1px solid #2a2a2c;
    border-radius: 8px;
    background: #131315;
  }
  .bite-row {
    display: flex;
    align-items: flex-start;
    gap: 0.6rem;
    width: 100%;
    padding: 0.55rem 0.75rem;
    border: none;
    border-bottom: 1px solid #1f1f21;
    background: transparent;
    color: inherit;
    cursor: pointer;
    font-family: inherit;
    text-align: left;
    transition: background 0.1s;
  }
  .bite-row:last-child {
    border-bottom: none;
  }
  .bite-row:hover {
    background: #1f1f21;
  }
  .bite-row.selected {
    background: #1e2035;
    border-left: 2px solid #5a5aff;
    padding-left: calc(0.75rem - 2px);
  }
  .bite-row.used {
    opacity: 0.55;
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
    margin-top: 0.35rem;
  }
  .bite-info {
    flex: 1;
    min-width: 0;
  }
  .bite-label {
    font-size: 0.85rem;
    color: #e0e0e0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-bottom: 0.1rem;
  }
  .bite-meta {
    font-size: 0.73rem;
    color: #6a6a6e;
    font-family: "SF Mono", ui-monospace, monospace;
  }
  .review-tag {
    display: inline-block;
    background: #3a1010;
    color: #f28b82;
    font-size: 0.65rem;
    padding: 0.05rem 0.35rem;
    border-radius: 3px;
    margin-right: 0.3rem;
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  .used-tag {
    display: inline-block;
    background: #103a10;
    color: #50c87a;
    font-size: 0.65rem;
    padding: 0.05rem 0.35rem;
    border-radius: 3px;
    margin-right: 0.3rem;
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  .match-count {
    color: #f5c877;
  }
  .judge-chip {
    flex-shrink: 0;
    font-size: 0.72rem;
    font-family: "SF Mono", ui-monospace, monospace;
    padding: 0.1rem 0.35rem;
    border-radius: 4px;
    margin-top: 0.15rem;
  }
  .judge-hi {
    background: #0d2a0d;
    color: #50c87a;
  }
  .judge-mid {
    background: #2a1f00;
    color: #f5c877;
  }
  .judge-lo {
    background: #2a0808;
    color: #f28b82;
  }

  /* Detail panel */
  .detail-panel {
    background: #131315;
    border: 1px solid #2a2a2c;
    border-radius: 8px;
    padding: 1rem;
    margin-top: 0.75rem;
  }
  .detail-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.6rem;
  }
  .dot-lg {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .detail-title {
    font-size: 0.95rem;
    font-weight: 600;
    color: #e6e6e6;
    flex: 1;
  }
  .detail-beat {
    font-size: 0.72rem;
    color: #7f7f82;
    font-family: "SF Mono", ui-monospace, monospace;
  }
  .extract {
    margin: 0 0 0.5rem 0;
    padding: 0.5rem 0.75rem;
    border-left: 2px solid #3a3a4a;
    font-size: 0.85rem;
    color: #c8c8cc;
    font-style: italic;
  }
  .why {
    font-size: 0.82rem;
    color: #7f7f82;
    margin: 0 0 0.5rem 0;
  }
  .detail-meta-row {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.78rem;
    color: #7f7f82;
    font-family: "SF Mono", ui-monospace, monospace;
    margin-bottom: 0.75rem;
  }

  /* Matches */
  .matches-section {
    margin-bottom: 0.75rem;
  }
  .matches-header {
    font-size: 0.72rem;
    color: #7f7f82;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    margin: 0 0 0.4rem 0;
  }
  .match-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.3rem 0;
    border-bottom: 1px solid #1f1f21;
  }
  .match-row:last-child {
    border-bottom: none;
  }
  .kind-badge {
    flex-shrink: 0;
    font-size: 0.65rem;
    font-weight: 700;
    padding: 0.1rem 0.3rem;
    border-radius: 3px;
    background: #1a2a4a;
    color: #7ab0ff;
    font-family: "SF Mono", ui-monospace, monospace;
    letter-spacing: 0.04em;
  }
  .kind-badge.literal {
    background: #1a3a1a;
    color: #50c87a;
  }
  .match-info {
    flex: 1;
    min-width: 0;
  }
  .match-name {
    font-size: 0.8rem;
    color: #c8c8cc;
    font-family: "SF Mono", ui-monospace, monospace;
  }
  .match-tags {
    font-size: 0.73rem;
    color: #6a6a6e;
    margin-left: 0.4rem;
  }
  .match-conf {
    flex-shrink: 0;
    font-size: 0.75rem;
    color: #9a9a9e;
    font-family: "SF Mono", ui-monospace, monospace;
  }
  .reject-btn {
    flex-shrink: 0;
    background: none;
    border: none;
    color: #4a4a4d;
    font-size: 0.75rem;
    cursor: pointer;
    padding: 0.1rem 0.25rem;
    border-radius: 3px;
    font-family: inherit;
  }
  .reject-btn:hover {
    color: #f28b82;
    background: #2a1010;
  }

  /* Actions */
  .action-row {
    display: flex;
    gap: 0.5rem;
    margin-top: 0.75rem;
  }
  .primary-btn {
    background: #2a4a8a;
    color: #d8e8ff;
    border: 1px solid #3a5a9a;
    border-radius: 6px;
    padding: 0.4rem 0.9rem;
    font-size: 0.88rem;
    cursor: pointer;
    font-family: inherit;
  }
  .primary-btn:hover {
    background: #335aaa;
  }
  .secondary-btn {
    background: #2a2a2d;
    color: #9a9a9e;
    border: 1px solid #3a3a3d;
    border-radius: 6px;
    padding: 0.4rem 0.9rem;
    font-size: 0.88rem;
    cursor: pointer;
    font-family: inherit;
  }
  .secondary-btn:hover {
    background: #35353a;
  }
  .used-btn {
    background: #0d2a0d;
    color: #50c87a;
    border-color: #1a4a1a;
  }
  .jump-feedback {
    font-size: 0.78rem;
    color: #a8d8a8;
    margin: 0.4rem 0 0;
    font-family: "SF Mono", ui-monospace, monospace;
  }

  /* Keyboard hint */
  .kb-hint {
    margin: 0.75rem 0 0;
    font-size: 0.72rem;
    color: #4a4a4d;
    text-align: center;
  }
</style>

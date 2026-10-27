<script lang="ts">
  // "Learn my taste": Claude proposes settings from the editor's keep/reject
  // ratings; the plugin replays them before showing the result.
  import { createEventDispatcher } from "svelte";
  import type { SelectsClip, SelectsLearnResult, SelectsSettings } from "../types";

  export let clips: SelectsClip[] = [];
  export let busy = false;
  export let result: SelectsLearnResult | null = null;
  export let saved: string | null = null;

  const dispatch = createEventDispatcher<{
    learn: undefined;
    apply: SelectsSettings;
    save: { name: string; settings: SelectsSettings; rationale: string; accuracy: number };
    dismiss: undefined;
  }>();

  const MIN_RATINGS = 6;

  let presetName = "";
  // Each new proposal brings its own suggested name; keep edits until the next one.
  let namedFor: SelectsLearnResult | null = null;
  function freshProposal(next: SelectsLearnResult | null): boolean {
    if (!next?.proposal || next === namedFor) return false;
    namedFor = next;
    return true;
  }
  $: if (freshProposal(result) && result?.proposal) presetName = result.proposal.preset_name;

  $: ratings = clips.flatMap((c) => Object.values(c.ratings));
  $: keeps = ratings.filter((r) => r.rating === "keep").length;
  $: rejects = ratings.length - keeps;
  $: ready = ratings.length >= MIN_RATINGS && keeps >= 2 && rejects >= 2;

  function pct(n: number, d: number): string {
    return d > 0 ? `${Math.round((100 * n) / d)}%` : "–";
  }
</script>

<div class="learn">
  <div class="intro">
    <p>
      Rate selects with <b>Keep</b> or <b>Reject</b> in the list below. Use <b>Keep</b> on a near miss
      when it should have been a select. Claude works out what your choices have in common and proposes
      settings. Only the measurements are sent to Claude, never footage or file names.
    </p>
    <div class="tally" aria-live="polite">
      <span class="k">{keeps} keep</span>
      <span class="r">{rejects} reject</span>
      {#if !ready}
        <span class="need">need {MIN_RATINGS}+ with at least 2 of each</span>
      {/if}
    </div>
    <button class="primary" disabled={!ready || busy} on:click={() => dispatch("learn")}>
      {busy ? "Claude is thinking…" : "Learn my taste"}
    </button>
  </div>

  {#if result && !result.ok}
    <p class="error" role="alert">{result.error}</p>
  {:else if result?.ok && result.proposal && result.before && result.after}
    <section class="proposal" aria-label="Claude's proposal">
      <div class="scoreline">
        <div>
          <span class="label">Now</span>
          <span class="big">{result.before.correct}/{result.before.total}</span>
        </div>
        <span class="arrow" aria-hidden="true">→</span>
        <div class:better={result.improved}>
          <span class="label">Proposed</span>
          <span class="big">{result.after.correct}/{result.after.total}</span>
        </div>
        <p class="breakdown">
          keeps kept {pct(result.after.keeps_kept, result.after.keeps_total)} · rejects dropped
          {pct(result.after.rejects_dropped, result.after.rejects_total)}
          {#if (result.rounds ?? 1) > 1}· took a second attempt{/if}
        </p>
      </div>
      <p class="rationale">{result.proposal.rationale}</p>
      {#if result.proposal.changes.length}
        <ul class="changes">
          {#each result.proposal.changes as c, i (i)}<li>{c}</li>{/each}
        </ul>
      {/if}
      {#if result.warning}<p class="warn">{result.warning}</p>{/if}
      {#if !result.improved}
        <p class="warn">
          This doesn't reproduce more of your ratings than the current settings. Rate a few more
          selects, or keep what you have.
        </p>
      {/if}
      <div class="actions">
        <button
          class="primary"
          on:click={() => result?.proposal && dispatch("apply", result.proposal.settings)}
          >Try these settings</button
        >
        <input
          type="text"
          bind:value={presetName}
          aria-label="Preset name"
          placeholder="Name this taste"
          maxlength="60"
        />
        <button
          disabled={!presetName.trim()}
          on:click={() =>
            result?.proposal &&
            dispatch("save", {
              name: presetName.trim(),
              settings: result.proposal.settings,
              rationale: result.proposal.rationale,
              accuracy: result.after?.accuracy ?? 0,
            })}>Save as preset</button
        >
        <button class="ghost" on:click={() => dispatch("dismiss")}>Dismiss</button>
      </div>
      {#if saved}<p class="saved">
          Saved “{saved}”. It now appears with the presets on every shoot.
        </p>{/if}
    </section>
  {/if}
</div>

<style>
  .intro p {
    color: var(--sel-muted);
    font-size: 0.84rem;
    line-height: 1.5;
    margin: 0 0 0.6rem;
  }
  .intro b {
    color: var(--sel-text);
    font-weight: 600;
  }
  .tally {
    display: flex;
    gap: 0.75rem;
    font-size: 0.8rem;
    margin-bottom: 0.6rem;
  }
  .k {
    color: var(--sel-keep);
  }
  .r {
    color: var(--sel-reject);
  }
  .need {
    color: var(--sel-faint);
  }
  button {
    background: var(--sel-well);
    color: var(--sel-text);
    border: 1px solid var(--sel-line-strong);
    border-radius: 6px;
    padding: 0.38rem 0.85rem;
    font-size: 0.84rem;
    cursor: pointer;
  }
  button:hover:not(:disabled) {
    border-color: var(--sel-accent);
  }
  button:disabled {
    opacity: 0.45;
    cursor: default;
  }
  button:focus-visible,
  input:focus-visible {
    outline: 2px solid var(--sel-accent);
    outline-offset: 2px;
  }
  button.primary {
    background: var(--sel-accent-soft);
    border-color: var(--sel-accent);
    color: var(--sel-accent-text);
  }
  button.ghost {
    background: none;
    border-color: transparent;
    color: var(--sel-muted);
  }
  .proposal {
    margin-top: 1rem;
    border: 1px solid var(--sel-line-strong);
    border-radius: 10px;
    padding: 0.9rem 1rem;
    background: linear-gradient(180deg, rgba(90, 140, 255, 0.06), transparent 60%);
  }
  .scoreline {
    display: flex;
    align-items: baseline;
    gap: 0.9rem;
    flex-wrap: wrap;
  }
  .scoreline .label {
    display: block;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--sel-faint);
  }
  .big {
    font-size: 1.6rem;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.02em;
  }
  .better .big {
    color: var(--sel-keep);
  }
  .arrow {
    color: var(--sel-faint);
    font-size: 1.2rem;
  }
  .breakdown {
    flex-basis: 100%;
    margin: 0.1rem 0 0;
    font-size: 0.76rem;
    color: var(--sel-faint);
  }
  .rationale {
    font-size: 0.88rem;
    line-height: 1.55;
    margin: 0.8rem 0 0.4rem;
  }
  .changes {
    margin: 0 0 0.6rem;
    padding-left: 1.1rem;
    font-size: 0.8rem;
    color: var(--sel-muted);
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.45rem;
    align-items: center;
  }
  .actions input {
    background: var(--sel-well);
    border: 1px solid var(--sel-line);
    color: var(--sel-text);
    border-radius: 6px;
    padding: 0.36rem 0.55rem;
    font-size: 0.84rem;
    min-width: 11rem;
  }
  .warn {
    color: var(--sel-warn);
    font-size: 0.8rem;
  }
  .error {
    color: var(--sel-reject);
    font-size: 0.84rem;
    margin-top: 0.75rem;
  }
  .saved {
    color: var(--sel-keep);
    font-size: 0.8rem;
    margin: 0.5rem 0 0;
  }
</style>

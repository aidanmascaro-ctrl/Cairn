<script lang="ts">
  // One row per clip: a strip showing every sample's score, the selects laid
  // over it (solid) and near misses (dashed), and keep / reject buttons that
  // teach the taste learner.
  import { createEventDispatcher } from "svelte";
  import type { SelectRatingValue, SelectSegment, SelectsClip } from "../types";
  import { bucket, formatDuration, formatScore, ratingKey, scoreColour } from "./score";

  export let clips: SelectsClip[] = [];
  export let selectsOnly = false;
  /** True while analysis runs: ratings would queue behind it in the worker. */
  export let disabled = false;

  const dispatch = createEventDispatcher<{
    rate: {
      clip: SelectsClip;
      segment: SelectSegment;
      rating: SelectRatingValue | null;
      source: "select" | "near_miss";
    };
    focus: string | null;
  }>();

  const STRIP_CELLS = 120;

  let open: string | null = null;

  $: shown = clips.filter((c) => c.analysed && (!selectsOnly || c.segments.length > 0));
  $: pending = clips.filter((c) => !c.analysed).length;

  function pct(sec: number, clip: SelectsClip): number {
    return clip.duration_sec > 0 ? (100 * sec) / clip.duration_sec : 0;
  }

  function current(clip: SelectsClip, seg: SelectSegment): SelectRatingValue | null {
    return clip.ratings[ratingKey(seg)]?.rating ?? null;
  }

  function toggle(
    clip: SelectsClip,
    seg: SelectSegment,
    want: SelectRatingValue,
    source: "select" | "near_miss",
  ): void {
    const rating = current(clip, seg) === want ? null : want;
    dispatch("rate", { clip, segment: seg, rating, source });
  }

  function label(clip: SelectsClip, seg: SelectSegment): string {
    return `${clip.name} ${formatDuration(seg.start_sec)} to ${formatDuration(seg.end_sec)}`;
  }

  function headLabel(clip: SelectsClip): string {
    const n = clip.segments.length;
    const m = clip.near_misses.length;
    const near = m ? `, ${m} near miss${m === 1 ? "" : "es"}` : "";
    return `${clip.name}: ${n} select${n === 1 ? "" : "s"}${near}`;
  }

  function expand(file: string): void {
    open = open === file ? null : file;
    dispatch("focus", open);
  }
</script>

<ul class="clips">
  {#each shown as clip (clip.file)}
    <li class:open={open === clip.file}>
      <button
        class="head"
        aria-expanded={open === clip.file}
        aria-label={headLabel(clip)}
        on:click={() => {
          expand(clip.file);
        }}
      >
        <span class="name">{clip.name}</span>
        <span class="strip" aria-hidden="true">
          {#each bucket(clip.scores, STRIP_CELLS) as s, i (i)}
            <span class="sample" style="background:{scoreColour(s)}"></span>
          {/each}
          {#each clip.near_misses as seg (seg.start_sec)}
            <span
              class="seg near"
              style="left:{pct(seg.start_sec, clip)}%;width:{pct(seg.duration_sec, clip)}%"
            ></span>
          {/each}
          {#each clip.segments as seg (seg.start_sec)}
            <span
              class="seg"
              class:kept={current(clip, seg) === "keep"}
              class:rejected={current(clip, seg) === "reject"}
              style="left:{pct(seg.start_sec, clip)}%;width:{pct(seg.duration_sec, clip)}%"
            ></span>
          {/each}
        </span>
        <span class="count">
          {clip.segments.length || "–"}
        </span>
      </button>

      {#if open === clip.file}
        <div class="detail">
          {#if clip.segments.length === 0 && clip.near_misses.length === 0}
            <p class="empty">Nothing in this clip comes close at these settings.</p>
          {/if}
          {#each [...clip.segments.map( (s) => ({ s, near: false }), ), ...clip.near_misses.map( (s) => ({ s, near: true }), )] as { s: seg, near } (`${near}-${seg.start_sec}`)}
            <div class="seg-row" class:near>
              <span class="dot" style="background:{scoreColour(seg.mean_score)}"></span>
              <span class="range">
                {formatDuration(seg.start_sec)} → {formatDuration(seg.end_sec)}
                <small>{formatDuration(seg.duration_sec)}</small>
              </span>
              <span class="score">{formatScore(seg.mean_score)}</span>
              <span class="meta">
                {near ? "near miss · " : ""}{seg.camera.class.replace("_", " ")}{seg.flags.length
                  ? ` · ${seg.flags.join(", ").replaceAll("_", " ")}`
                  : ""}
              </span>
              <span class="rate">
                <button
                  class="keep"
                  class:on={current(clip, seg) === "keep"}
                  aria-pressed={current(clip, seg) === "keep"}
                  aria-label="Keep {label(clip, seg)}"
                  {disabled}
                  title={near ? "This should have been a select" : "Good select"}
                  on:click={() => {
                    toggle(clip, seg, "keep", near ? "near_miss" : "select");
                  }}>Keep</button
                >
                {#if !near}
                  <button
                    class="reject"
                    class:on={current(clip, seg) === "reject"}
                    aria-pressed={current(clip, seg) === "reject"}
                    aria-label="Reject {label(clip, seg)}"
                    {disabled}
                    title="Not a select"
                    on:click={() => {
                      toggle(clip, seg, "reject", "select");
                    }}>Reject</button
                  >
                {/if}
              </span>
            </div>
          {/each}
        </div>
      {/if}
    </li>
  {/each}
</ul>
{#if pending > 0}
  <p class="pending">{pending} clip{pending === 1 ? "" : "s"} not analysed yet.</p>
{/if}

<style>
  .clips {
    list-style: none;
    margin: 0;
    padding: 0;
    max-height: 420px;
    overflow-y: auto;
    border-top: 1px solid var(--sel-line);
  }
  li {
    border-bottom: 1px solid var(--sel-line);
  }
  .head {
    display: grid;
    grid-template-columns: 7.5rem 1fr 2rem;
    align-items: center;
    gap: 0.75rem;
    width: 100%;
    background: none;
    border: 0;
    color: inherit;
    padding: 0.4rem 0.25rem;
    cursor: pointer;
    text-align: left;
  }
  .head:hover {
    background: var(--sel-hover);
  }
  .head:focus-visible {
    outline: 2px solid var(--sel-accent);
    outline-offset: -2px;
  }
  .name {
    font-family: "SF Mono", ui-monospace, monospace;
    font-size: 0.76rem;
    color: var(--sel-muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .strip {
    position: relative;
    display: flex;
    height: 14px;
    border-radius: 3px;
    overflow: hidden;
    background: var(--sel-well);
  }
  .sample {
    flex: 1 1 0;
    opacity: 0.38;
  }
  .seg {
    position: absolute;
    top: 0;
    bottom: 0;
    border: 1.5px solid #fff;
    border-radius: 3px;
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.35);
  }
  .seg.near {
    border: 1.5px dashed var(--sel-faint);
    box-shadow: none;
  }
  .seg.kept {
    border-color: var(--sel-keep);
  }
  .seg.rejected {
    border-color: var(--sel-reject);
    background: repeating-linear-gradient(
      135deg,
      transparent 0 4px,
      rgba(242, 139, 130, 0.25) 4px 6px
    );
  }
  .count {
    text-align: right;
    font-size: 0.8rem;
    font-variant-numeric: tabular-nums;
    color: var(--sel-muted);
  }
  .detail {
    padding: 0.2rem 0.25rem 0.6rem 8.25rem;
  }
  .seg-row {
    display: grid;
    grid-template-columns: 0.6rem 9.5rem 2.5rem 1fr auto;
    align-items: center;
    gap: 0.6rem;
    font-size: 0.8rem;
    padding: 0.18rem 0;
  }
  .seg-row.near {
    color: var(--sel-muted);
  }
  .dot {
    width: 0.6rem;
    height: 0.6rem;
    border-radius: 50%;
  }
  .seg-row.near .dot {
    background: none !important;
    border: 1px dashed var(--sel-faint);
  }
  .range small {
    color: var(--sel-faint);
    margin-left: 0.3rem;
  }
  .score {
    font-variant-numeric: tabular-nums;
  }
  .meta {
    color: var(--sel-faint);
    font-size: 0.74rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .rate {
    display: flex;
    gap: 0.3rem;
  }
  .rate button {
    background: transparent;
    border: 1px solid var(--sel-line);
    color: var(--sel-muted);
    border-radius: 5px;
    padding: 0.12rem 0.5rem;
    font-size: 0.74rem;
    cursor: pointer;
  }
  .rate button:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .rate button:hover:not(:disabled) {
    color: var(--sel-text);
    border-color: var(--sel-line-strong);
  }
  .rate .keep.on {
    background: rgba(120, 200, 140, 0.16);
    border-color: var(--sel-keep);
    color: var(--sel-keep);
  }
  .rate .reject.on {
    background: rgba(242, 139, 130, 0.14);
    border-color: var(--sel-reject);
    color: var(--sel-reject);
  }
  .empty,
  .pending {
    color: var(--sel-faint);
    font-size: 0.8rem;
    margin: 0.3rem 0;
  }
</style>

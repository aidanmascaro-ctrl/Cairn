<script lang="ts">
  // Every select as a dot: across = how long it is, up = its mean score.
  // The dashed line is the score threshold; dots below it are near misses.
  import type { SelectsClip } from "../types";
  import { formatDuration, formatScore, scoreColour } from "./score";

  export let clips: SelectsClip[] = [];
  export let threshold = 0.8;
  export let focusFile: string | null = null;

  const W = 640;
  const H = 180;
  const PAD = { l: 34, r: 10, t: 10, b: 24 };
  const Y_MIN = 0.4;

  interface Dot {
    file: string;
    name: string;
    duration: number;
    score: number;
    near: boolean;
  }

  $: dots = clips.flatMap((c): Dot[] => [
    ...c.segments.map((s) => ({
      file: c.file,
      name: c.name,
      duration: s.duration_sec,
      score: s.mean_score,
      near: false,
    })),
    ...c.near_misses.map((s) => ({
      file: c.file,
      name: c.name,
      duration: s.duration_sec,
      score: s.mean_score,
      near: true,
    })),
  ]);
  $: maxDur = Math.max(10, ...dots.map((d) => d.duration));
  // Log scale so a 200 s take doesn't squash every 3 s select into the left edge.
  function x(sec: number, max: number): number {
    return PAD.l + (Math.log1p(sec) / Math.log1p(max)) * (W - PAD.l - PAD.r);
  }
  function y(score: number): number {
    return PAD.t + (1 - (Math.max(Y_MIN, score) - Y_MIN) / (1 - Y_MIN)) * (H - PAD.t - PAD.b);
  }
  $: ticks = [1, 3, 10, 30, 100, 300, 1000].filter((t) => t <= maxDur);

  let hovered: Dot | null = null;
</script>

<figure class="chart">
  <svg viewBox="0 0 {W} {H}" role="img" aria-label="Selects by duration and score">
    {#each [0.5, 0.6, 0.7, 0.8, 0.9, 1] as s (s)}
      <line class="grid" x1={PAD.l} x2={W - PAD.r} y1={y(s)} y2={y(s)} />
      <text class="axis" x={PAD.l - 6} y={y(s) + 3} text-anchor="end">{s.toFixed(1)}</text>
    {/each}
    {#each ticks as t (t)}
      <text class="axis" x={x(t, maxDur)} y={H - 6} text-anchor="middle">{formatDuration(t)}</text>
    {/each}
    <line class="threshold" x1={PAD.l} x2={W - PAD.r} y1={y(threshold)} y2={y(threshold)} />
    {#each dots as d, i (i)}
      <circle
        cx={x(d.duration, maxDur)}
        cy={y(d.score)}
        r={focusFile === d.file ? 5 : 3.5}
        fill={d.near ? "none" : scoreColour(d.score)}
        stroke={d.near ? "var(--sel-faint)" : focusFile === d.file ? "#fff" : "none"}
        stroke-dasharray={d.near ? "2 2" : undefined}
        role="img"
        aria-label="{d.name}, score {formatScore(d.score)}, {formatDuration(d.duration)}{d.near
          ? ', near miss'
          : ''}"
        class:dim={focusFile !== null && focusFile !== d.file}
        on:mouseenter={() => (hovered = d)}
        on:mouseleave={() => (hovered = null)}
      >
        <title
          >{d.name}: score {formatScore(d.score)}, {formatDuration(d.duration)}{d.near
            ? " (near miss)"
            : ""}</title
        >
      </circle>
    {/each}
  </svg>
  <figcaption>
    {#if hovered}
      <strong>{hovered.name}</strong> · {formatScore(hovered.score)} · {formatDuration(
        hovered.duration,
      )}{hovered.near ? " · near miss" : ""}
    {:else}
      Each dot is a select (hollow = near miss). Across: length. Up: score.
    {/if}
  </figcaption>
</figure>

<style>
  .chart {
    margin: 0;
  }
  svg {
    width: 100%;
    height: auto;
    display: block;
  }
  .grid {
    stroke: var(--sel-line);
    stroke-width: 1;
  }
  .axis {
    fill: var(--sel-faint);
    font-size: 9px;
    font-variant-numeric: tabular-nums;
  }
  .threshold {
    stroke: var(--sel-accent);
    stroke-width: 1.2;
    stroke-dasharray: 5 4;
  }
  circle {
    transition:
      opacity 120ms ease,
      r 120ms ease;
  }
  circle.dim {
    opacity: 0.25;
  }
  figcaption {
    color: var(--sel-faint);
    font-size: 0.76rem;
    min-height: 1.2em;
    margin-top: 0.2rem;
  }
  figcaption strong {
    color: var(--sel-text);
    font-weight: 600;
  }
</style>

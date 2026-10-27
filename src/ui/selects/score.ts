// Shared helpers for the Selects UI: score colours, labels, formatting.

import type { FlagName, FlagTier, SelectGroup, SelectSegment, SubMetric } from "../types";

/** Red (weak) → amber → green (strong). Matches the marker bands in Resolve. */
export function scoreColour(score: number): string {
  const v = Math.max(0, Math.min(1, score));
  // Hue 4 (red) at 0.55 and below, 48 (amber) at 0.75, 140 (green) at 0.95+.
  const t = Math.max(0, Math.min(1, (v - 0.55) / 0.4));
  const hue = t < 0.5 ? 4 + (48 - 4) * (t / 0.5) : 48 + (140 - 48) * ((t - 0.5) / 0.5);
  return `hsl(${hue.toFixed(0)} 62% 52%)`;
}

export function formatDuration(sec: number): string {
  if (!Number.isFinite(sec) || sec <= 0) return "0s";
  if (sec < 60) return `${sec.toFixed(sec < 10 ? 1 : 0)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  if (m < 60) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

export function formatScore(score: number | null | undefined): string {
  return score === null || score === undefined ? "–" : score.toFixed(2);
}

export const GROUP_LABELS: Record<SelectGroup, string> = {
  focus: "Focus",
  exposure: "Exposure",
  motion: "Motion",
};

/** Sub-metric → [group, label, low end, high end]. */
export const SUB_METRICS: readonly (readonly [SubMetric, SelectGroup, string, string, string])[] = [
  ["sharpness", "focus", "Sharpness", "Soft", "Sharp"],
  ["detail", "focus", "Detail", "Flat", "Detailed"],
  ["focus_stability", "focus", "Focus stability", "Drifting", "Locked"],
  ["headroom", "exposure", "Grading headroom", "Clipped", "Flexible"],
  ["flicker", "exposure", "Flicker", "Flickering", "Stable"],
  ["luma_target", "exposure", "Brightness target", "Off", "On"],
  ["contrast_target", "exposure", "Contrast target", "Off", "On"],
  ["camera_stillness", "motion", "Camera", "Moving", "Still"],
  ["subject_stillness", "motion", "Subject", "Busy", "Calm"],
  ["motion_smoothness", "motion", "Smoothness", "Jerky", "Smooth"],
] as const;

export const FLAG_LABELS: Record<FlagName, string> = {
  clipping: "Clipping",
  duplicate: "Duplicate frames",
  solid_color: "Solid colour",
  hard_cut: "Hard cut",
  motion_blur: "Motion blur",
  shaky: "Shaky",
  low_light: "Low light",
};

export const TIER_LABELS: Record<FlagTier, string> = {
  exclude: "Exclude",
  disfavour: "Disfavour",
  neutral: "Neutral",
  favour: "Favour",
  include: "Always keep",
};

export const SAMPLING_RATES: readonly { value: number; label: string }[] = [
  { value: 1, label: "Draft — 1 per second" },
  { value: 2, label: "Fast — 2 per second" },
  { value: 4, label: "Standard — 4 per second" },
  { value: 8, label: "Detailed — 8 per second" },
  { value: 16, label: "Thorough — 16 per second" },
  { value: 0, label: "Every frame" },
];

export function ratingKey(seg: Pick<SelectSegment, "key">): string {
  // Python's f"{x:.2f}" and JS toFixed(2) round exact ties (.125, .375 …) differently,
  // so the key always comes from the worker, never from formatting here.
  return seg.key;
}

export function fileName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

/** Trailing-edge debounce that always runs the latest call; `cancel()` drops a pending one. */
export interface Debounced<A extends unknown[]> {
  (...args: A): void;
  cancel(): void;
}

export function debounce<A extends unknown[]>(fn: (...args: A) => void, ms: number): Debounced<A> {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const run = (...args: A): void => {
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      fn(...args);
    }, ms);
  };
  run.cancel = (): void => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  };
  return run;
}

/** Average scores into at most `n` buckets so long clips draw a light strip. */
export function bucket(scores: readonly number[], n: number): number[] {
  if (scores.length <= n) return [...scores];
  const size = scores.length / n;
  return Array.from({ length: n }, (_, i) => {
    const from = Math.floor(i * size);
    const to = Math.max(from + 1, Math.floor((i + 1) * size));
    const slice = scores.slice(from, to);
    return slice.reduce((a, b) => a + b, 0) / slice.length;
  });
}

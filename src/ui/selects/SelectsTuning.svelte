<script lang="ts">
  import { createEventDispatcher } from "svelte";
  import type {
    CameraAxis,
    FlagName,
    FlagTier,
    SelectGroup,
    SelectsPreset,
    SelectsSettings,
  } from "../types";
  import { FLAG_LABELS, GROUP_LABELS, SUB_METRICS, TIER_LABELS } from "./score";

  export let settings: SelectsSettings;
  export let presets: SelectsPreset[] = [];
  export let disabled = false;

  const dispatch = createEventDispatcher<{ change: SelectsSettings; preset: string }>();

  const GROUPS: SelectGroup[] = ["focus", "exposure", "motion"];
  const FLAGS = Object.keys(FLAG_LABELS) as FlagName[];
  const TIERS = Object.keys(TIER_LABELS) as FlagTier[];

  // Every edit dispatches a new settings object; this component never mutates its prop.
  // A hand edit leaves the preset, so no chip claims settings it no longer matches.
  function update(patch: Partial<SelectsSettings>): void {
    dispatch("change", { ...settings, ...patch, preset_id: "custom" });
  }

  function num(e: Event): number {
    return Number((e.currentTarget as HTMLInputElement).value);
  }

  function optionalNum(e: Event): number | null {
    const raw = (e.currentTarget as HTMLInputElement).value.trim();
    const v = Number(raw);
    return raw === "" || !Number.isFinite(v) || v <= 0 ? null : v;
  }

  function tier(e: Event): FlagTier {
    const v = (e.currentTarget as HTMLSelectElement).value;
    return (TIERS as string[]).includes(v) ? (v as FlagTier) : "neutral";
  }

  function checked(e: Event): boolean {
    return (e.currentTarget as HTMLInputElement).checked;
  }

  const CAMERA_AXES: readonly (readonly [CameraAxis, string, string, string])[] = [
    ["pan", "Pan", "Left", "Right"],
    ["tilt", "Tilt", "Up", "Down"],
    ["zoom", "Zoom", "Out", "In"],
    ["twist", "Rotate", "Anti-clockwise", "Clockwise"],
  ];

  $: builtin = presets.filter((p) => p.builtin);
  $: personal = presets.filter((p) => !p.builtin);
</script>

<div class="tuning" class:disabled>
  <div class="presets" role="group" aria-label="Presets">
    {#each builtin as p (p.id)}
      <button
        class="chip"
        class:active={settings.preset_id === p.id}
        aria-pressed={settings.preset_id === p.id}
        title={p.description}
        {disabled}
        on:click={() => dispatch("preset", p.id)}>{p.name}</button
      >
    {/each}
    {#each personal as p (p.id)}
      <button
        class="chip personal"
        class:active={settings.preset_id === p.id}
        aria-pressed={settings.preset_id === p.id}
        title={p.rationale || p.description}
        {disabled}
        on:click={() => dispatch("preset", p.id)}>★ {p.name}</button
      >
    {/each}
  </div>

  <div class="sliders">
    <label class="row">
      <span class="name">Keep scores above</span>
      <input
        type="range"
        min="0.3"
        max="1"
        step="0.01"
        value={settings.score_threshold_lo}
        {disabled}
        on:input={(e) => {
          update({ score_threshold_lo: num(e) });
        }}
      />
      <output>{settings.score_threshold_lo.toFixed(2)}</output>
    </label>
    <label class="row">
      <span class="name">Shortest select</span>
      <input
        type="range"
        min="0"
        max="15"
        step="0.25"
        value={settings.min_segment_sec}
        {disabled}
        on:input={(e) => {
          update({ min_segment_sec: num(e) });
        }}
      />
      <output>{settings.min_segment_sec.toFixed(2)}s</output>
    </label>
    <label class="row">
      <span class="name" title="How long a shot may dip below the threshold before it is split"
        >Bridge dips up to</span
      >
      <input
        type="range"
        min="0"
        max="6"
        step="0.25"
        value={settings.dip_sec}
        {disabled}
        on:input={(e) => {
          update({ dip_sec: num(e) });
        }}
      />
      <output>{settings.dip_sec.toFixed(2)}s</output>
    </label>
    <div class="row pair">
      <label>
        <span class="name">Longest select</span>
        <input
          type="number"
          min="0"
          step="1"
          placeholder="no limit"
          value={settings.max_segment_sec ?? ""}
          {disabled}
          on:change={(e) => {
            update({ max_segment_sec: optionalNum(e) });
          }}
        />
      </label>
      <label>
        <span class="name">Most per clip</span>
        <input
          type="number"
          min="0"
          step="1"
          placeholder="no limit"
          value={settings.max_segments_per_clip ?? ""}
          {disabled}
          on:change={(e) => {
            const v = optionalNum(e);
            update({ max_segments_per_clip: v === null ? null : Math.round(v) });
          }}
        />
      </label>
    </div>
  </div>

  <details>
    <summary>What counts as good</summary>
    <div class="groups">
      {#each GROUPS as g (g)}
        <fieldset>
          <legend>
            {GROUP_LABELS[g]}
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              aria-label="{GROUP_LABELS[g]} weight"
              value={settings.group_weights[g]}
              {disabled}
              on:input={(e) => {
                update({ group_weights: { ...settings.group_weights, [g]: num(e) } });
              }}
            />
            <output>{Math.round(settings.group_weights[g] * 100)}%</output>
          </legend>
          {#each SUB_METRICS.filter((m) => m[1] === g) as [key, , label, low, high] (key)}
            <label class="sub" class:off={settings.sub_weights[key] === 0}>
              <span class="ends">{low}</span>
              <input
                type="range"
                min="-1"
                max="1"
                step="0.05"
                aria-label={label}
                value={settings.sub_weights[key]}
                {disabled}
                on:input={(e) => {
                  update({ sub_weights: { ...settings.sub_weights, [key]: num(e) } });
                }}
                on:dblclick={() => {
                  update({ sub_weights: { ...settings.sub_weights, [key]: 0 } });
                }}
              />
              <span class="ends">{high}</span>
              <span class="sub-name">{label}</span>
            </label>
          {/each}
        </fieldset>
      {/each}
    </div>
    <p class="note">
      Right of centre rewards the right-hand quality, left rewards the left. Centre (double-click)
      ignores it.
    </p>
  </details>

  <details>
    <summary>Flags</summary>
    <div class="flags">
      {#each FLAGS as f (f)}
        <label class="flag">
          <span>{FLAG_LABELS[f]}</span>
          <select
            value={settings.flags[f]}
            {disabled}
            on:change={(e) => {
              update({ flags: { ...settings.flags, [f]: tier(e) } });
            }}
          >
            {#each TIERS as t (t)}
              <option value={t}>{TIER_LABELS[t]}</option>
            {/each}
          </select>
        </label>
      {/each}
    </div>
  </details>

  <details>
    <summary>Camera movement</summary>
    <div class="camera">
      <label class="check">
        <input
          type="checkbox"
          checked={settings.camera_filter.enabled}
          {disabled}
          on:change={(e) => {
            update({
              camera_filter: {
                ...settings.camera_filter,
                enabled: checked(e),
              },
            });
          }}
        />
        Filter by camera move
      </label>
      <label class="check">
        <input
          type="checkbox"
          checked={settings.camera_filter.static_only}
          disabled={disabled || !settings.camera_filter.enabled}
          on:change={(e) => {
            update({
              camera_filter: {
                ...settings.camera_filter,
                static_only: checked(e),
              },
            });
          }}
        />
        Tripod shots only
      </label>
      {#each CAMERA_AXES as [axis, label, low, high] (axis)}
        <label class="sub">
          <span class="ends">{low}</span>
          <input
            type="range"
            min="-1"
            max="1"
            step="0.05"
            aria-label={label}
            value={settings.camera_filter[axis]}
            disabled={disabled ||
              !settings.camera_filter.enabled ||
              settings.camera_filter.static_only}
            on:input={(e) => {
              update({ camera_filter: { ...settings.camera_filter, [axis]: num(e) } });
            }}
          />
          <span class="ends">{high}</span>
          <span class="sub-name">{label}</span>
        </label>
      {/each}
      <label class="check">
        <input
          type="checkbox"
          checked={settings.camera_filter.mirror}
          disabled={disabled || !settings.camera_filter.enabled}
          on:change={(e) => {
            update({
              camera_filter: {
                ...settings.camera_filter,
                mirror: checked(e),
              },
            });
          }}
        />
        Either direction
      </label>
    </div>
  </details>
</div>

<style>
  .tuning.disabled {
    opacity: 0.55;
  }
  .presets {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
    margin-bottom: 1rem;
  }
  .chip {
    background: transparent;
    color: var(--sel-muted);
    border: 1px solid var(--sel-line);
    border-radius: 999px;
    padding: 0.25rem 0.7rem;
    font-size: 0.8rem;
    cursor: pointer;
    transition:
      background-color 120ms ease,
      color 120ms ease;
  }
  .chip:hover:not(:disabled) {
    color: var(--sel-text);
    border-color: var(--sel-line-strong);
  }
  .chip.active {
    background: var(--sel-accent-soft);
    color: var(--sel-accent-text);
    border-color: var(--sel-accent);
  }
  .chip.personal {
    border-style: dashed;
  }
  .chip:focus-visible,
  select:focus-visible,
  input:focus-visible {
    outline: 2px solid var(--sel-accent);
    outline-offset: 2px;
  }
  .sliders {
    display: grid;
    gap: 0.45rem;
  }
  .row {
    display: grid;
    grid-template-columns: 9.5rem 1fr 3.5rem;
    align-items: center;
    gap: 0.75rem;
    font-size: 0.86rem;
  }
  .row.pair {
    grid-template-columns: 1fr 1fr;
  }
  .row.pair label {
    display: grid;
    grid-template-columns: 9.5rem 1fr;
    align-items: center;
    gap: 0.75rem;
  }
  .name {
    color: var(--sel-muted);
  }
  output {
    font-variant-numeric: tabular-nums;
    color: var(--sel-text);
    text-align: right;
    font-size: 0.82rem;
  }
  input[type="range"] {
    width: 100%;
    accent-color: var(--sel-accent);
  }
  input[type="number"],
  select {
    background: var(--sel-well);
    color: var(--sel-text);
    border: 1px solid var(--sel-line);
    border-radius: 6px;
    padding: 0.25rem 0.45rem;
    font-size: 0.82rem;
    width: 100%;
  }
  details {
    margin-top: 0.9rem;
    border-top: 1px solid var(--sel-line);
    padding-top: 0.6rem;
  }
  summary {
    cursor: pointer;
    color: var(--sel-muted);
    font-size: 0.82rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  summary:hover {
    color: var(--sel-text);
  }
  .groups {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 0.75rem;
    margin-top: 0.75rem;
  }
  fieldset {
    border: 1px solid var(--sel-line);
    border-radius: 8px;
    padding: 0.5rem 0.65rem 0.6rem;
    margin: 0;
    min-width: 0;
  }
  legend {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0 0.3rem;
    font-size: 0.82rem;
    color: var(--sel-text);
    font-weight: 600;
  }
  legend input {
    width: 5rem;
  }
  .sub {
    display: grid;
    grid-template-columns: 4.6rem 1fr 4.6rem;
    grid-template-areas: "name name name" "low bar high";
    align-items: center;
    column-gap: 0.4rem;
    font-size: 0.74rem;
    margin-top: 0.35rem;
  }
  .sub-name {
    grid-area: name;
    color: var(--sel-muted);
  }
  .sub .ends:first-child {
    grid-area: low;
    text-align: right;
    color: var(--sel-faint);
  }
  .sub input {
    grid-area: bar;
  }
  .sub .ends:nth-child(3) {
    grid-area: high;
    color: var(--sel-faint);
  }
  .sub.off .sub-name {
    text-decoration: line-through;
    color: var(--sel-faint);
  }
  .note {
    color: var(--sel-faint);
    font-size: 0.76rem;
    margin: 0.6rem 0 0;
  }
  .flags {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
    gap: 0.4rem 1rem;
    margin-top: 0.6rem;
  }
  .flag {
    display: grid;
    grid-template-columns: 1fr 7.5rem;
    align-items: center;
    font-size: 0.82rem;
    color: var(--sel-muted);
  }
  .camera {
    display: grid;
    gap: 0.3rem;
    margin-top: 0.6rem;
    max-width: 420px;
  }
  .check {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    font-size: 0.82rem;
    color: var(--sel-muted);
  }
</style>

---
version: 1
stage: selects_learn
---

You are tuning the scoring settings of a footage-selects tool for one video editor. The tool samples frames from every clip, measures them, scores each sample, and keeps unbroken runs of high-scoring samples as "selects". The editor has rated some of those runs. Your job is to propose settings that reproduce the editor's ratings, so the tool learns their taste.

## How a sample is scored

Every measurement is 0 to 1. For most, 1 is the conventionally "good" end:

- Focus group: `sharpness` (soft→sharp), `detail` (flat→textured), `focus_stability` (hunting→locked)
- Exposure group: `headroom` (clipped→room to grade), `flicker` (pulsing→stable), `luma` (dark→bright, no good end), `contrast` (flat→contrasty, no good end)
- Motion group: `camera_stillness` (moving camera→locked off), `subject_stillness` (busy frame→calm frame), `motion_smoothness` (jerky→gliding)

Each sub-metric has a slider from -1 to +1. Positive rewards the high end, negative rewards the low end, the magnitude is how much it counts, and 0 ignores it. `luma_target` and `contrast_target` instead reward closeness to `targets.luma` / `targets.contrast`. A group's score is the weighted mean of its active sliders. The sample score is the weighted mean of the three groups by `group_weights`.

Flags are per-sample warnings: clipping, duplicate, solid_color, hard_cut, motion_blur, shaky, low_light. Each flag has a tier: `exclude` (the sample can never be in a select and splits takes), `disfavour` (score × 0.6), `neutral`, `favour` (score × 1.2), `include` (always kept).

A select is a run of samples scoring at or above `score_threshold_lo`. A dip below the threshold shorter than `dip_sec` does not split a run. Runs shorter than `min_segment_sec` are dropped. `max_segment_sec` and `max_segments_per_clip` cap length and count (null = no cap).

## What the editor rated

Each example is one rated time range with the mean of every measurement over it. `rating: keep` means it should be a select; `rating: reject` means it should not. `source: near_miss` means the range was not a select under the current settings and the editor said it should be. `camera_move` is the summed camera motion per second; `flag_share` is the fraction of samples carrying each flag.

```json
{{EXAMPLES_JSON}}
```

## Current settings

```json
{{SETTINGS_JSON}}
```

Under these settings, {{BASELINE_CORRECT}} of {{BASELINE_TOTAL}} ratings come out the way the editor wanted.

## What to do

Look for what separates the keeps from the rejects in the measurements, and change the fewest settings that would move the most ratings to the right side. A keep counts as reproduced when at least half of its range ends up selected. Prefer settings that express a taste that will hold on the editor's next shoot over settings that only memorise these examples. If the ratings contradict each other or the measurements cannot separate them, say so in the rationale rather than forcing a fit.

Return the complete settings object (unchanged values included), a short name for the taste, two to four sentences of rationale written to the editor in plain English, and a list of the specific changes.

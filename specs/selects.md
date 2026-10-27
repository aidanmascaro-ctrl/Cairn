# specs/selects.md — Stage 5: Selects (footage scoring → selects timeline)

## Purpose

Recreate the Cutdown workflow inside the plugin: score every sampled frame of every
clip for **focus**, **exposure** and **motion** once, store the raw measurements, then let
the editor tune which runs of frames become **selects** without re-analysing, and lay those
selects out as a Resolve timeline with score-coloured markers.

Cutdown (cutdown.app, Softworks) does this as a standalone Tauri app and hands the result to
Resolve as OTIO. Cairn already lives inside Resolve, so the export step disappears: the
timeline is built directly through the Scripting API, and the measurements live in the
clip sidecars like every other stage.

Deterministic, offline, no AI model. Same footage + same settings = same selects.

## Inputs

- Every video clip the Classify scanner sees (`classify.scan_video_clips`): `01_VIDEOS/*_CAM`
  for organised shoots, `Footage/<Camera>` for Offload shoots. Classification is **not**
  required; selects work on raw ingested footage.
- `sampling_rate` samples per second (default **4**; choices 1 / 2 / 4 / 8 / 16 / every frame).
- Tuning settings (`SelectsSettings`, below). The user never has to touch them: the
  **All-Rounder** preset is the default.

Idempotent: a clip whose sidecar already has `"selects"` in `stages_complete` at the same
sampling rate is skipped. A different sampling rate re-analyses.

## Outputs

### Per clip — sidecar `selects` block (written **before** anything touches Resolve)

```json
"selects": {
  "version": 1,
  "sampling_rate": 4.0,
  "fps": 25.0,
  "duration_sec": 35.52,
  "width": 3840, "height": 2160,
  "analysed_at": "2026-10-27T12:00:00+00:00",
  "columns": ["t", "sharpness", "detail", "focus_stability", "headroom", "luma",
              "contrast", "flicker", "camera_stillness", "subject_stillness",
              "motion_smoothness", "pan", "tilt", "zoom", "twist",
              "motion_confidence", "flags"],
  "frames": [[0.0, 0.71, 0.4, 1.0, 0.93, 0.41, 0.22, 1.0, 0.98, 0.9, 1.0, 0.0, 0.0, 0.0, 0.0, 0.6, 0], ...]
}
```

Every metric is `0.0–1.0`, oriented so **1.0 is the "good" end** of the Cutdown bar
(Sharp, Detailed, Locked, Flexible, Stable, Still, Steady, Smooth). `luma` and `contrast`
are fixed scales with no good end. `pan`/`tilt`/`zoom`/`twist` are signed camera-motion
speeds **per second of footage** (frame widths or heights per second, log-scale per second,
radians per second), so classes and filters mean the same thing at any sampling rate.
`flags` is a bitmask: `1 clipping, 2 duplicate, 4 solid_color, 8 hard_cut,
16 motion_blur, 32 shaky, 64 low_light`.

### Per shoot — `.cairn/selects.json`

Last-used `SelectsSettings` so reopening the shoot restores the tuning.

### Resolve — the selects timeline

Name `"<shoot> — Cairn Selects"` (suffixed `(2)`, `(3)` if taken; never overwritten).
Resolution + fps = modal across the analysed clips (same rule as the assembly timeline).

Two layouts, mirroring Cutdown's Resolve export:

| Layout              | V1 / A1                                                       | V2 / A2                                                  |
| ------------------- | ------------------------------------------------------------- | -------------------------------------------------------- |
| `context` (default) | every clip in full **minus** its selects (the grey remainder) | the selects, cut out of V1 at the same timeline position |
| `selects_only`      | the selects packed back-to-back, in chosen order              | —                                                        |

Audio comes with each piece (video + audio appended together) and is linked to its
picture (`SetClipsLinked`), per the house rule.

Markers (`include_markers`, default on): one **timeline** marker per select, at the select's
start, with the select's duration. Colour by score band across the exported selects:
top third **Green**, middle **Yellow**, bottom third **Red**. Name `Select 0.87 · C0340`.
Note carries mean / peak score, the three group scores, duration, camera-movement class and
any flags. `customData = "cairn:select"` so a rebuild can clear them.

Source-clip markers (`clip_markers`, default on): the same markers on each media-pool clip at
source position, so selects show in the source viewer and survive the timeline being deleted.
`clear_cairn_markers` runs first so a re-tune does not stack duplicates.

## Algorithm

### 1. Sampling

One ffmpeg pass per clip:

```
ffmpeg -nostdin -i <clip> -an -sn
  -vf "fps=<rate>,scale=w='if(gte(iw,ih),960,-2)':h='if(gte(iw,ih),-2,960)',format=gray"
  -f rawvideo -pix_fmt gray -
```

Frames stream into numpy as `uint8[h, w]`. Long edge ≤ 960 px, grayscale, autorotated.
Decoding is software-only: VideoToolbox can't decode Sony XAVC 4:2:2 10-bit H.264 and was twice as slow as software on the files it could open. Frame `i` is at
`t = i / rate`. `fps`, `duration`, `width`, `height` come from ffprobe.
Source media is opened read-only; nothing is written next to it.

### 2. Per-frame measurements (`selects_metrics.py`)

Letterbox rows/columns (mean < 0.02 at the edges) are cropped first.

| Metric              | Measure                                                                                                                                                                                                                                 | Normalisation             |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------- |
| `sharpness`         | variance of the Laplacian per cell of a 6×6 grid; take the **max** cell so an in-focus subject on a soft background scores well; cells with mean luma < 0.08 are down-weighted (sensor noise ≠ detail)                                  | `1 - exp(-v / 0.0025)`    |
| `detail`            | fraction of pixels whose Sobel magnitude exceeds 0.08                                                                                                                                                                                   | `min(1, frac / 0.15)`     |
| `headroom`          | `1 - min(1, 8 × (crushed + blown))` where crushed = frac < 1/255, blown = frac > 254/255                                                                                                                                                | —                         |
| `luma`              | mean                                                                                                                                                                                                                                    | —                         |
| `contrast`          | standard deviation                                                                                                                                                                                                                      | `min(1, std / 0.3)`       |
| camera motion       | `cv2.estimateAffinePartial2D` on `goodFeaturesToTrack` + Lucas-Kanade between consecutive samples → translation (`pan`, `tilt` as fraction of width/height), `zoom = log(scale)`, `twist` (radians), `motion_confidence` = inlier ratio | —                         |
| `camera_stillness`  | `1 - min(1, Σ abs(axis) / CAMERA_AXIS_SCALE[axis])` with the per-second scale in `selects_score.py` (pan 0.15, tilt 0.15 frame/s; zoom 0.15 log/s; twist 0.05 rad/s), the same numbers the camera classes and filter use                | —                         |
| `subject_stillness` | previous sample warped by the camera transform, mean abs diff vs current                                                                                                                                                                | `1 - min(1, diff / 0.08)` |

Temporal metrics, computed over a ±2-sample window once all frames are in:

| Metric              | Measure                                                       |
| ------------------- | ------------------------------------------------------------- |
| `focus_stability`   | `1 - min(1, std(sharpness over window) / 0.15)`               |
| `flicker`           | `1 - min(1, std(luma over window) / 0.03)`                    |
| `motion_smoothness` | `1 - min(1, std(camera-motion magnitude over window) / 0.03)` |

Flags:

| Flag        | Rule                                                                     |
| ----------- | ------------------------------------------------------------------------ |
| clipping    | crushed + blown ≥ 1.5 %                                                  |
| duplicate   | mean abs diff vs previous sample < 0.003                                 |
| solid_color | std < 0.01                                                               |
| hard_cut    | raw mean abs diff vs previous > 0.35 **and** histogram correlation < 0.5 |
| motion_blur | sharpness < 0.25 **and** camera_stillness < 0.5                          |
| shaky       | camera_stillness < 0.5 **and** motion_smoothness < 0.4                   |
| low_light   | luma < 0.08                                                              |

The first sample of a clip has no previous frame: motion metrics are copied from the second.

### 3. Scoring (`selects_score.py`, pure, re-run on every tuning change)

```
SelectsSettings
  preset_id
  sampling_rate            (analysis-time only; informational here)
  weights: focus_group 0.6, exposure_group 0.2, motion_group 0.2
  sub: {sharpness: +0.5, detail: +0.3, focus_stability: +0.4,
        headroom: +1.0, flicker: 0, luma_target: off, contrast_target: off,
        camera_stillness: +0.4, subject_stillness: +0.3, motion_smoothness: +0.6}
       each in [-1, +1]; sign = favoured direction, magnitude = weight; 0 = off
  score_threshold_lo 0.80, score_threshold_hi 1.0
  min_segment_sec 2.0, max_segment_sec null, max_segments_per_clip null
  dip_sec 3.0
  handles_samples 3
  flags: {clipping: "exclude", duplicate: "neutral", solid_color: "exclude",
          hard_cut: "neutral", motion_blur: "neutral", shaky: "neutral", low_light: "neutral"}
         tiers: exclude | disfavour (×0.6) | neutral | favour (×1.2) | include
  camera_filter: {enabled, static_only, pan, tilt, zoom, twist (each -1..+1),
                  strictness 0..1, mirror}
  metrics_enabled true, flags_enabled true
```

Per sample:

1. Sub-metric term: `v` if slider > 0, `1 - v` if slider < 0; `luma_target`/`contrast_target`
   are `1 - |v - target| / 0.5`. Group score = weighted mean by `|slider|` of enabled terms.
   A group with no enabled sub-metric contributes nothing (Cutdown's amber state).
2. `score = Σ group_weight × group / Σ group_weight` over contributing groups. With
   `metrics_enabled` off, `score = 1.0`.
3. Flags (`flags_enabled`): any `exclude` flag → sample is **unusable**; any `include` →
   **forced**; otherwise multiply by 0.6 per disfavoured flag and 1.2 per favoured flag,
   clamped to 1.0.
4. Camera filter: `static_only` → multiply by `camera_stillness`. Otherwise each non-zero
   axis contributes a match in 0..1 (signed direction match, `mirror` accepts both
   signs, `strictness` sharpens the match curve); the score is multiplied by the mean match.
   The filter never gates on its own.

Segments per clip:

1. `qualifying = forced or (lo ≤ score ≤ hi and not unusable)`.
2. Walk the samples. A run of qualifying samples stays open across a dip of non-qualifying
   samples shorter than `dip_sec`; an unusable sample always closes it.
3. Convert to seconds; drop runs shorter than `min_segment_sec`; split runs longer than
   `max_segment_sec` into equal pieces.
4. Add up to `handles_samples` samples each side, stopping at an unusable sample, the next
   segment, or the clip edge.
5. Keep the `max_segments_per_clip` highest mean scores.
6. Each segment: `start_sec`, `end_sec`, `mean_score`, `peak_score`, `peak_sec`,
   `group_scores`, `flags`, `camera` (`pan/tilt/zoom/twist` means, `confidence`, `class` ∈
   static / pan_left / pan_right / tilt_up / tilt_down / zoom_in / zoom_out / twist_cw /
   twist_ccw / mixed), `forced`.

Marker band = tertile of `mean_score` across all exported selects.

### 4. Presets

| id             | Favours                                                   | Key changes from All-Rounder                                                                                          |
| -------------- | --------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `all-rounder`  | sharp, well exposed, mostly static                        | defaults above                                                                                                        |
| `talking-head` | locked focus, still camera, some subject movement allowed | focus 0.65, subject_stillness +0.1, camera_stillness +0.6, dip 4 s, min 3 s                                           |
| `social-media` | punchy, short, bright                                     | min 1 s, max 8 s, max 3 per clip, contrast_target 0.45 on, threshold 0.75                                             |
| `action-sport` | energetic footage                                         | motion 0.45; camera_stillness −0.4, subject_stillness −0.5, motion_smoothness +0.6, motion_blur favour, shaky neutral |
| `cleanup`      | just drop the junk                                        | threshold 0.55, flags clipping/solid/duplicate/hard_cut exclude, min 1 s                                              |
| `long-takes`   | long stable shots                                         | min 8 s, dip 6 s, focus_stability +0.8                                                                                |
| `drone-aerial` | smooth sharp gimbal moves                                 | motion_smoothness +1.0, camera_stillness 0 (off), shaky exclude                                                       |
| `whip-pans`    | the blur other presets reject                             | motion_blur favour, sharpness −0.3, camera_stillness −0.8, min 0.5 s                                                  |
| `locked-off`   | static only                                               | camera_filter static_only                                                                                             |

### 5. Timeline build (`selects_timeline.py`)

For each clip: `source_fps` from `GetClipProperty("FPS")`; source frames are **0-based native
frames**, `endFrame` exclusive: `src_in = round(start_sec × source_fps)`,
`src_out = round(end_sec × source_fps)`. Timeline position in timeline frames:
`record = tl_start + round(t × timeline_fps)`.

`context` layout: per clip in order, append the remainder pieces (`[0, s1)`, `[e1, s2)`, …,
`[eN, dur)`) to track 1 and the selects to track 2 at the same timeline offsets, as one
`AppendToTimeline` batch of clipInfo dicts (`mediaType` omitted → video + audio).
Then `SetClipsLinked` per piece, then markers.

`selects_only` layout: selects back-to-back on track 1 in `order` ∈ timeline /
score_desc / score_asc / duration_desc / duration_asc.

The timeline is read back after the build (`GetItemListInTrack`) and the counts in the
result come from the read-back, not from the request.

## Sensors / validations

- Every scoring rule has a pytest in `tests/python/test_selects_score.py` (dip bridging,
  min/max length, per-clip cap, exclude/include tiers, handles, bands, camera classes,
  presets all valid).
- Metrics are tested on synthetic frames in `tests/python/test_selects_metrics.py`: a blurred
  frame scores lower `sharpness` than its source; a shifted frame yields the right `pan`
  sign; a dark frame flags `low_light`; identical frames flag `duplicate`.
- Sampler integration test runs on a generated `testsrc` clip when ffmpeg is present.
- Zod schemas in `python-bridge.ts` reject malformed bridge messages (Vitest).
- After the Resolve build the worker reads the timeline back and compares piece counts.
- Comparison against Cutdown's own segments on the ACME shoot (dev-time only): overlap of
  Cairn selects with Cutdown selects at All-Rounder settings is the regression number to
  watch when touching metrics.

## Out of scope (Cutdown features deliberately not recreated)

Watch folders, proxy linking / RAW decode, the video player with overlays, AAF / FCPXML /
EDL / CSV export, licensing, and the keyboard-shortcut editor. The timeline is built in
Resolve directly; Resolve is the player.

## Taste learning (§6)

The editor rates selects (`keep` / `reject`) and near misses (`keep` = should have been a
select; a near miss is a run that qualifies with the threshold 0.1 lower). Ratings are time
ranges stored in the clip sidecar under `select_ratings`, so they survive any re-tune.

`selects_learn.learn_context` turns each rated range into a numeric summary (mean of every
measurement, camera class, flag shares, duration). Clip names become `clip N`; no frames,
paths or file names leave the machine. Claude (`claude-opus-5-5`, adaptive thinking, effort
high, structured output, server-side refusal fallback) proposes a complete settings object
from `src/orchestrator/prompts/selects_learn.md`. Python replays the proposal over the
ratings (`selects_learn.evaluate`: a keep is right when at least half of it is selected).
If it is no better than the current settings, the misses go back to Claude for one more
attempt. The best proposal is shown with before/after counts; the editor can try it or
save it as a personal preset (`~/.cairn/selects_presets.json`, shared across shoots).

Learning needs at least 6 ratings with at least 2 keeps and 2 rejects.

## Calibration and live findings (2026-10-27)

- Constants in `selects_metrics.py` were tuned against Cutdown's own All-Rounder selects on
  40 ACME clips (Sony XAVC 4K vertical, SLog3): coordinate descent on 20 clips, checked on
  the other 20. Held-out overlap rose from 0.49 to 0.68 IoU. Across all 40, sample-level
  agreement is 90% and Cairn keeps 25.6% of footage against Cutdown's 24.2%.
- Throughput: 116 s of 4K XAVC analysed in 26 s at 4 samples/s (about 4.4× real time).
- Resolve 21.1 `AppendToTimeline` with clipInfo returns only the video items, puts the
  clip's audio on the matching audio track index, and links the pair itself.
  `SetClipsLinked` returns False on an already-linked group, so links are counted from
  `GetLinkedItems` read-back.
- A select that reaches the last sample now runs to the true clip end; before, the last
  fraction of a sample left 1–2 frame slivers on V1.

## Open questions

- A live end-to-end Claude learning call has not run yet: the only key available in the
  dev environment was rejected (401). Unit tests cover the loop with a fake client.
- Cutdown's exact constants are not published; the calibrated values above reproduce its
  All-Rounder behaviour on Sony XAVC. Other cameras (DJI, iPhone HDR) are unmeasured.

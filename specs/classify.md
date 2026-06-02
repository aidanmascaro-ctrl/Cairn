# specs/classify.md — Stage 2: Classify A-roll vs B-roll

> **Algorithm note (scope reset 966ce94):** Cairn classifies on **vision, not
> audio**. The earlier Silero-VAD speech-ratio design was removed — the user
> keeps a live mic on the interview subject while shooting B-roll, so any speech
> signal is unreliable as a primary classifier. The shipping classifier is a
> **KNN model over two local visual features (face ratio + motion score)**. This
> spec documents the implemented behaviour in `src/workers/classify.py`.

## Purpose

For every imported video clip, decide whether it's **A-ROLL** (primary subject
/ talking head), **B-ROLL** (visual-only / cutaway), or **AMBIGUOUS** (needs
human review). Tag the clip in Resolve and apply the media-pool colour so the
editor sees categories at a glance. Classification runs **entirely locally with
no network calls.**

## Inputs

- Every clip under the shoot's `01_VIDEOS/{A_CAM,B_CAM,C_CAM}` whose sidecar:
  - has `"stages_complete"` containing `"ingest"`, and
  - does **not** contain `"classify"` (idempotency — already-classified clips
    are skipped and re-emitted as `clip_skipped`).
- The clip file itself, read via the vendored `ffmpeg`/`ffprobe`
  (`assets/bin/`) to probe duration and extract sample frames.
- The KNN training set at `~/.cairn/ground_truth.json` (written by
  `tools/trainer.js`), when present.

**Not touched:** photos, assets. These are pre-coloured by ingest.

## Features (per clip)

Five frames are extracted at **10 / 25 / 50 / 75 / 90 %** of the clip duration
(downscaled to 640 px wide) and reused for both features:

1. **`face_ratio`** — frames containing ≥1 detected face / frames sampled.
   Detection uses the OpenCV frontal-face **Haar cascade** (local, no model
   download). Min face box edge `FACE_MIN_SIZE = 50 px` filters noise.
2. **`motion_score`** — mean normalised frame-to-frame absolute pixel diff over
   the grayscale frames (`0.0` = static, `1.0` = chaotic). `< 2` frames → `0.0`.

## Decision

```
KNN lookup (k=5) against ground_truth.json
  ├─ ≥ KNN_MIN_EXAMPLES (5) examples available
  │     ├─ winning-label vote share ≥ KNN_CONFIDENCE_THRESHOLD (0.70) → that label
  │     └─ vote share < 0.70                                          → AMBIGUOUS
  └─ < 5 examples (cold start) → rule fallback:
        face_ratio ≥ FACE_THRESHOLD (0.40) → A-ROLL, else B-ROLL
```

Distance is the squared Euclidean distance in **range-normalised**
`(face_ratio, motion_score)` space (each feature divided by its observed range
across the training set). Labels come from the `k` nearest neighbours; the
modal label wins and its vote share is the confidence.

**Tunable constants** (top of `classify.py`):

| Constant                   | Value | Meaning                                     |
| -------------------------- | ----- | ------------------------------------------- |
| `FACE_THRESHOLD`           | 0.40  | rule-fallback A-roll cutoff on `face_ratio` |
| `FACE_SAMPLE_COUNT`        | 5     | frames sampled per clip                     |
| `FACE_MIN_SIZE`            | 50    | min face box edge (px)                      |
| `KNN_K`                    | 5     | neighbours considered                       |
| `KNN_MIN_EXAMPLES`         | 5     | min training examples before KNN engages    |
| `KNN_CONFIDENCE_THRESHOLD` | 0.70  | min winning vote share to commit a label    |

## Outputs

**Per clip:**

- **Classification label** → Resolve custom metadata key `Cairn Classification`
  (`A-ROLL` / `B-ROLL` / `AMBIGUOUS`), plus `SetClipColor` and `AddFlag` —
  written by `resolve_api.classify_write_resolve`.
- **Clip colour** (the two-colour visual system, see `overview.md`):
  - `A-ROLL` → Blue · `B-ROLL` → Green · `AMBIGUOUS` → Yellow
- **Sidecar updated** (sidecar first, then Resolve — taste invariant #6):

  ```json
  {
    "classification": "A-ROLL" | "B-ROLL" | "AMBIGUOUS",
    "classification_meta": {
      "duration_sec": 12.42,
      "face_ratio": 0.6,
      "face_frames": 3,
      "frames_sampled": 5,
      "motion_score": 0.0431,
      "classifier_version": "v3-knn-face-motion"
    },
    "stages_complete": ["ingest", "classify"]
  }
  ```

**Streaming events** (one JSON line each, consumed by the orchestrator):
`clip_classified` / `clip_skipped` / `clip_error` per clip, then a final
`classify_complete` summary `{a_roll, b_roll, ambiguous, skipped, total}`.

## Visual inspector

`inspect_clip(clip_path)` re-analyses a single clip for the UI's
`ClassifyInspector`: it re-extracts the same 5 frames, draws cyan face boxes and
a per-frame motion-intensity bar, and returns **base64 JPEG data URLs** plus the
recomputed `face_ratio`, `motion_score`, and KNN label/confidence — no temp
files survive the call.

## Training (`tools/trainer.js`)

A standalone local tool generates a looping GIF preview per clip and lets the
user label A-ROLL / B-ROLL / skip; confirmed examples (with feature vectors) are
appended to `~/.cairn/ground_truth.json`. The classifier reads this file on the
next run. More/better examples → fewer AMBIGUOUS results.

## Sensors / Validations

- **Classifier eval harness** (`tests/python/test_classifier_evals.py`): a
  held-out set with ground-truth labels; the suite asserts accuracy stays at or
  above the regression gate (≈80%). Any constant change must not regress it.
- **Idempotency**: a second run on a classified clip is a no-op (`clip_skipped`).
- **Cold-start fallback**: with `< 5` examples, the rule path is used; tested.
- **Resolve read-back**: after writing, re-query the metadata + clip colour and
  assert they match (taste invariant — verify Resolve mutations).
- **Colour validity**: every colour string is validated against the known
  `SetClipColor` enum at call time; invalid colour → raise, never silently skip.
- **No network**: `classify.py` performs no network I/O.

### Taste-invariant cross-checks

- #1 / #2 (read-only source): frames extracted to a temp dir only; source clips
  are never modified or deleted.
- #3 (serial Resolve): frame extraction / face / motion are CPU-bound and may run
  per-clip, but every Resolve write drains through the single serial worker.
- #7 (flag ambiguous): low-confidence clips become `AMBIGUOUS` for user review —
  the classifier never silently guesses.

## Open questions

- **Ground-truth volume** — KNN quality scales with labelled examples. How many
  clips across how many shoots before AMBIGUOUS rates drop to comfortable levels?
- **Feature set v2** — would adding shot-scale (face-box area fraction) or a
  colour-temperature feature meaningfully separate edge cases? Evaluate via the
  eval harness before adding.
- **Per-camera priors** — A_CAM is usually A-roll, B_CAM/C_CAM usually B-roll.
  Worth a weak prior, or does it bias too hard? Currently camera is not a feature.

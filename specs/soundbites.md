# specs/soundbites.md — Stage 6: Identify soundbites

## Purpose

For every A-roll clip, read the transcript and the project brief, and identify the strong, usable soundbites — the moments an editor would actually pull. For each soundbite, place a colour-coded timeline marker on the source clip at the exact in/out timecode, with a rich note. The editor scrubs markers instead of re-reading an hour of transcript.

## Inputs

- Every A-roll clip sidecar with:
  - `"transcript"` populated (Stage 3 done)
  - `"stages_complete"` NOT containing `"soundbites"`
- `brief.json` with `finalised: true` (Stage 5 done)
- Claude API key

## Outputs

**Per clip:**
- **Timeline markers** placed on the *source* clip (media pool clip marker, not timeline marker — these travel with the clip into any timeline the editor drags it to):
  - Marker `frame` = source-frame of soundbite in-point
  - Marker `duration` = soundbite length in frames
  - Marker `color` = one of Resolve's accepted colours, mapped by beat type (below)
  - Marker `name` = short label (e.g. "Origin story — first touched clay at 19")
  - Marker `note` = structured multi-line note (see below)
- **Sidecar updated** with:
  ```json
  {
    "soundbites": [
      {
        "id": "sb_<clip-id>_001",
        "beat_type": "origin-story",
        "in_sec": 12.40,
        "out_sec": 27.80,
        "in_frame": 298,              // source-clip frame numbers
        "out_frame": 667,
        "in_tc": "00:00:12:10",       // display-only, 24 fps example
        "out_tc": "00:00:27:19",
        "label": "Origin story — first touched clay at 19",
        "why_usable": "Strong emotional hook; naturally cold-open-able.",
        "extract": "So I started at 19 after a pottery class at uni...",
        "marker_color": "Green",
        "confidence": 0.87,
        "judge_score": 0.91           // second-pass LLM-as-judge
      },
      ...
    ],
    "stages_complete": ["ingest", "classify", "transcribe", "soundbites"]
  }
  ```

## Beat-type → marker-colour mapping

From `spec.md`:

| Beat type | Colour |
|---|---|
| `origin-story` | Green |
| `emotional-peak` | Red |
| `factual` | Blue |
| `hook` | Yellow |

Additional beat types discoverable from the brief's `desired_soundbite_types` map to other Resolve marker colours. Defaults for new types:

| Beat type | Colour |
|---|---|
| `quote` | Cyan |
| `process-detail` | Purple |
| `humour` | Pink |
| `reflection` | Lavender |
| `technical` | Sky |
| `tension` | Fuchsia |
| `resolution` | Mint |

Invalid colour strings are rejected before the Resolve call; fallback to `Lemon` and log.

## Algorithm

### 1. Build prompt context

For each A-roll clip (per-clip, not per-batch — soundbites are local to a clip):

- Clip identity: filename, duration, speaker labels
- Full transcript (`utterances[]` with timestamps)
- Brief summary: `subject`, `narrative_angle`, `tone`, `themes`, `desired_soundbite_types`, `deliverables` (especially target durations — an IG Reel wants bites < 20 s, YouTube tolerates longer)

### 2. Claude selection call

**Prompt:** `src/orchestrator/prompts/soundbite_select.md`

**Meta-reasoning block (mandatory):** the prompt must open with at least two ways this can go wrong, addressed in-prompt:

```
<meta_reasoning>
Two failure modes to guard against:
1. Greedy selection — returning 30 soundbites when only 8 are strong. Bias toward
   fewer, stronger picks. If uncertain, don't mark.
2. Beat-type miscategorisation — calling every emotional moment "emotional-peak"
   when it's actually a reflection or quote. Use the definitions below strictly.
Address: apply the definitions rigorously, default to omitting on uncertainty.
</meta_reasoning>
```

**Beat-type definitions** included in-prompt so the model doesn't guess:

- **origin-story** — how/why the subject started; formative moment
- **emotional-peak** — visible emotion, vulnerability, or catharsis
- **factual** — claim, statistic, or concrete biographical fact (good for lower-thirds)
- **hook** — a single sentence that would work as a cold open; attention-grabbing, self-contained
- **quote** — memorable phrasing, pull-quote-able
- **process-detail** — how the thing is made/done; craft specifics
- **humour** — unironic laughter or a landed joke
- **reflection** — introspective, looking-back moment
- **technical** — jargon-heavy explanation that serves a specific audience
- **tension** — conflict, struggle, or unresolved stakes
- **resolution** — the pay-off or synthesis of tension

**Tool-use call shape:** `propose_soundbites` tool with array-of-objects schema matching the sidecar output above (excluding `judge_score`, set by Stage 6b).

**Selection rules baked into the prompt:**
- Use word-level timestamps to bound the bite to a complete utterance (don't cut mid-word).
- Prefer starts on a breath / sentence boundary; prefer ends on a natural falling-intonation cue (Claude can't hear this directly, but the transcript punctuation is a reasonable proxy).
- Target duration: 5–40 s (hard limits). Most bites 8–20 s.
- If the brief specifies tight deliverables (e.g. IG Reel 60–90 s), bias toward bites under 15 s.
- No overlapping bites on the same clip unless they're genuinely nested (one is a shorter excerpt of a longer one — allowed, but label clearly).

### 3. In/out → frame conversion

Resolve timeline markers are placed in frames, not seconds. Per clip:

```python
fps = clip.GetClipProperty("FPS")  # e.g. 24.0, 23.976, 25.0
in_frame  = round(in_sec  * fps)
out_frame = round(out_sec * fps)
duration  = out_frame - in_frame
```

Snap to the nearest whole frame. Document fps in the sidecar so re-runs (or user playback at a different project frame rate) can reconcile.

### 4. Judge pass (LLM-as-judge)

**Prompt:** `src/orchestrator/prompts/soundbite_judge.md`

Run a second Claude call per clip: "Here are the proposed soundbites, the transcript, and the brief. Score each 0–1 on (a) standalone usability and (b) beat-type accuracy. Flag any < 0.6."

Writes `judge_score` per bite. Bites with `judge_score < 0.6` are tagged `"flagged_for_review": true` in the sidecar; they still get a marker (the user might disagree), but the marker's note starts with `[REVIEW]` and the UI review panel surfaces them.

### 5. Write

**Sidecar first.** Then through the serial Resolve worker:

```python
clip.AddMarker(
    frameId=in_frame,
    color=beat_type_colour,
    name=label,
    note=render_marker_note(bite),
    duration=duration,
    customData=f"cairn:{bite['id']}"   # so we can find + delete on re-run
)
```

**Marker note format** (multi-line):

```
<label>

WHY: <why_usable>

EXTRACT: <extract>

ID: <id>
TYPE: <beat_type>
CONFIDENCE: <confidence>
```

(Stage 7 appends "MATCHES:" lines after the fact.)

### 6. Idempotency + re-run

If the clip already has Cairn markers (detectable via `customData` prefix `cairn:`), delete them first (single, atomic Resolve transaction) before writing new ones. This keeps re-runs clean even if the model's picks change.

## Sensors / Validations

- **Frame-precision round-trip test** — write a marker at a known frame, read it back via `GetMarkers()`, assert `frame` matches.
- **Colour validation** — reject invalid marker colours before the Resolve call.
- **Overlap rule** — within a clip, bites must not non-trivially overlap unless one is a nested subset of another. Flag in tests + sidecar.
- **Judge threshold** — at least 60% of bites across a project should pass `judge_score ≥ 0.6`. Below that, surface a project-wide "Selection quality may be low — consider refining the brief" banner.
- **Hard duration bounds** — bite duration ∈ [3 s, 60 s]. Outside that range, raise.
- **Schema validation** on the tool-use response.
- **Prompt versioning** — every prompt file in `src/orchestrator/prompts/` has a `version:` frontmatter. Sidecars record which prompt version produced them.

### Taste-invariant cross-checks

- #3 (serial Resolve): marker writes + reads drain through the single worker.
- #5 (no placeholders): if the Claude response is malformed after retry, raise and log in `fix_plan.md` — do not ship "best-effort" markers.
- #6 (sidecar first): sidecar written, then Resolve markers.
- #7 (flag ambiguous): judge-flagged bites get `[REVIEW]` prefix, surfaced in the review panel. Never silently dropped.

## Open questions

- **Markers on source clip vs timeline** — we're using media pool clip markers so they travel with the clip. Verify `MediaPoolItem.AddMarker` supports `color`, `name`, `note`, `duration`, `customData`. If not, we may need timeline markers on a dedicated "Soundbites" timeline the editor can open.
- **24 vs 23.976** — Sony commonly records 23.976. Frame rounding under non-integer fps can drift. Document in `AGENT.md` after first real shoot.
- **Multi-clip bites** — some shoots break an interview across multiple clips (card swap). A strong bite might span the boundary. v1: don't handle this — each clip is independent. v2: match adjacent-clip bites.
- **User-added vs Cairn-added markers** — we only touch markers with `customData` starting `cairn:`. Verified via test; documented in README so users know their own markers are safe.
- **How many bites per clip?** No hard cap. Prompt's "fewer, stronger" guidance is the soft cap. Monitor on real shoots.
- **Confidence calibration** — are `confidence` values meaningful or noise? Cross-check against judge scores on first real shoot; if they don't correlate, drop the self-reported confidence and rely on judge only.

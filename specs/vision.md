# specs/vision.md — Stage 4: Index B-roll visually

## Purpose

For every clip labelled `B-ROLL` (including user-confirmed ambiguous clips), extract representative frames, send them to Claude vision, and write structured visual tags to Resolve's `Keywords` field plus the sidecar. This is the step that makes later B-roll matching (Stage 7) possible.

## Inputs

- Every clip whose sidecar has:
  - `"classification": "B-ROLL"`
  - `"stages_complete"` NOT containing `"vision"`
- User's `ANTHROPIC_API_KEY` (keychain via keytar)
- Per-project opt-in flag: `cloud_vision_allowed: true | false`
  - `true` → Claude vision (default)
  - `false` → local vision (e.g. CLIP / OpenCLIP tags + colour histogram). Local is less rich; for NDA'd work.

## Outputs

**Per clip:**

- **Five frames** extracted to `04_EXPORT/.cairn/frames/<clip-id>/{01..05}.jpg` (deleted after upload unless `CAIRN_KEEP_FRAMES` env var is set).
- **Structured tags** written to Resolve `Keywords` as a comma-joined list of the most filterable labels (subjects + shot type + key descriptors):
  - Example: `hands, wheel, clay, warm-light, close-up, shallow-DOF`
- **Sidecar updated** with the full tag structure:
  ```json
  {
    "vision": {
      "provider": "anthropic" | "local-clip",
      "provider_model": "claude-<version>",
      "frame_times": [0.0, 7.25, 14.5, 21.75, 29.0],
      "tags": {
        "subjects": ["hands", "potter's wheel", "wet clay"],
        "actions": ["shaping", "spinning", "centering"],
        "shot_type": "close-up",
        "mood": "intimate",
        "environment": "indoor studio",
        "colour_palette": ["warm tungsten", "earthy ochre", "deep brown"],
        "time_of_day": "evening",
        "lighting_style": "key from left, shallow shadow fill",
        "camera_motion": "static tripod",
        "confidence": 0.88
      },
      "raw_response_sha": "<sha256 of full JSON response for debug>"
    },
    "stages_complete": ["ingest", "classify", "vision"]
  }
  ```

## Algorithm

### 1. Frame selection

Per `spec.md`: five frames at 0%, 25%, 50%, 75%, and 100% of clip duration.

```
ffprobe -v quiet -print_format json -show_format <clip>  # get duration
# For pct in [0.0, 0.25, 0.5, 0.75, 1.0]:
ffmpeg -ss <pct * duration> -i <clip> -frames:v 1 -q:v 3 \
       -update 1 -y <frames-dir>/<pct-index>.jpg
```

Edge cases:

- **Clips < 2 s**: take 3 frames instead (first, middle, last) — 5 frames from a 1-s clip over-samples.
- **End frame at exactly duration**: some containers drop the last frame; if ffmpeg fails, step back 0.1 s.
- **Very long clips (> 10 min)**: still 5 frames, but note in sidecar `"sampling_note": "long-clip-sparse"` — Stage 7 matching should weight this lower for literal matches since the visual content likely varies across the clip.

`-q:v 3` yields reasonable JPEG quality (~85) without huge file sizes. Target: each frame < 300 kB → 5 frames ≈ 1.5 MB per clip uploaded.

### 2. Claude vision call

**Endpoint:** `messages.create` with `content` blocks containing 5 image parts + one text prompt.

**Prompt location:** `src/orchestrator/prompts/vision_tag.md` (per CLAUDE.md rule: all prompts live there, never inline).

**Prompt structure (meta-reasoning block mandatory per CLAUDE.md):**

```
You are tagging B-roll footage to make it searchable and matchable to dialogue later.

<meta_reasoning>
Two ways this could go wrong:
1. You might invent details not visible in the frames (e.g. "warm family moment" when
   the frames show an isolated object). If a tag isn't observable, omit it.
2. You might over-generalise (e.g. "person" when it's clearly "potter's hands"). Prefer
   the most specific accurate noun.
Address both by grounding every tag in at least one visible frame.
</meta_reasoning>

You'll see 5 frames from one B-roll clip. Return a JSON object with these keys:
- subjects: concrete nouns that appear (specific > generic)
- actions: verbs / gerunds visible in motion between frames
- shot_type: one of wide, medium, close-up, macro, aerial, handheld-POV
- mood: 1–2 words describing emotional register
- environment: where this takes place (indoor studio, outdoor golden-hour, etc.)
- colour_palette: 2–4 dominant colours (use evocative names, not hex)
- time_of_day: morning, midday, afternoon, golden-hour, evening, night, indoor, unclear
- lighting_style: short phrase (key from left, window soft, practical only, etc.)
- camera_motion: static tripod, handheld, slider, gimbal, whip-pan, unclear
- confidence: 0.0–1.0, your overall confidence in these tags

Return ONLY the JSON object. No prose.
```

**Tool-use alternative:** instead of asking for JSON-in-text, define a `tag_broll` tool that takes the structured shape. This is more robust (no JSON-parse errors) and is the preferred implementation.

### 3. Parse + write

- Validate response against Zod (Node) or dataclass (Python) schema. Reject (raise, log, do not write) if malformed.
- Flatten tag set into the `Keywords` string for Resolve (a curated subset — not every sub-field; Keywords should stay scannable):
  ```python
  keywords = list(subjects) + list(actions[:2]) + [shot_type, *colour_palette[:2], lighting_style.split()[0]]
  ```
  → `"hands, wheel, clay, shaping, close-up, warm-tungsten, earthy-ochre, key"`.
- Write sidecar first, then `SetMetadata("Keywords", …)` and `SetMetadata("Cairn Stage", "indexed")` via the serial Resolve worker.

### 4. Local vision fallback (NDA path)

If `cloud_vision_allowed: false`:

- Use OpenCLIP (ViT-L-14) via `open_clip_torch` to generate a dense embedding per frame.
- Compute cosine similarity against a curated tag vocabulary (~500 generic visual concepts) to produce top-k tags.
- Colour palette via k-means on frame pixels.
- Shot type via heuristics (face-detection confidence + bounding-box size → close vs wide).

Local output is necessarily coarser. `tags.confidence` ≤ 0.6 flagged so Stage 7 can down-weight. Full local vision is more of a stretch goal; if it's a significant lift, ship cloud-only + `"local not available for NDA shoots yet"` in v1 and log in `fix_plan.md`.

### 5. Idempotency

Skip any clip whose sidecar already has `"vision"` with the same `provider_model`. Re-run only if the user explicitly clicks "Re-index" in the UI.

## Sensors / Validations

- **LLM-as-judge (loop-back rule)**: after a batch of clips is tagged, sample 10% and ask Claude: "Here are the 5 frames and the tags we assigned. Do the tags accurately describe the visible content? Score 0–1 per field. Any that are < 0.5?" Log judge output; surface low-confidence tags in the review panel.
- **Schema validation** on every response; raise on malformed.
- **Frame-integrity check** — ffmpeg's return code + frame file existence + nonzero size. Failure → skip clip and log, don't crash the batch.
- **Resolve read-back** after `Keywords` write.
- **Cost estimate** — before a batch, show estimated tokens + $ (Claude vision pricing × frame count × clip count). Confirm threshold for batches > 100 clips.
- **Frame cleanup** — `/tmp` or `.cairn/frames/` cleaned up after upload. Verified by unit test.
- **pyrefly + ruff** clean.

### Taste-invariant cross-checks

- #1 (read-only source): ffmpeg `-ss <t> -i <clip> -frames:v 1` — extracts, never modifies.
- #3 (serial Resolve): tagging work fans out; Resolve writes drain through the serial worker.
- #6 (sidecar first): sidecar written before Resolve `SetMetadata`.
- #8 (cloud opt-in): frames only leave the machine when `cloud_vision_allowed: true`.

## Open questions

- **5 frames enough for minute-plus clips?** For short clips yes; for a 3-min tracking shot with lots of variation, probably not. Option: scale frame count with duration (5 + 1 per extra minute, capped at 12). Revisit after first real shoot.
- **Tool use vs JSON-in-text** — tool use is more robust. Default to tool use; keep JSON-in-text as a fallback for models that don't support tool use cleanly. Leaning: tool use only.
- **Motion-aware frames** — 5 uniform samples miss fast action. A v2 improvement: scene-detection (`ffmpeg -vf select='gt(scene,0.3)'`) to bias frames toward cut points. Out of scope for v1.
- **Keywords vs Metadata cell** — Resolve has many places tags can live. Keywords is searchable in the Smart Bins UI; that's what the editor wants. Confirm the Smart Bin use case with the user.
- **Local vision shipping weight** — OpenCLIP is ~600 MB + torch is ~800 MB. That's a heavy install for an NDA-only fallback. Consider downloadable addon pack triggered on first NDA project.
- **Persistent frames?** Useful for debugging + "why did this tag come back?" but expensive on disk. Gate behind `CAIRN_KEEP_FRAMES` env var; default: delete after use.

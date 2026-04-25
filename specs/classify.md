# specs/classify.md — Stage 2: Classify A-roll vs B-roll

## Purpose

For every imported video clip, decide whether it's A-roll (primary dialogue), B-roll (visual only or ambient), or Ambiguous (needs human review). Tag the clip in Resolve and apply the media-pool colour so the editor sees categories at a glance.

## Inputs

- Every clip in Media Pool bins `01_VIDEOS/A_CAM` and `01_VIDEOS/B_CAM` that has:
  - A sidecar with `"stages_complete"` containing `"ingest"`
  - `"stages_complete"` NOT containing `"classify"` (idempotency)
- The clip file itself (read via ffmpeg to extract audio).

**Not touched:** photos, assets. These are pre-coloured by ingest (Purple, Tan).

## Outputs

**Per clip:**
- **Classification label** written to Resolve `Keywords`: one of `A-ROLL`, `B-ROLL`, `AMBIGUOUS`.
- **Clip colour** set via `SetClipColor`:
  - `A-ROLL` → Blue
  - `B-ROLL` → Green
  - `AMBIGUOUS` → Yellow
- **Sidecar updated** with:
  ```json
  {
    "classification": "A-ROLL" | "B-ROLL" | "AMBIGUOUS",
    "vad": {
      "speech_ratio": 0.82,       // seconds of speech / total duration
      "speech_segments": [[0.4, 1.9], [2.3, 4.1], …],
      "model": "silero-vad",
      "model_version": "4.0",
      "threshold_used": 0.5
    },
    "stages_complete": ["ingest", "classify"]
  }
  ```

**Review panel surface:**
- All clips classified `AMBIGUOUS` appear in a plugin panel with a scrubbable preview and two buttons (`Mark A-roll` / `Mark B-roll`). User decisions update both Resolve + sidecar and move the clip into its confirmed category.

## Algorithm

### 1. Audio extraction

Use vendored ffmpeg (`assets/bin/ffmpeg`) to decode audio to a temporary 16 kHz mono WAV. Silero VAD requires 16 kHz.

```
ffmpeg -i <clip> -ac 1 -ar 16000 -f wav -vn -y /tmp/cairn-<clip-id>.wav
```

Temporary files cleaned up after VAD completes. On failure, cleanup is guaranteed via `try/finally`.

### 2. VAD

Use Silero VAD (open source, ONNX runtime — runs locally with no network):

```python
import torch
model, utils = torch.hub.load('snakers4/silero-vad', 'silero_vad')
(get_speech_timestamps, _, read_audio, _, _) = utils
wav = read_audio("/tmp/cairn-<id>.wav", sampling_rate=16000)
ts = get_speech_timestamps(wav, model, threshold=0.5, sampling_rate=16000)
```

**Threshold:** start at `0.5` (Silero default). Lower = more permissive (more clips labelled A-roll); higher = more conservative (more Ambiguous).

### 3. Ratio + decision

```python
total_speech = sum(s['end'] - s['start'] for s in ts)  # samples
speech_ratio = total_speech / len(wav)                 # 0.0 – 1.0
```

**Classification thresholds** (tunable; start conservative):

| `speech_ratio` | Label |
|---|---|
| `≥ 0.60` | `A-ROLL` |
| `≤ 0.05` | `B-ROLL` |
| `0.05 < ratio < 0.60` | `AMBIGUOUS` |

Rationale: the ambiguous middle captures clips with incidental chatter, studio walkthroughs, overheard voices, or clips where the mic picked up the interviewer's prompt but the interviewee hasn't started yet. Better to ask the user than silently mis-tag.

Thresholds live in a single constants block at the top of `classify.py` and are documented in `AGENT.md` once tuned against the sample shoot.

### 4. Silent clips

If `speech_ratio == 0.0` and audio is genuinely silent (no detected speech segments), label `B-ROLL` — these are typically set-ups, pickups, cutaway shots.

If audio file can't be extracted (e.g., clip has no audio stream), label `B-ROLL` and note `"vad.audio_stream": false` in the sidecar.

### 5. Write

**Sidecar first**, then Resolve: update `<clip-id>.json`, then call `SetMetadata("Keywords", …)` and `SetClipColor(…)` via the single Resolve worker.

### 6. Review panel

The UI queries all clips with `"classification": "AMBIGUOUS"` and renders them in a panel:

- Thumbnail (from Resolve `GetClipProperty("Thumbnail")` or ffmpeg frame at 50%)
- Filename
- `speech_ratio` value (helps the user decide)
- Two buttons: `A-roll` / `B-roll`
- "Play in Resolve" button → sets playhead to clip start

User confirmation writes `"classification": "<confirmed>"` and `"user_confirmed": true` into the sidecar, and updates Resolve.

## Sensors / Validations

- **VAD sanity test** (pytest): feed in a known speech-heavy WAV, assert `speech_ratio > 0.5`; feed in silence, assert `speech_ratio < 0.05`.
- **Threshold stability test**: regression fixtures (5–10 real clips, ground-truth labels) — any threshold change must not regress accuracy more than 1 label.
- **Resolve read-back**: after writing, re-query `Keywords` and `ClipColor`; assert they match what was written.
- **No-network assertion**: `classify.py` runs offline. Test harness blocks outbound network and asserts pass.
- **Idempotency**: second run on a classified clip is a no-op.
- **Colour validity**: every colour string is validated against a known-good enum at call time; invalid colour → raise, never silently skip.

### Taste-invariant cross-checks

- #1 / #2 (read-only on source): ffmpeg invocation uses `-i <clip>` with output to `/tmp` only. Source is never touched.
- #3 (serial Resolve): classification loop extracts + VAD's in parallel (CPU-bound), but Resolve writes drain through the serial worker.
- #7 (flag ambiguous): entire point of the Ambiguous category.

## Open questions

- **Ideal thresholds** — 0.60 / 0.05 are educated guesses. Calibrate against Aidan's sample shoot + two other projects before shipping.
- **Silero VAD v4 vs v5** — v5 is newer but has occasional false-positives on music. Pin to v4 for now; revisit if misclassifications surface.
- **Clips with music + dialogue** (e.g. interview with B-roll ambient mix) — VAD will flag as speech. Is that right? Probably yes for v1 (it *does* have dialogue) — user can override via the review panel.
- **Multi-track audio** (e.g. lav on track 1, shotgun on track 2) — current plan: downmix to mono. A smarter v2 could VAD each track separately and flag "dialogue on track 1 only" to inform later audio routing. Out of scope for v1.
- **Long clips** (>10 min talking heads) — VAD runs in ~1–2 s per file per `spec.md`; verify against a 30-min test clip.

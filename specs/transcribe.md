# specs/transcribe.md — Stage 3: Transcribe A-roll

## Purpose

For every clip labelled `A-ROLL` (including user-confirmed ambiguous clips), produce a word-level, speaker-diarised transcript. Attach it to the clip as a Resolve subtitle track so the editor can scan dialogue in the clip preview. Save a JSON sidecar so re-runs don't re-bill.

## Inputs

- Every clip whose sidecar has:
  - `"classification": "A-ROLL"` (either VAD-decided or user-confirmed via the Ambiguous review panel)
  - `"stages_complete"` NOT containing `"transcribe"`
- User's `ASSEMBLYAI_API_KEY` (retrieved from keychain via keytar)
- Per-project opt-in flag: `cloud_transcription_allowed: true | false`
  - `true` → AssemblyAI Universal-2 (default)
  - `false` → local `faster-whisper` (for NDA'd client work)

## Outputs

**Per clip:**
- **Subtitle track** attached in Resolve:
  - Created via `MediaPoolItem.AddSubTitle(file_path)` using a generated `.srt`
  - Speakers labelled (e.g., "Speaker 1: Lauren", "Speaker 2: Interviewer") — user can edit names in the UI; edits propagate to all of that speaker's captions.
- **Sidecar updated** with full transcript JSON:
  ```json
  {
    "transcript": {
      "provider": "assemblyai" | "whisper",
      "provider_model": "universal-2" | "faster-whisper-large-v3",
      "language": "en",
      "speakers": [
        {"id": "A", "label": "Lauren"},
        {"id": "B", "label": "Interviewer"}
      ],
      "words": [
        {"text": "So", "start": 0.40, "end": 0.62, "speaker": "A", "confidence": 0.98},
        …
      ],
      "utterances": [
        {"speaker": "A", "start": 0.40, "end": 12.84, "text": "So I started at 19..."},
        …
      ],
      "srt_path": "04_EXPORT/.cairn/subtitles/<clip-id>.srt"
    },
    "stages_complete": ["ingest", "classify", "transcribe"]
  }
  ```
- **SRT file** written to `04_EXPORT/.cairn/subtitles/<clip-id>.srt` — referenced by both the sidecar and the Resolve subtitle track.

## Algorithm

### Provider selection

```python
if sidecar["project_brief"].get("cloud_transcription_allowed", True):
    result = assemblyai_transcribe(audio_path, api_key)
else:
    result = whisper_transcribe(audio_path)
```

### 1a. AssemblyAI path (default)

- Upload the audio (or pass a signed URL if we pre-upload; v1 uses direct file upload via their SDK).
- Request Universal-2 model with `speaker_labels=True`, `word_boost=[<project-specific terms from brief>]` if the brief is available (Stage 5+).
- Poll until `status == "completed"`.
- Parse response into the sidecar shape above.

**Minimal request shape (pseudocode):**
```python
import assemblyai as aai
aai.settings.api_key = key
transcriber = aai.Transcriber()
config = aai.TranscriptionConfig(
    speaker_labels=True,
    speech_model=aai.SpeechModel.universal,
    word_boost=word_boost,
)
transcript = transcriber.transcribe(audio_path, config=config)
```

**Word-level output mapping:**
- AssemblyAI `words[]` → our `words[]` directly; field renames as noted above.
- AssemblyAI `utterances[]` (from speaker labels) → our `utterances[]`.
- Speaker IDs are AssemblyAI's `A`, `B`, `C`… We preserve them and let the user label in the UI.

### 1b. Whisper fallback

- Use `faster-whisper` (CTranslate2 runtime, CPU or GPU).
- Model: `large-v3` by default; downgradable to `medium` if the user opts in via settings for speed.
- No native diarisation in Whisper — for NDA work we run `pyannote.audio` locally **only if** the user has explicitly enabled it (adds ~2 GB of model weights). Otherwise: single-speaker output with `speaker: null` everywhere.

**Note in `fix_plan.md` at Session 4 kickoff:** decide whether to ship pyannote in the default install or as a downloadable addon.

### 2. SRT generation

From `utterances[]`:

```
1
00:00:00,400 --> 00:00:12,840
Speaker 1 (Lauren): So I started at 19 after a pottery taster at uni...
```

Speaker label prepended when diarisation is available. Numbering sequential. Timestamps SRT format (`HH:MM:SS,mmm`).

### 3. Resolve attach

```python
clip.AddSubTitle(srt_path)
clip.SetMetadata("Cairn Stage", "transcribed")
```

Single Resolve worker as always.

### 4. Idempotency + re-ingest

If sidecar already has `"transcript"` with the same `provider` and `provider_model`, **skip the API call entirely** — this is the "don't re-bill" guarantee.

If the user changes provider (cloud ↔ local), re-transcribe is a conscious decision — surface a confirmation in the UI ("This will use ~45 min of AssemblyAI credit. Continue?").

## Sensors / Validations

- **Schema validation** — JSON responses validated with Zod (Node side) and a dataclass (Python side) before any write. Malformed responses → raise and log, do not write partial data.
- **SRT round-trip test** — parse the SRT we wrote, assert timestamps match `words[]`.
- **Resolve read-back** — after `AddSubTitle`, re-query the clip's subtitle tracks and assert our file is there.
- **Bill-safety test** — two consecutive runs on the same clip make exactly one API call. Integration test with a mocked AssemblyAI client.
- **Cost estimate** — before starting a batch, the UI shows estimated minutes-to-be-billed; user must confirm for batches >30 min.
- **No-PII-leak in prompts** — the transcriber itself has no prompt, but the SRT writer must never embed API keys or internal paths in output.
- **pyrefly + ruff clean** on `src/workers/transcribe.py`.

### Taste-invariant cross-checks

- #1 (never modify source): audio is extracted to `/tmp` (or re-used from classify stage if still present). Source clip is never touched.
- #3 (serial Resolve): subtitle attachment through the single worker.
- #6 (sidecar first): SRT + sidecar JSON written before `AddSubTitle`.
- #8 (no cloud without opt-in): AssemblyAI path gated on the per-project `cloud_transcription_allowed` flag. Default in the brief flow is `true`; user can flip it before Stage 3 runs. For NDA shoots, the brief flow prompts explicitly.

## Open questions

- **Per-project vs per-clip opt-in for cloud transcription** — current plan is per-project. Is that the right granularity? Probably yes; a single shoot either is or isn't under NDA.
- **Word boost from brief** — if the brief hasn't been filled yet, transcription runs without it. Do we offer to re-transcribe after the brief? Decision: no — spec says brief opens *after* indexing. Word boost is nice-to-have, not required.
- **Multiple languages** — v1 assumes English. Detect non-English via AssemblyAI's language detection and surface to user? Current plan: yes, detect and display; actual multilingual transcription is out of scope for v1.
- **Subtitle styling in Resolve** — `AddSubTitle` imports as a track; Resolve controls styling. Do we need to also write a burned-in preview option? v2.
- **Live progress** — AssemblyAI returns partial transcripts. Do we stream them to the UI? Nice UX but adds complexity; v1 shows a spinner and final result.
- **Whisper model size** — `large-v3` is ~3 GB, slow on CPU. Offer a "fast but slightly less accurate" option (`medium`)? Document in `fix_plan.md` when Stage 3 lands.

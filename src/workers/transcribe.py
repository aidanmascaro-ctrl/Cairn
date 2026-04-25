"""
Stage 3 -- Transcribe A-roll clips.

Provider selection (per-project):
  cloud_allowed=True  → AssemblyAI Universal-2 (speaker-diarised, word-level)
  cloud_allowed=False → faster-whisper large-v3 (local, no cloud, no diarisation)

Idempotent: clips whose sidecar already has a matching provider+model are skipped.
SRT written to 04_EXPORT/.cairn/subtitles/<clip-id>.srt before Resolve attach.
No Resolve calls here — those go through bridge.py → resolve_api.add_subtitle_track.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# ── Constants ─────────────────────────────────────────────────────────────────

PROVIDER_ASSEMBLYAI = "assemblyai"
PROVIDER_WHISPER = "whisper"
ASSEMBLYAI_MODEL = "universal-2"
WHISPER_MODEL_DEFAULT = "large-v3"

# AssemblyAI Universal-2 pricing (as of 2024): $0.37 / hr = ~$0.0062 / min
ASSEMBLYAI_COST_PER_MIN_USD: float = 0.0062

_VIDEO_EXTS: set[str] = {".mp4", ".mov", ".mxf", ".avi", ".m2ts"}

# ── ffmpeg / ffprobe paths ─────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_FFMPEG_PATH: Path = _PROJECT_ROOT / "assets" / "bin" / "ffmpeg"
if not _FFMPEG_PATH.exists():
    _FFMPEG_PATH = Path("ffmpeg")

_FFPROBE_PATH: Path = _PROJECT_ROOT / "assets" / "bin" / "ffprobe"
if not _FFPROBE_PATH.exists():
    _FFPROBE_PATH = Path("ffprobe")


# ── Errors ────────────────────────────────────────────────────────────────────


class TranscriptionError(Exception):
    """Raised when transcription fails unrecoverably for a clip."""


# ── Duration probe ─────────────────────────────────────────────────────────────


def get_clip_duration_sec(clip_path: str) -> float:
    """Return clip duration in seconds, or 0.0 on failure."""
    try:
        result = subprocess.run(
            [
                str(_FFPROBE_PATH),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                clip_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return float(result.stdout.strip())
    except Exception:
        pass
    # Fallback: parse Duration line from ffmpeg stderr
    try:
        result = subprocess.run(
            [str(_FFMPEG_PATH), "-i", clip_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", result.stderr)
        if m:
            h, mn, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            return h * 3600 + mn * 60 + s
    except Exception:
        pass
    return 0.0


# ── Clip scanning ─────────────────────────────────────────────────────────────


def scan_aroll_clips(shoot_folder: str | Path) -> list[str]:
    """
    Return absolute paths of all A-ROLL clips in the organised folder
    that have not yet been transcribed.
    A clip qualifies when its sidecar has classification='A-ROLL' and
    'transcribe' is not in stages_complete.
    """
    import sidecar as sid

    folder = Path(shoot_folder)
    eligible: list[str] = []
    for cam_dir in ("A_CAM", "B_CAM", "C_CAM"):
        target = folder / "01_VIDEOS" / cam_dir
        if not target.is_dir():
            continue
        for f in sorted(target.iterdir()):
            if not (f.is_file() and f.suffix.lower() in _VIDEO_EXTS):
                continue
            sc = sid.read_clip(shoot_folder, str(f))
            if sc is None:
                continue
            if sc.get("classification") != "A-ROLL":
                continue
            if "transcribe" in sc.get("stages_complete", []):
                continue
            eligible.append(str(f))
    return eligible


def estimate_minutes(clip_paths: list[str]) -> float:
    """Return total audio duration in minutes across all clips."""
    total_sec = sum(get_clip_duration_sec(p) for p in clip_paths)
    return round(total_sec / 60.0, 2)


# ── SRT generation ────────────────────────────────────────────────────────────


def _format_srt_time(ms: int) -> str:
    """Convert milliseconds to SRT timestamp HH:MM:SS,mmm."""
    ms_part = ms % 1000
    total_s = ms // 1000
    hh, rem = divmod(total_s, 3600)
    mm, ss = divmod(rem, 60)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms_part:03d}"


def write_srt(utterances: list[dict[str, Any]], srt_path: Path) -> None:
    """Write utterances list to an SRT file. Speaker label is prepended when present."""
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for idx, utt in enumerate(utterances, start=1):
        start = _format_srt_time(int(utt.get("start", 0)))
        end = _format_srt_time(int(utt.get("end", 0)))
        speaker = utt.get("speaker")
        text = (utt.get("text") or "").strip()
        prefix = f"Speaker {speaker}: " if speaker else ""
        lines.extend([str(idx), f"{start} --> {end}", f"{prefix}{text}", ""])
    srt_path.write_text("\n".join(lines), encoding="utf-8")


# ── AssemblyAI provider ───────────────────────────────────────────────────────


def transcribe_with_assemblyai(
    clip_path: str,
    api_key: str,
    word_boost: list[str] | None = None,
) -> dict[str, Any]:
    """
    Transcribe with AssemblyAI Universal-2 (speaker-diarised, word-level).
    Raises TranscriptionError on API or import failure.
    """
    try:
        import assemblyai as aai
    except ImportError as exc:
        raise TranscriptionError("assemblyai package not installed") from exc

    aai.settings.api_key = api_key
    transcriber = aai.Transcriber()
    config = aai.TranscriptionConfig(
        speaker_labels=True,
        speech_model=aai.SpeechModel.universal,
        word_boost=word_boost or [],
    )

    try:
        transcript = transcriber.transcribe(clip_path, config=config)
    except Exception as exc:
        raise TranscriptionError(f"AssemblyAI transcription failed: {exc}") from exc

    if transcript.status == aai.TranscriptStatus.error:
        raise TranscriptionError(f"AssemblyAI error: {transcript.error}")

    words: list[dict[str, Any]] = [
        {
            "text": w.text,
            "start": w.start or 0,
            "end": w.end or 0,
            "speaker": w.speaker,
            "confidence": w.confidence or 0.0,
        }
        for w in (transcript.words or [])
    ]

    utterances: list[dict[str, Any]] = [
        {
            "speaker": u.speaker,
            "start": u.start,
            "end": u.end,
            "text": u.text,
        }
        for u in (transcript.utterances or [])
    ]

    speaker_ids = list(dict.fromkeys(u["speaker"] for u in utterances if u.get("speaker")))
    speakers = [{"id": sid, "label": f"Speaker {sid}"} for sid in speaker_ids]

    return {
        "provider": PROVIDER_ASSEMBLYAI,
        "provider_model": ASSEMBLYAI_MODEL,
        "language": transcript.language_code or "en",
        "speakers": speakers,
        "words": words,
        "utterances": utterances,
    }


# ── faster-whisper fallback ───────────────────────────────────────────────────


def transcribe_with_whisper(
    clip_path: str,
    model_size: str = WHISPER_MODEL_DEFAULT,
) -> dict[str, Any]:
    """
    Transcribe locally with faster-whisper. No speaker diarisation.
    Utterances map 1:1 to Whisper segments; speaker is None throughout.
    Raises TranscriptionError on import or runtime failure.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionError("faster-whisper package not installed") from exc

    try:
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments_gen, info = model.transcribe(clip_path, word_timestamps=True)
    except Exception as exc:
        raise TranscriptionError(f"faster-whisper failed: {exc}") from exc

    words: list[dict[str, Any]] = []
    utterances: list[dict[str, Any]] = []

    for segment in segments_gen:
        for w in segment.words or []:
            words.append(
                {
                    "text": w.word.strip(),
                    "start": int(w.start * 1000),
                    "end": int(w.end * 1000),
                    "speaker": None,
                    "confidence": float(w.probability),
                }
            )
        utterances.append(
            {
                "speaker": None,
                "start": int(segment.start * 1000),
                "end": int(segment.end * 1000),
                "text": segment.text.strip(),
            }
        )

    return {
        "provider": PROVIDER_WHISPER,
        "provider_model": model_size,
        "language": info.language,
        "speakers": [],
        "words": words,
        "utterances": utterances,
    }


# ── Speaker label update ──────────────────────────────────────────────────────


def update_speaker_labels(
    shoot_folder: str | Path,
    speaker_labels: dict[str, str],
) -> int:
    """
    Merge human-readable speaker names into every transcribed clip's sidecar.
    speaker_labels maps speaker ID (e.g. 'A') to user-assigned name (e.g. 'Lauren').
    Returns the number of sidecars updated.
    """
    import sidecar as sid

    folder = Path(shoot_folder)
    updated = 0
    for cam_dir in ("A_CAM", "B_CAM", "C_CAM"):
        target = folder / "01_VIDEOS" / cam_dir
        if not target.is_dir():
            continue
        for f in target.iterdir():
            if not (f.is_file() and f.suffix.lower() in _VIDEO_EXTS):
                continue
            sc = sid.read_clip(shoot_folder, str(f))
            if sc is None or "transcribe" not in sc.get("stages_complete", []):
                continue
            transcript = sc.get("transcript", {})
            speakers = transcript.get("speakers", [])
            changed = False
            for spk in speakers:
                new_label = speaker_labels.get(spk["id"])
                if new_label and spk.get("label") != new_label:
                    spk["label"] = new_label
                    changed = True
            if changed:
                transcript["speakers"] = speakers
                sid.merge_clip(shoot_folder, str(f), updates={"transcript": transcript})
                updated += 1
    return updated


# ── Main pipeline ─────────────────────────────────────────────────────────────


def transcribe_all(
    clip_paths: list[str],
    shoot_folder: str | Path,
    api_key: str,
    cloud_allowed: bool,
    word_boost: list[str] | None = None,
) -> Iterator[dict[str, Any]]:
    """
    Transcribe each clip, yielding progress events then transcribe_complete.
    Idempotent: clips already transcribed with the same provider+model are skipped.
    Writes SRT + sidecar before yielding each clip event.
    """
    import sidecar as sid

    provider = PROVIDER_ASSEMBLYAI if cloud_allowed else PROVIDER_WHISPER
    provider_model = ASSEMBLYAI_MODEL if cloud_allowed else WHISPER_MODEL_DEFAULT
    total = len(clip_paths)
    counts: dict[str, int] = {"transcribed": 0, "skipped": 0, "errors": 0}

    yield {"event": "transcribe_started", "total_clips": total, "provider": provider}

    for clip_path in clip_paths:
        cid = sid.clip_id(clip_path)
        srt_dir = sid.sidecar_root(shoot_folder).parent / "subtitles"
        srt_path = srt_dir / f"{cid}.srt"

        existing = sid.read_clip(shoot_folder, clip_path)
        if existing:
            ex_t = existing.get("transcript", {})
            if (
                ex_t.get("provider") == provider
                and ex_t.get("provider_model") == provider_model
                and "transcribe" in existing.get("stages_complete", [])
            ):
                counts["skipped"] += 1
                yield {"event": "clip_skipped", "file": clip_path, "reason": "already transcribed"}
                continue

        try:
            if cloud_allowed:
                data = transcribe_with_assemblyai(clip_path, api_key, word_boost)
            else:
                data = transcribe_with_whisper(clip_path)
        except TranscriptionError as exc:
            counts["errors"] += 1
            print(f"[transcribe.py] {clip_path}: {exc}", file=sys.stderr)
            yield {"event": "clip_error", "file": clip_path, "reason": str(exc)}
            continue

        write_srt(data["utterances"], srt_path)
        data["srt_path"] = str(srt_path)

        sid.merge_clip(
            shoot_folder,
            clip_path,
            updates={"transcript": data},
            new_stage="transcribe",
        )

        counts["transcribed"] += 1
        yield {
            "event": "clip_transcribed",
            "file": clip_path,
            "provider": provider,
            "utterance_count": len(data["utterances"]),
            "word_count": len(data["words"]),
            "srt_path": str(srt_path),
            "speaker_count": len(data["speakers"]),
        }

    yield {
        "event": "transcribe_complete",
        "total": total,
        "transcribed": counts["transcribed"],
        "skipped": counts["skipped"],
        "errors": counts["errors"],
    }

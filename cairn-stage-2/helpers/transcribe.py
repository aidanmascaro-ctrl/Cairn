"""faster-whisper transcription → word-timestamped record. Single-speaker v1."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any


def segments_to_record(segments: Iterable[Any], info: Any) -> dict:
    words: list[dict] = []
    seg_list: list[dict] = []
    for seg in segments:
        seg_list.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
        for w in seg.words or []:
            words.append({"text": w.word.strip(), "start": w.start, "end": w.end})
    return {
        "language": getattr(info, "language", None),
        "duration": getattr(info, "duration", None),
        "segments": seg_list,
        "words": words,
    }


def transcribe_file(file_path: str | Path, model: Any, **kw: Any) -> dict:
    # `model` is a faster-whisper WhisperModel (duck-typed: anything with a
    # `.transcribe(path, word_timestamps=...) -> (segments, info)` method).
    segments, info = model.transcribe(str(file_path), word_timestamps=True, **kw)
    return segments_to_record(segments, info)


def load_model(size: str = "base", device: str = "auto", compute_type: str = "int8") -> Any:
    from faster_whisper import WhisperModel  # imported lazily so unit tests need no model

    return WhisperModel(size, device=device, compute_type=compute_type)

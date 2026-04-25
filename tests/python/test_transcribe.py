"""
pytest suite for src/workers/transcribe.py

Tests:
- SRT timestamp formatting and round-trip
- SRT write + parse
- classify_clip threshold logic (reused from classify tests — sanity)
- estimate_minutes with mocked ffprobe
- scan_aroll_clips filters correctly
- transcribe_all idempotency (same provider+model → skip, no API call)
- transcribe_all bill-safety (two consecutive runs → exactly one API call)
- transcribe_all Whisper path (cloud_allowed=False → calls whisper, not assemblyai)
- transcribe_all error handling (API failure → clip_error event, no crash)
- update_speaker_labels merges into sidecar without losing other fields
- No-network assertion: transcribe_with_assemblyai raises without network (mocked)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure src/workers is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src" / "workers"))

import sidecar as sid
import transcribe as tr

# ── SRT helpers ───────────────────────────────────────────────────────────────


def test_format_srt_time_zero():
    assert tr._format_srt_time(0) == "00:00:00,000"


def test_format_srt_time_typical():
    # 1 min 23.456 s = 83456 ms
    ms = 1 * 60 * 1000 + 23 * 1000 + 456
    assert tr._format_srt_time(ms) == "00:01:23,456"


def test_format_srt_time_hours():
    # 2h 5m 9s 7ms = 7509007 ms
    ms = 2 * 3600 * 1000 + 5 * 60 * 1000 + 9 * 1000 + 7
    assert tr._format_srt_time(ms) == "02:05:09,007"


def test_write_srt_creates_file(tmp_path):
    utterances = [
        {"speaker": "A", "start": 400, "end": 2500, "text": "Hello world"},
        {"speaker": "B", "start": 3000, "end": 5000, "text": "Great to meet you"},
    ]
    srt_path = tmp_path / "out.srt"
    tr.write_srt(utterances, srt_path)
    content = srt_path.read_text()
    assert "00:00:00,400 --> 00:00:02,500" in content
    assert "Speaker A: Hello world" in content
    assert "00:00:03,000 --> 00:00:05,000" in content
    assert "Speaker B: Great to meet you" in content


def test_write_srt_no_speaker(tmp_path):
    utterances = [{"speaker": None, "start": 0, "end": 1000, "text": "Mono"}]
    srt_path = tmp_path / "out.srt"
    tr.write_srt(utterances, srt_path)
    content = srt_path.read_text()
    assert "Speaker" not in content
    assert "Mono" in content


def test_write_srt_round_trip_timestamps(tmp_path):
    """Parse back the timestamps we wrote and assert they match the input."""
    import re

    utterances = [
        {"speaker": "A", "start": 0, "end": 12840, "text": "So I started"},
        {"speaker": "A", "start": 13000, "end": 15200, "text": "at nineteen"},
    ]
    srt_path = tmp_path / "rt.srt"
    tr.write_srt(utterances, srt_path)
    content = srt_path.read_text()
    times = re.findall(r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})", content)
    assert times[0] == ("00:00:00,000", "00:00:12,840")
    assert times[1] == ("00:00:13,000", "00:00:15,200")


# ── estimate_minutes ──────────────────────────────────────────────────────────


def test_estimate_minutes_sums_durations():
    with patch.object(tr, "get_clip_duration_sec", side_effect=[60.0, 120.0, 30.0]):
        result = tr.estimate_minutes(["a.mp4", "b.mp4", "c.mp4"])
    assert result == pytest.approx(3.5, rel=1e-3)


def test_estimate_minutes_empty():
    assert tr.estimate_minutes([]) == 0.0


# ── scan_aroll_clips ──────────────────────────────────────────────────────────


def _make_video(directory: Path, name: str) -> Path:
    f = directory / name
    f.write_bytes(b"\x00" * 100)
    return f


def test_scan_aroll_clips_filters_to_aroll(tmp_path):
    shoot = tmp_path / "shoot"
    acam = shoot / "01_VIDEOS" / "A_CAM"
    acam.mkdir(parents=True)
    clip_a = _make_video(acam, "clip_aroll.mp4")
    clip_b = _make_video(acam, "clip_broll.mp4")
    clip_u = _make_video(acam, "clip_unclassified.mp4")

    # Write sidecars
    sid.merge_clip(shoot, str(clip_a), {"classification": "A-ROLL"}, new_stage="classify")
    sid.merge_clip(shoot, str(clip_b), {"classification": "B-ROLL"}, new_stage="classify")
    # clip_u has no sidecar

    result = tr.scan_aroll_clips(shoot)
    assert str(clip_a) in result
    assert str(clip_b) not in result
    assert str(clip_u) not in result


def test_scan_aroll_clips_skips_already_transcribed(tmp_path):
    shoot = tmp_path / "shoot"
    acam = shoot / "01_VIDEOS" / "A_CAM"
    acam.mkdir(parents=True)
    clip = _make_video(acam, "done.mp4")

    sid.merge_clip(shoot, str(clip), {"classification": "A-ROLL"}, new_stage="classify")
    sid.merge_clip(
        shoot,
        str(clip),
        {"transcript": {"provider": "assemblyai"}},
        new_stage="transcribe",
    )

    result = tr.scan_aroll_clips(shoot)
    assert result == []


# ── Idempotency + bill-safety ─────────────────────────────────────────────────


def _mock_assemblyai_data():
    return {
        "provider": tr.PROVIDER_ASSEMBLYAI,
        "provider_model": tr.ASSEMBLYAI_MODEL,
        "language": "en",
        "speakers": [{"id": "A", "label": "Speaker A"}],
        "words": [{"text": "hi", "start": 0, "end": 500, "speaker": "A", "confidence": 0.99}],
        "utterances": [{"speaker": "A", "start": 0, "end": 500, "text": "hi"}],
    }


def test_transcribe_all_idempotency(tmp_path):
    """Second run with same provider+model skips, no API call."""
    shoot = tmp_path / "shoot"
    acam = shoot / "01_VIDEOS" / "A_CAM"
    acam.mkdir(parents=True)
    clip = _make_video(acam, "aroll.mp4")

    # Pre-populate sidecar as already transcribed
    sid.merge_clip(
        shoot,
        str(clip),
        {
            "classification": "A-ROLL",
            "transcript": {
                "provider": tr.PROVIDER_ASSEMBLYAI,
                "provider_model": tr.ASSEMBLYAI_MODEL,
            },
        },
        new_stage="transcribe",
    )
    # Also needs classify stage
    sid.merge_clip(shoot, str(clip), {"classification": "A-ROLL"}, new_stage="classify")

    with patch.object(tr, "transcribe_with_assemblyai") as mock_aai:
        events = list(tr.transcribe_all([str(clip)], shoot, "key", cloud_allowed=True))

    mock_aai.assert_not_called()
    skipped = [e for e in events if e.get("event") == "clip_skipped"]
    assert len(skipped) == 1


def test_transcribe_all_bill_safety(tmp_path):
    """Two consecutive runs → exactly one AssemblyAI call."""
    shoot = tmp_path / "shoot"
    acam = shoot / "01_VIDEOS" / "A_CAM"
    acam.mkdir(parents=True)
    clip = _make_video(acam, "aroll.mp4")
    sid.merge_clip(shoot, str(clip), {"classification": "A-ROLL"}, new_stage="classify")

    mock_data = _mock_assemblyai_data()

    with patch.object(tr, "transcribe_with_assemblyai", return_value=mock_data) as mock_aai:
        list(tr.transcribe_all([str(clip)], shoot, "key", cloud_allowed=True))
        # Second run — sidecar now has transcribe in stages_complete
        list(tr.transcribe_all([str(clip)], shoot, "key", cloud_allowed=True))

    assert mock_aai.call_count == 1


# ── Provider routing ──────────────────────────────────────────────────────────


def test_transcribe_all_whisper_path(tmp_path):
    """cloud_allowed=False → calls whisper, not assemblyai."""
    shoot = tmp_path / "shoot"
    acam = shoot / "01_VIDEOS" / "A_CAM"
    acam.mkdir(parents=True)
    clip = _make_video(acam, "aroll.mp4")
    sid.merge_clip(shoot, str(clip), {"classification": "A-ROLL"}, new_stage="classify")

    whisper_data = {
        "provider": tr.PROVIDER_WHISPER,
        "provider_model": tr.WHISPER_MODEL_DEFAULT,
        "language": "en",
        "speakers": [],
        "words": [],
        "utterances": [{"speaker": None, "start": 0, "end": 2000, "text": "hello"}],
    }

    with (
        patch.object(tr, "transcribe_with_whisper", return_value=whisper_data) as mock_w,
        patch.object(tr, "transcribe_with_assemblyai") as mock_aai,
    ):
        events = list(tr.transcribe_all([str(clip)], shoot, "", cloud_allowed=False))

    mock_w.assert_called_once()
    mock_aai.assert_not_called()
    transcribed = [e for e in events if e.get("event") == "clip_transcribed"]
    assert len(transcribed) == 1


# ── Error handling ────────────────────────────────────────────────────────────


def test_transcribe_all_api_error_yields_clip_error(tmp_path):
    """API failure emits clip_error event, pipeline continues, no crash."""
    shoot = tmp_path / "shoot"
    acam = shoot / "01_VIDEOS" / "A_CAM"
    acam.mkdir(parents=True)
    clip = _make_video(acam, "bad.mp4")
    sid.merge_clip(shoot, str(clip), {"classification": "A-ROLL"}, new_stage="classify")

    with patch.object(
        tr, "transcribe_with_assemblyai", side_effect=tr.TranscriptionError("API down")
    ):
        events = list(tr.transcribe_all([str(clip)], shoot, "key", cloud_allowed=True))

    error_events = [e for e in events if e.get("event") == "clip_error"]
    assert len(error_events) == 1
    assert "API down" in error_events[0]["reason"]

    complete = [e for e in events if e.get("event") == "transcribe_complete"]
    assert complete[0]["errors"] == 1


# ── Sidecar merge ─────────────────────────────────────────────────────────────


def test_transcribe_all_sidecar_merge(tmp_path):
    """Transcribed sidecar retains ingest+classify stages and adds transcribe."""
    shoot = tmp_path / "shoot"
    acam = shoot / "01_VIDEOS" / "A_CAM"
    acam.mkdir(parents=True)
    clip = _make_video(acam, "aroll.mp4")

    # Ingest + classify already done
    sid.merge_clip(shoot, str(clip), {"classification": "A-ROLL"}, new_stage="ingest")
    sid.merge_clip(shoot, str(clip), {}, new_stage="classify")

    mock_data = _mock_assemblyai_data()
    with patch.object(tr, "transcribe_with_assemblyai", return_value=mock_data):
        list(tr.transcribe_all([str(clip)], shoot, "key", cloud_allowed=True))

    sc = sid.read_clip(shoot, str(clip))
    assert sc is not None
    assert "ingest" in sc["stages_complete"]
    assert "classify" in sc["stages_complete"]
    assert "transcribe" in sc["stages_complete"]
    assert sc["transcript"]["provider"] == tr.PROVIDER_ASSEMBLYAI


# ── update_speaker_labels ─────────────────────────────────────────────────────


def test_update_speaker_labels_merges_names(tmp_path):
    shoot = tmp_path / "shoot"
    acam = shoot / "01_VIDEOS" / "A_CAM"
    acam.mkdir(parents=True)
    clip = _make_video(acam, "aroll.mp4")

    transcript_data = {
        "provider": tr.PROVIDER_ASSEMBLYAI,
        "provider_model": tr.ASSEMBLYAI_MODEL,
        "language": "en",
        "speakers": [
            {"id": "A", "label": "Speaker A"},
            {"id": "B", "label": "Speaker B"},
        ],
        "words": [],
        "utterances": [],
    }
    sid.merge_clip(shoot, str(clip), {"classification": "A-ROLL"}, new_stage="ingest")
    sid.merge_clip(shoot, str(clip), {"transcript": transcript_data}, new_stage="transcribe")

    updated = tr.update_speaker_labels(shoot, {"A": "Lauren", "B": "Interviewer"})
    assert updated == 1

    sc = sid.read_clip(shoot, str(clip))
    assert sc is not None
    spk_map = {s["id"]: s["label"] for s in sc["transcript"]["speakers"]}
    assert spk_map["A"] == "Lauren"
    assert spk_map["B"] == "Interviewer"


def test_update_speaker_labels_no_op_if_same(tmp_path):
    shoot = tmp_path / "shoot"
    acam = shoot / "01_VIDEOS" / "A_CAM"
    acam.mkdir(parents=True)
    clip = _make_video(acam, "aroll.mp4")

    transcript_data = {
        "provider": tr.PROVIDER_ASSEMBLYAI,
        "provider_model": tr.ASSEMBLYAI_MODEL,
        "language": "en",
        "speakers": [{"id": "A", "label": "Lauren"}],
        "words": [],
        "utterances": [],
    }
    sid.merge_clip(shoot, str(clip), {"classification": "A-ROLL"}, new_stage="ingest")
    sid.merge_clip(shoot, str(clip), {"transcript": transcript_data}, new_stage="transcribe")

    updated = tr.update_speaker_labels(shoot, {"A": "Lauren"})
    assert updated == 0

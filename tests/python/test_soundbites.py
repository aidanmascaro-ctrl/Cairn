"""
pytest suite for src/workers/soundbites.py

Tests:
- sec_to_frame: correct rounding at various fps
- sec_to_tc: correct timecode formatting
- beat_type_to_color: known and unknown beat types
- validate_duration: rejects < 3s and > 60s, accepts valid
- render_marker_note: includes label, WHY, EXTRACT, ID; prepends [REVIEW] when flagged
- _has_non_nested_overlap: detects non-nested overlaps, allows nested, allows non-overlapping
- scan_aroll_clips: finds A-roll with transcript, skips B-roll, skips already-soundbited
- estimate_soundbites: correct clip_count and estimated cost
- _brief_summary: renders expected fields; handles empty brief
- _make_soundbite_id: correct format
- process_all: yields events, writes sidecar, brief-not-finalised guard
- process_all error handling: clip error yields clip_error event, continues
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src" / "workers"))

import sidecar as sid
import soundbites as sb

# ── sec_to_frame ──────────────────────────────────────────────────────────────


def test_sec_to_frame_24fps():
    assert sb.sec_to_frame(1.0, 24.0) == 24
    assert sb.sec_to_frame(0.5, 24.0) == 12


def test_sec_to_frame_rounding():
    # 1.04166... * 24 = 25.0 → 25
    assert sb.sec_to_frame(25 / 24, 24.0) == 25


def test_sec_to_frame_23976():
    # 10s at 23.976 fps
    assert sb.sec_to_frame(10.0, 23.976) == round(10.0 * 23.976)


def test_sec_to_frame_25fps():
    assert sb.sec_to_frame(4.0, 25.0) == 100


# ── sec_to_tc ─────────────────────────────────────────────────────────────────


def test_sec_to_tc_zero():
    assert sb.sec_to_tc(0.0, 24.0) == "00:00:00:00"


def test_sec_to_tc_one_second_24fps():
    assert sb.sec_to_tc(1.0, 24.0) == "00:00:01:00"


def test_sec_to_tc_minutes():
    assert sb.sec_to_tc(61.0, 25.0) == "00:01:01:00"


def test_sec_to_tc_hours():
    assert sb.sec_to_tc(3661.0, 24.0) == "01:01:01:00"


# ── beat_type_to_color ────────────────────────────────────────────────────────


def test_beat_type_to_color_known():
    assert sb.beat_type_to_color("origin-story") == "Green"
    assert sb.beat_type_to_color("emotional-peak") == "Red"
    assert sb.beat_type_to_color("factual") == "Blue"
    assert sb.beat_type_to_color("hook") == "Yellow"


def test_beat_type_to_color_unknown_fallback():
    assert sb.beat_type_to_color("made-up-type") == "Lemon"


# ── validate_duration ─────────────────────────────────────────────────────────


def test_validate_duration_too_short():
    with pytest.raises(ValueError, match="below minimum"):
        sb.validate_duration(0.0, 2.9)


def test_validate_duration_too_long():
    with pytest.raises(ValueError, match="exceeds maximum"):
        sb.validate_duration(0.0, 61.0)


def test_validate_duration_valid():
    sb.validate_duration(0.0, 15.0)  # should not raise
    sb.validate_duration(0.0, 3.0)  # exact minimum
    sb.validate_duration(0.0, 60.0)  # exact maximum


# ── render_marker_note ────────────────────────────────────────────────────────


def test_render_marker_note_basic():
    bite = {
        "label": "Origin story",
        "why_usable": "Strong hook",
        "extract": "I started at 19...",
        "id": "sb_abc_001",
        "beat_type": "origin-story",
        "confidence": 0.88,
    }
    note = sb.render_marker_note(bite)
    assert "Origin story" in note
    assert "WHY: Strong hook" in note
    assert "EXTRACT: I started at 19..." in note
    assert "ID: sb_abc_001" in note
    assert "TYPE: origin-story" in note
    assert "CONFIDENCE: 0.88" in note
    assert "[REVIEW]" not in note


def test_render_marker_note_flagged():
    bite = {
        "label": "Weak bite",
        "why_usable": "Borderline",
        "extract": "Something something",
        "id": "sb_xyz_002",
        "beat_type": "factual",
        "confidence": 0.4,
        "flagged_for_review": True,
    }
    note = sb.render_marker_note(bite)
    assert note.startswith("[REVIEW]")


# ── _has_non_nested_overlap ───────────────────────────────────────────────────


def _bite(in_sec: float, out_sec: float) -> dict:
    return {"in_sec": in_sec, "out_sec": out_sec, "label": f"{in_sec}-{out_sec}"}


def test_no_overlap():
    bites = [_bite(0, 10), _bite(15, 25), _bite(30, 40)]
    assert not sb._has_non_nested_overlap(bites)


def test_adjacent_no_overlap():
    bites = [_bite(0, 10), _bite(10, 20)]
    assert not sb._has_non_nested_overlap(bites)


def test_nested_no_flag():
    # bite 5-15 is nested inside 0-20
    bites = [_bite(0, 20), _bite(5, 15)]
    assert not sb._has_non_nested_overlap(bites)


def test_non_nested_overlap_flagged():
    # 0-15 and 10-25 overlap non-nestedly
    bites = [_bite(0, 15), _bite(10, 25)]
    assert sb._has_non_nested_overlap(bites)


# ── scan_aroll_clips ──────────────────────────────────────────────────────────


def test_scan_aroll_clips_finds_ready_clips(tmp_path):
    shoot = tmp_path / "shoot"
    clip_a = str(tmp_path / "a.mp4")
    Path(clip_a).write_bytes(b"\x00")

    sid.merge_clip(
        shoot,
        clip_a,
        {"classification": "A-ROLL"},
        new_stage="transcribe",
    )

    clips = sb.scan_aroll_clips(shoot)
    assert clip_a in clips


def test_scan_aroll_clips_skips_broll(tmp_path):
    shoot = tmp_path / "shoot"
    clip_b = str(tmp_path / "b.mp4")
    Path(clip_b).write_bytes(b"\x00")

    sid.merge_clip(shoot, clip_b, {"classification": "B-ROLL"}, new_stage="transcribe")

    clips = sb.scan_aroll_clips(shoot)
    assert clip_b not in clips


def test_scan_aroll_clips_skips_already_processed(tmp_path):
    shoot = tmp_path / "shoot"
    clip_a = str(tmp_path / "a.mp4")
    Path(clip_a).write_bytes(b"\x00")

    sid.merge_clip(
        shoot,
        clip_a,
        {"classification": "A-ROLL"},
        new_stage="transcribe",
    )
    sid.merge_clip(shoot, clip_a, {}, new_stage="soundbites")

    clips = sb.scan_aroll_clips(shoot)
    assert clip_a not in clips


def test_scan_aroll_clips_skips_no_transcript(tmp_path):
    shoot = tmp_path / "shoot"
    clip_a = str(tmp_path / "a.mp4")
    Path(clip_a).write_bytes(b"\x00")

    sid.merge_clip(shoot, clip_a, {"classification": "A-ROLL"})

    clips = sb.scan_aroll_clips(shoot)
    assert clip_a not in clips


# ── estimate_soundbites ───────────────────────────────────────────────────────


def test_estimate_soundbites():
    result = sb.estimate_soundbites(["a.mp4", "b.mp4", "c.mp4"])
    assert result["clip_count"] == 3
    assert result["estimated_cost_usd"] == pytest.approx(3 * sb.ANTHROPIC_COST_PER_CLIP_USD)


def test_estimate_soundbites_empty():
    result = sb.estimate_soundbites([])
    assert result["clip_count"] == 0
    assert result["estimated_cost_usd"] == 0.0


# ── _brief_summary ────────────────────────────────────────────────────────────


def test_brief_summary_full():
    brief = {
        "subject": {"name": "Lauren", "profession": "ceramicist"},
        "narrative_angle": "origin story",
        "tone": "warm",
        "themes": ["craft", "process"],
        "deliverables": [{"platform": "instagram_reel", "target_duration_sec": [60, 90]}],
    }
    summary = sb._brief_summary(brief)
    assert "Lauren" in summary
    assert "ceramicist" in summary
    assert "origin story" in summary
    assert "warm" in summary
    assert "craft" in summary
    assert "instagram_reel" in summary


def test_brief_summary_empty():
    summary = sb._brief_summary({})
    assert "Brief not yet filled" in summary


# ── _make_soundbite_id ────────────────────────────────────────────────────────


def test_make_soundbite_id_format():
    cid = "abc123"
    assert sb._make_soundbite_id(cid, 0) == "sb_abc123_001"
    assert sb._make_soundbite_id(cid, 4) == "sb_abc123_005"


# ── process_all: brief not finalised ─────────────────────────────────────────


def test_process_all_requires_finalised_brief(tmp_path):
    shoot = tmp_path / "shoot"
    # Write an unfinalised brief
    brief_path = sid.sidecar_root(shoot)
    brief_path.mkdir(parents=True, exist_ok=True)
    (brief_path / "brief.json").write_text(json.dumps({"finalised": False}))

    events = list(sb.process_all(["clip.mp4"], shoot, "key"))
    assert len(events) == 1
    assert events[0]["event"] == "error"
    assert "brief" in events[0]["reason"].lower()


def test_process_all_requires_brief_exists(tmp_path):
    shoot = tmp_path / "shoot"
    # No brief.json at all

    events = list(sb.process_all(["clip.mp4"], shoot, "key"))
    assert len(events) == 1
    assert events[0]["event"] == "error"


# ── process_all: mocked Claude, full pipeline ────────────────────────────────


def _make_tool_response(tool_name: str, input_data: dict) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = input_data
    resp = MagicMock()
    resp.content = [block]
    return resp


def test_process_all_happy_path(tmp_path):
    shoot = tmp_path / "shoot"
    clip = str(tmp_path / "clip.mp4")
    Path(clip).write_bytes(b"\x00")

    # Set up sidecar with A-ROLL + transcript
    sid.merge_clip(
        shoot,
        clip,
        {
            "classification": "A-ROLL",
            "fps": 24.0,
            "transcript": {
                "utterances": [
                    {
                        "speaker": "A",
                        "start": 0,
                        "end": 20000,
                        "text": "I started ceramics at nineteen after a class at uni.",
                    }
                ],
                "audio_duration": 30.0,
            },
        },
        new_stage="transcribe",
    )

    # Write finalised brief
    brief_root = sid.sidecar_root(shoot)
    brief_root.mkdir(parents=True, exist_ok=True)
    (brief_root / "brief.json").write_text(
        json.dumps(
            {
                "finalised": True,
                "subject": {"name": "Lauren"},
                "narrative_angle": "origin story",
                "tone": "warm",
            }
        )
    )

    propose_input = {
        "soundbites": [
            {
                "beat_type": "origin-story",
                "in_sec": 0.0,
                "out_sec": 15.0,
                "label": "First touched clay",
                "why_usable": "Strong hook",
                "extract": "I started ceramics at nineteen after a class at uni.",
                "confidence": 0.9,
            }
        ]
    }
    judge_input = {
        "scores": [
            {
                "label": "First touched clay",
                "standalone_usability": 0.9,
                "beat_type_accuracy": 0.95,
                "judge_score": 0.925,
            }
        ]
    }

    mock_propose_resp = _make_tool_response("propose_soundbites", propose_input)
    mock_judge_resp = _make_tool_response("judge_soundbites", judge_input)
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [mock_propose_resp, mock_judge_resp]

    with (
        patch("anthropic.Anthropic", return_value=mock_client),
        patch.object(
            sb,
            "_load_prompt",
            return_value="prompt {{BRIEF_SUMMARY}} {{TRANSCRIPT}} {{CLIP_FILENAME}} {{CLIP_DURATION_SEC}} {{CLIP_FPS}} {{SPEAKER_LABELS}} {{PROPOSED_BITES_JSON}}",
        ),
    ):
        events = list(sb.process_all([clip], shoot, "key"))

    event_types = [e["event"] for e in events]
    assert "soundbites_started" in event_types
    assert "clip_processed" in event_types
    assert "soundbites_complete" in event_types

    clip_event = next(e for e in events if e["event"] == "clip_processed")
    assert clip_event["soundbite_count"] == 1
    assert clip_event["flagged_count"] == 0

    bites = clip_event["soundbites"]
    assert len(bites) == 1
    assert bites[0]["label"] == "First touched clay"
    assert bites[0]["in_frame"] == 0
    assert bites[0]["out_frame"] == 360  # 15s * 24fps
    assert bites[0]["judge_score"] == pytest.approx(0.925)

    # Sidecar updated with soundbites stage
    sc = sid.read_clip(shoot, clip)
    assert sc is not None
    assert "soundbites" in sc["stages_complete"]
    assert len(sc["soundbites"]) == 1


def test_process_all_flags_low_judge_score(tmp_path):
    shoot = tmp_path / "shoot"
    clip = str(tmp_path / "clip.mp4")
    Path(clip).write_bytes(b"\x00")

    sid.merge_clip(
        shoot,
        clip,
        {
            "classification": "A-ROLL",
            "fps": 25.0,
            "transcript": {"utterances": [], "audio_duration": 20.0},
        },
        new_stage="transcribe",
    )
    brief_root = sid.sidecar_root(shoot)
    brief_root.mkdir(parents=True, exist_ok=True)
    (brief_root / "brief.json").write_text(json.dumps({"finalised": True}))

    propose_input = {
        "soundbites": [
            {
                "beat_type": "factual",
                "in_sec": 0.0,
                "out_sec": 10.0,
                "label": "Weak bite",
                "why_usable": "Borderline",
                "extract": "Some text",
                "confidence": 0.55,
            }
        ]
    }
    judge_input = {
        "scores": [
            {
                "label": "Weak bite",
                "standalone_usability": 0.4,
                "beat_type_accuracy": 0.7,
                "judge_score": 0.55,
            }
        ]
    }

    mock_propose = _make_tool_response("propose_soundbites", propose_input)
    mock_judge = _make_tool_response("judge_soundbites", judge_input)
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [mock_propose, mock_judge]

    with (
        patch("anthropic.Anthropic", return_value=mock_client),
        patch.object(
            sb,
            "_load_prompt",
            return_value="prompt {{BRIEF_SUMMARY}} {{TRANSCRIPT}} {{CLIP_FILENAME}} {{CLIP_DURATION_SEC}} {{CLIP_FPS}} {{SPEAKER_LABELS}} {{PROPOSED_BITES_JSON}}",
        ),
    ):
        events = list(sb.process_all([clip], shoot, "key"))

    clip_event = next(e for e in events if e["event"] == "clip_processed")
    assert clip_event["flagged_count"] == 1
    assert clip_event["soundbites"][0]["flagged_for_review"] is True
    note = sb.render_marker_note(clip_event["soundbites"][0])
    assert note.startswith("[REVIEW]")


def test_process_all_skips_invalid_duration(tmp_path):
    shoot = tmp_path / "shoot"
    clip = str(tmp_path / "clip.mp4")
    Path(clip).write_bytes(b"\x00")

    sid.merge_clip(
        shoot,
        clip,
        {
            "classification": "A-ROLL",
            "fps": 24.0,
            "transcript": {"utterances": [], "audio_duration": 10.0},
        },
        new_stage="transcribe",
    )
    brief_root = sid.sidecar_root(shoot)
    brief_root.mkdir(parents=True, exist_ok=True)
    (brief_root / "brief.json").write_text(json.dumps({"finalised": True}))

    propose_input = {
        "soundbites": [
            # Too short — should be skipped
            {
                "beat_type": "hook",
                "in_sec": 0.0,
                "out_sec": 1.5,
                "label": "Too short",
                "why_usable": "x",
                "extract": "x",
                "confidence": 0.9,
            },
            # Valid
            {
                "beat_type": "hook",
                "in_sec": 2.0,
                "out_sec": 12.0,
                "label": "Good bite",
                "why_usable": "Works",
                "extract": "Some text",
                "confidence": 0.9,
            },
        ]
    }
    judge_input = {
        "scores": [
            {
                "label": "Good bite",
                "standalone_usability": 0.9,
                "beat_type_accuracy": 0.9,
                "judge_score": 0.9,
            }
        ]
    }

    mock_propose = _make_tool_response("propose_soundbites", propose_input)
    mock_judge = _make_tool_response("judge_soundbites", judge_input)
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [mock_propose, mock_judge]

    with (
        patch("anthropic.Anthropic", return_value=mock_client),
        patch.object(
            sb,
            "_load_prompt",
            return_value="prompt {{BRIEF_SUMMARY}} {{TRANSCRIPT}} {{CLIP_FILENAME}} {{CLIP_DURATION_SEC}} {{CLIP_FPS}} {{SPEAKER_LABELS}} {{PROPOSED_BITES_JSON}}",
        ),
    ):
        events = list(sb.process_all([clip], shoot, "key"))

    clip_event = next(e for e in events if e["event"] == "clip_processed")
    # Only the valid bite should survive
    assert clip_event["soundbite_count"] == 1
    assert clip_event["soundbites"][0]["label"] == "Good bite"

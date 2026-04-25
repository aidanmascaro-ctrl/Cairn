"""
pytest suite for src/workers/matching.py

Tests:
- _format_tags_line: correct format, handles missing fields
- format_library_for_prompt: joins all tags_lines
- estimate_matching: correct soundbite_count, broll_clip_count, cost
- _brief_tone / _brief_summary: renders known fields, handles empty brief
- _compute_shot_string: sorts by confidence, caps at SHOT_FIELD_CAP, L/T suffix, overflow label
- _compute_description_string: literal first, capped at DESCRIPTION_FIELD_CAP
- _matches_line_for_marker: MATCHES: prefix, sorted by confidence
- build_broll_library: finds vision-complete B-roll, skips A-roll, skips no-vision clips
- scan_aroll_for_matching: finds soundbites-complete A-roll, skips matching-complete
- count_soundbites: sums soundbites across clips
- process_all: brief guard, no-library guard, happy path, error continues
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src" / "workers"))

import matching as mt
import sidecar as sid

# ── _format_tags_line ─────────────────────────────────────────────────────────


def test_format_tags_line_full():
    tags = {
        "subjects": ["potter", "clay"],
        "actions": ["throwing", "centering"],
        "shot_type": "close-up",
        "mood": "focused",
        "environment": "studio",
        "colour_palette": ["terracotta", "grey"],
        "camera_motion": "static",
    }
    line = mt._format_tags_line("clip_042", tags)
    assert line.startswith("clip_042 |")
    assert "subjects: potter, clay" in line
    assert "actions: throwing, centering" in line
    assert "shot: close-up" in line
    assert "mood: focused" in line
    assert "env: studio" in line
    assert "palette: terracotta, grey" in line
    assert "motion: static" in line


def test_format_tags_line_empty_tags():
    line = mt._format_tags_line("clip_001", {})
    assert line == "clip_001 | no tags"


def test_format_tags_line_partial():
    tags = {"shot_type": "wide", "mood": "calm"}
    line = mt._format_tags_line("clip_002", tags)
    assert "shot: wide" in line
    assert "mood: calm" in line
    assert "subjects" not in line


# ── format_library_for_prompt ─────────────────────────────────────────────────


def test_format_library_for_prompt():
    library = [
        {"clip_id": "a", "clip_path": "/a.mp4", "tags_line": "a | foo", "tags": {}},
        {"clip_id": "b", "clip_path": "/b.mp4", "tags_line": "b | bar", "tags": {}},
    ]
    text = mt.format_library_for_prompt(library)
    assert "a | foo" in text
    assert "b | bar" in text
    assert text.index("a | foo") < text.index("b | bar")


def test_format_library_for_prompt_empty():
    assert mt.format_library_for_prompt([]) == ""


# ── estimate_matching ─────────────────────────────────────────────────────────


def test_estimate_matching_basic():
    aroll = [
        {"clip_path": "/a.mp4", "sidecar": {}, "soundbites": [{"id": "sb1"}, {"id": "sb2"}]},
        {"clip_path": "/b.mp4", "sidecar": {}, "soundbites": [{"id": "sb3"}]},
    ]
    broll = [{"clip_id": "br1"}, {"clip_id": "br2"}, {"clip_id": "br3"}]
    result = mt.estimate_matching(aroll, broll)
    assert result["soundbite_count"] == 3
    assert result["broll_clip_count"] == 3
    assert result["estimated_cost_usd"] == pytest.approx(3 * mt.ANTHROPIC_COST_PER_SOUNDBITE_USD)


def test_estimate_matching_empty():
    result = mt.estimate_matching([], [])
    assert result["soundbite_count"] == 0
    assert result["broll_clip_count"] == 0
    assert result["estimated_cost_usd"] == 0.0


# ── _brief_tone ───────────────────────────────────────────────────────────────


def test_brief_tone_both():
    brief = {"tone": "warm", "narrative_angle": "origin story"}
    tone = mt._brief_tone(brief)
    assert "warm" in tone
    assert "origin story" in tone


def test_brief_tone_empty():
    assert mt._brief_tone({}) == "unspecified"


def test_brief_tone_only_tone():
    assert mt._brief_tone({"tone": "cinematic"}) == "cinematic"


# ── _brief_summary ────────────────────────────────────────────────────────────


def test_brief_summary_full():
    brief = {
        "subject": {"name": "Lauren", "profession": "ceramicist"},
        "narrative_angle": "origin story",
        "tone": "warm",
    }
    summary = mt._brief_summary(brief)
    assert "Lauren" in summary
    assert "ceramicist" in summary
    assert "origin story" in summary
    assert "warm" in summary


def test_brief_summary_empty():
    assert mt._brief_summary({}) == "(Brief not yet filled)"


def test_brief_summary_subject_no_profession():
    summary = mt._brief_summary({"subject": {"name": "Alex"}})
    assert "Alex" in summary
    assert "(" not in summary


# ── _compute_shot_string ──────────────────────────────────────────────────────


def _match(soundbite_id: str, kind: str, confidence: float) -> dict:
    return {"soundbite_id": soundbite_id, "kind": kind, "confidence": confidence}


def test_compute_shot_string_ordering():
    matches = [
        _match("sb_001", "thematic", 0.7),
        _match("sb_002", "literal", 0.95),
    ]
    s = mt._compute_shot_string(matches)
    # Higher confidence first
    assert s.index("sb_002:L") < s.index("sb_001:T")


def test_compute_shot_string_literal_suffix():
    matches = [_match("sb_001", "literal", 0.9)]
    assert "sb_001:L" in mt._compute_shot_string(matches)


def test_compute_shot_string_thematic_suffix():
    matches = [_match("sb_001", "thematic", 0.9)]
    assert "sb_001:T" in mt._compute_shot_string(matches)


def test_compute_shot_string_cap():
    matches = [_match(f"sb_{i:03d}", "literal", 0.9 - i * 0.01) for i in range(12)]
    s = mt._compute_shot_string(matches)
    # Should only include SHOT_FIELD_CAP = 8 entries before overflow marker
    assert "+4 more" in s
    entry_count = s.split(" +")[0].count(":L")
    assert entry_count == mt.SHOT_FIELD_CAP


def test_compute_shot_string_no_overflow():
    matches = [_match(f"sb_{i:03d}", "literal", 0.9) for i in range(3)]
    s = mt._compute_shot_string(matches)
    assert "more" not in s


# ── _compute_description_string ───────────────────────────────────────────────


def _desc_match(soundbite_id: str, kind: str, confidence: float, rationale: str) -> dict:
    return {
        "soundbite_id": soundbite_id,
        "kind": kind,
        "confidence": confidence,
        "rationale": rationale,
    }


def test_compute_description_literal_first():
    matches = [
        _desc_match("sb_002", "thematic", 0.95, "thematic rationale"),
        _desc_match("sb_001", "literal", 0.7, "literal rationale"),
    ]
    desc = mt._compute_description_string(matches)
    assert desc.index("Literal") < desc.index("Thematic")


def test_compute_description_cap():
    matches = [_desc_match(f"sb_{i:03d}", "literal", 0.9, f"reason {i}") for i in range(10)]
    desc = mt._compute_description_string(matches)
    assert desc.count("Literal") == mt.DESCRIPTION_FIELD_CAP


def test_compute_description_contains_rationale():
    matches = [_desc_match("sb_001", "literal", 0.9, "hands centering clay")]
    desc = mt._compute_description_string(matches)
    assert "hands centering clay" in desc


# ── _matches_line_for_marker ──────────────────────────────────────────────────


def test_matches_line_for_marker_format():
    matches = [
        {"broll_clip_id": "clip_042", "kind": "literal", "confidence": 0.9},
        {"broll_clip_id": "clip_007", "kind": "thematic", "confidence": 0.75},
    ]
    line = mt._matches_line_for_marker(matches)
    assert line.startswith("MATCHES: ")
    assert "clip_042 (literal)" in line
    assert "clip_007 (thematic)" in line
    # Sorted by confidence (descending) → clip_042 first
    assert line.index("clip_042") < line.index("clip_007")


def test_matches_line_for_marker_single():
    matches = [{"broll_clip_id": "clip_001", "kind": "thematic", "confidence": 0.8}]
    line = mt._matches_line_for_marker(matches)
    assert line == "MATCHES: clip_001 (thematic)"


# ── build_broll_library ───────────────────────────────────────────────────────


def _write_broll_sidecar(shoot, clip_path, with_vision: bool = True):
    tags = (
        {
            "subjects": ["potter"],
            "actions": ["throwing"],
            "shot_type": "close-up",
            "mood": "focused",
            "environment": "studio",
            "colour_palette": ["terracotta"],
            "camera_motion": "static",
        }
        if with_vision
        else {}
    )
    sc_data = {"classification": "B-ROLL"}
    sid.merge_clip(shoot, clip_path, sc_data, new_stage="classify")
    if with_vision:
        sid.merge_clip(shoot, clip_path, {"vision": {"tags": tags}}, new_stage="vision")


def test_build_broll_library_finds_vision_clips(tmp_path):
    shoot = tmp_path / "shoot"
    clip = str(tmp_path / "b.mp4")
    Path(clip).write_bytes(b"\x00")
    _write_broll_sidecar(shoot, clip, with_vision=True)

    library = mt.build_broll_library(shoot)
    assert any(entry["clip_path"] == clip for entry in library)


def test_build_broll_library_skips_aroll(tmp_path):
    shoot = tmp_path / "shoot"
    clip = str(tmp_path / "a.mp4")
    Path(clip).write_bytes(b"\x00")
    sid.merge_clip(shoot, clip, {"classification": "A-ROLL"}, new_stage="classify")

    library = mt.build_broll_library(shoot)
    assert not any(entry["clip_path"] == clip for entry in library)


def test_build_broll_library_skips_no_vision(tmp_path):
    shoot = tmp_path / "shoot"
    clip = str(tmp_path / "b.mp4")
    Path(clip).write_bytes(b"\x00")
    sid.merge_clip(shoot, clip, {"classification": "B-ROLL"}, new_stage="classify")
    # No vision stage

    library = mt.build_broll_library(shoot)
    assert not any(entry["clip_path"] == clip for entry in library)


def test_build_broll_library_empty_shoot(tmp_path):
    assert mt.build_broll_library(tmp_path / "shoot") == []


# ── scan_aroll_for_matching ───────────────────────────────────────────────────


def test_scan_aroll_finds_soundbited_clips(tmp_path):
    shoot = tmp_path / "shoot"
    clip = str(tmp_path / "a.mp4")
    Path(clip).write_bytes(b"\x00")

    sid.merge_clip(
        shoot,
        clip,
        {
            "classification": "A-ROLL",
            "soundbites": [{"id": "sb_001"}],
        },
        new_stage="soundbites",
    )

    clips = mt.scan_aroll_for_matching(shoot)
    assert any(c["clip_path"] == clip for c in clips)


def test_scan_aroll_skips_already_matched(tmp_path):
    shoot = tmp_path / "shoot"
    clip = str(tmp_path / "a.mp4")
    Path(clip).write_bytes(b"\x00")

    sid.merge_clip(
        shoot,
        clip,
        {
            "classification": "A-ROLL",
            "soundbites": [{"id": "sb_001"}],
        },
        new_stage="soundbites",
    )
    sid.merge_clip(shoot, clip, {}, new_stage="matching")

    clips = mt.scan_aroll_for_matching(shoot)
    assert not any(c["clip_path"] == clip for c in clips)


def test_scan_aroll_skips_no_soundbites(tmp_path):
    shoot = tmp_path / "shoot"
    clip = str(tmp_path / "a.mp4")
    Path(clip).write_bytes(b"\x00")

    sid.merge_clip(
        shoot,
        clip,
        {"classification": "A-ROLL"},
        new_stage="transcribe",
    )

    clips = mt.scan_aroll_for_matching(shoot)
    assert not any(c["clip_path"] == clip for c in clips)


def test_scan_aroll_skips_broll(tmp_path):
    shoot = tmp_path / "shoot"
    clip = str(tmp_path / "b.mp4")
    Path(clip).write_bytes(b"\x00")

    sid.merge_clip(
        shoot,
        clip,
        {"classification": "B-ROLL", "soundbites": [{"id": "sb_001"}]},
        new_stage="soundbites",
    )

    clips = mt.scan_aroll_for_matching(shoot)
    assert not any(c["clip_path"] == clip for c in clips)


# ── count_soundbites ──────────────────────────────────────────────────────────


def test_count_soundbites():
    clips = [
        {"soundbites": [{"id": "a"}, {"id": "b"}]},
        {"soundbites": [{"id": "c"}]},
    ]
    assert mt.count_soundbites(clips) == 3


def test_count_soundbites_empty():
    assert mt.count_soundbites([]) == 0


# ── process_all: guards ───────────────────────────────────────────────────────


def test_process_all_requires_finalised_brief(tmp_path):
    shoot = tmp_path / "shoot"
    brief_root = sid.sidecar_root(shoot)
    brief_root.mkdir(parents=True, exist_ok=True)
    (brief_root / "brief.json").write_text(json.dumps({"finalised": False}))

    events = list(mt.process_all(shoot, "key"))
    assert len(events) == 1
    assert events[0]["event"] == "error"
    assert "brief" in events[0]["reason"].lower()


def test_process_all_requires_brief_exists(tmp_path):
    shoot = tmp_path / "shoot"
    # No brief at all

    events = list(mt.process_all(shoot, "key"))
    assert len(events) == 1
    assert events[0]["event"] == "error"


def test_process_all_requires_broll_library(tmp_path):
    shoot = tmp_path / "shoot"
    brief_root = sid.sidecar_root(shoot)
    brief_root.mkdir(parents=True, exist_ok=True)
    (brief_root / "brief.json").write_text(json.dumps({"finalised": True}))
    # No B-roll clips

    events = list(mt.process_all(shoot, "key"))
    assert len(events) == 1
    assert events[0]["event"] == "error"
    assert "vision" in events[0]["reason"].lower()


# ── process_all: happy path ───────────────────────────────────────────────────


def _make_tool_response(tool_name: str, input_data: dict) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = input_data
    resp = MagicMock()
    resp.content = [block]
    return resp


def _setup_shoot(tmp_path):
    """Create a minimal shoot with one B-roll (vision-tagged) and one A-roll (soundbited)."""
    shoot = tmp_path / "shoot"

    broll_clip = str(tmp_path / "broll.mp4")
    Path(broll_clip).write_bytes(b"\x00")
    sid.merge_clip(
        shoot,
        broll_clip,
        {
            "classification": "B-ROLL",
            "vision": {
                "tags": {
                    "subjects": ["potter"],
                    "actions": ["throwing"],
                    "shot_type": "close-up",
                    "mood": "focused",
                    "environment": "studio",
                    "colour_palette": ["terracotta"],
                    "camera_motion": "static",
                }
            },
        },
        new_stage="vision",
    )

    aroll_clip = str(tmp_path / "aroll.mp4")
    Path(aroll_clip).write_bytes(b"\x00")
    sid.merge_clip(
        shoot,
        aroll_clip,
        {
            "classification": "A-ROLL",
            "soundbites": [
                {
                    "id": "sb_aroll_001",
                    "beat_type": "origin-story",
                    "in_sec": 0.0,
                    "out_sec": 15.0,
                    "in_tc": "00:00:00:00",
                    "out_tc": "00:00:15:00",
                    "label": "Started ceramics",
                    "why_usable": "Strong hook",
                    "extract": "I started at nineteen.",
                    "confidence": 0.9,
                }
            ],
        },
        new_stage="soundbites",
    )

    brief_root = sid.sidecar_root(shoot)
    (brief_root / "brief.json").write_text(
        json.dumps(
            {
                "finalised": True,
                "subject": {"name": "Lauren", "profession": "ceramicist"},
                "narrative_angle": "origin story",
                "tone": "warm",
            }
        )
    )

    return shoot, broll_clip, aroll_clip


def _broll_clip_id(shoot, broll_clip) -> str:
    sc = sid.read_clip(shoot, broll_clip)
    assert sc is not None
    return sc.get("clip_id", sid.clip_id(broll_clip))


def test_process_all_happy_path(tmp_path):
    shoot, broll_clip, aroll_clip = _setup_shoot(tmp_path)
    broll_cid = _broll_clip_id(shoot, broll_clip)

    propose_input = {
        "matches": [
            {
                "clip_id": broll_cid,
                "kind": "literal",
                "rationale": "Hands centering clay mirrors the origin story beat",
                "confidence": 0.88,
            }
        ]
    }
    judge_input = {
        "scores": [
            {
                "clip_id": broll_cid,
                "kind": "literal",
                "kind_accuracy": 0.9,
                "tone_fit": 0.92,
                "judge_score": 0.91,
            }
        ]
    }

    mock_propose = _make_tool_response("propose_matches", propose_input)
    mock_judge = _make_tool_response("judge_matches", judge_input)
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [mock_propose, mock_judge]

    with (
        patch("anthropic.Anthropic", return_value=mock_client),
        patch.object(
            mt,
            "_load_prompt",
            return_value="prompt {{BRIEF_SUMMARY}} {{SOUNDBITE_ID}} {{SPEAKER_NAME}} {{BEAT_TYPE}} {{IN_TC}} {{OUT_TC}} {{DURATION_SEC}} {{EXTRACT}} {{WHY_USABLE}} {{BROLL_LIBRARY}} {{PROPOSED_MATCHES_JSON}} {{BROLL_DETAILS}} {{BRIEF_TONE}}",
        ),
    ):
        events = list(mt.process_all(shoot, "key"))

    event_types = [e["event"] for e in events]
    assert "matching_started" in event_types
    assert "soundbite_matched" in event_types
    assert "matching_complete" in event_types

    matched_ev = next(e for e in events if e["event"] == "soundbite_matched")
    assert matched_ev["accepted"] == 1
    assert matched_ev["rejected"] == 0

    complete_ev = next(e for e in events if e["event"] == "matching_complete")
    assert complete_ev["total_matches"] == 1
    assert complete_ev["rejected"] == 0
    assert complete_ev["broll_clips_matched"] == 1
    assert complete_ev["uncovered_soundbites"] == []

    # A-roll sidecar updated with matching stage
    aroll_sc = sid.read_clip(shoot, aroll_clip)
    assert aroll_sc is not None
    assert "matching" in aroll_sc["stages_complete"]
    assert aroll_sc["soundbites"][0]["matches"][0]["clip_id"] == broll_cid

    # B-roll sidecar updated with matching stage
    broll_sc = sid.read_clip(shoot, broll_clip)
    assert broll_sc is not None
    assert "matching" in broll_sc["stages_complete"]
    assert len(broll_sc["matches"]) == 1


def test_process_all_judge_drops_low_score(tmp_path):
    shoot, broll_clip, _aroll_clip = _setup_shoot(tmp_path)
    broll_cid = _broll_clip_id(shoot, broll_clip)

    propose_input = {
        "matches": [
            {
                "clip_id": broll_cid,
                "kind": "thematic",
                "rationale": "Loosely related",
                "confidence": 0.55,
            }
        ]
    }
    judge_input = {
        "scores": [
            {
                "clip_id": broll_cid,
                "kind": "thematic",
                "kind_accuracy": 0.4,
                "tone_fit": 0.5,
                "judge_score": 0.45,  # below JUDGE_DROP_THRESHOLD
            }
        ]
    }

    mock_propose = _make_tool_response("propose_matches", propose_input)
    mock_judge = _make_tool_response("judge_matches", judge_input)
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [mock_propose, mock_judge]

    with (
        patch("anthropic.Anthropic", return_value=mock_client),
        patch.object(
            mt,
            "_load_prompt",
            return_value="prompt {{BRIEF_SUMMARY}} {{SOUNDBITE_ID}} {{SPEAKER_NAME}} {{BEAT_TYPE}} {{IN_TC}} {{OUT_TC}} {{DURATION_SEC}} {{EXTRACT}} {{WHY_USABLE}} {{BROLL_LIBRARY}} {{PROPOSED_MATCHES_JSON}} {{BROLL_DETAILS}} {{BRIEF_TONE}}",
        ),
    ):
        events = list(mt.process_all(shoot, "key"))

    matched_ev = next(e for e in events if e["event"] == "soundbite_matched")
    assert matched_ev["accepted"] == 0
    assert matched_ev["rejected"] == 1

    complete_ev = next(e for e in events if e["event"] == "matching_complete")
    assert complete_ev["total_matches"] == 0
    assert complete_ev["rejected"] == 1
    # soundbite should be uncovered
    assert "sb_aroll_001" in complete_ev["uncovered_soundbites"]


def test_process_all_bite_error_continues(tmp_path):
    shoot, _broll_clip, _aroll_clip = _setup_shoot(tmp_path)

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("API unavailable")

    with (
        patch("anthropic.Anthropic", return_value=mock_client),
        patch.object(
            mt,
            "_load_prompt",
            return_value="prompt {{BRIEF_SUMMARY}} {{SOUNDBITE_ID}} {{SPEAKER_NAME}} {{BEAT_TYPE}} {{IN_TC}} {{OUT_TC}} {{DURATION_SEC}} {{EXTRACT}} {{WHY_USABLE}} {{BROLL_LIBRARY}}",
        ),
    ):
        events = list(mt.process_all(shoot, "key"))

    event_types = [e["event"] for e in events]
    # soundbite_error emitted, pipeline completes
    assert "soundbite_error" in event_types
    assert "matching_complete" in event_types

    error_ev = next(e for e in events if e["event"] == "soundbite_error")
    assert "API unavailable" in error_ev["reason"]

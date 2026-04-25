"""
pytest suite for src/workers/brief.py

Tests:
- brief_path: returns correct location under .cairn/
- read_brief / write_brief: round-trip JSON, None on missing file
- delete_brief: removes file; no-op if absent
- count_fields: correct filled/required counts for partial and complete briefs
- get_project_summary: returns string with shoot name, handles empty .cairn dir
- _deep_merge: overwrites scalars, merges nested dicts, skips None values
- _apply_tool_input: updates brief from tool call input
- send_message: calls Claude, processes update_brief_draft, appends to conversation_log
- send_message finalise: calls finalise_brief, sets finalised=True, triggers judge
- save_brief: merges updates and saves
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src" / "workers"))

import brief as b
import sidecar as sid

# ── brief_path ────────────────────────────────────────────────────────────────


def test_brief_path(tmp_path):
    p = b.brief_path(tmp_path / "shoot")
    assert p.name == "brief.json"
    assert ".cairn" in str(p)


# ── read_brief / write_brief ──────────────────────────────────────────────────


def test_read_brief_returns_none_if_missing(tmp_path):
    assert b.read_brief(tmp_path / "shoot") is None


def test_write_read_round_trip(tmp_path):
    shoot = tmp_path / "shoot"
    data = {"schema_version": 1, "tone": "warm", "finalised": False, "conversation_log": []}
    b.write_brief(shoot, data)
    result = b.read_brief(shoot)
    assert result is not None
    assert result["tone"] == "warm"


def test_read_brief_returns_none_on_corrupt_json(tmp_path):
    shoot = tmp_path / "shoot"
    p = b.brief_path(shoot)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{{not json}}")
    assert b.read_brief(shoot) is None


# ── delete_brief ──────────────────────────────────────────────────────────────


def test_delete_brief_removes_file(tmp_path):
    shoot = tmp_path / "shoot"
    b.write_brief(shoot, {"schema_version": 1})
    assert b.brief_path(shoot).exists()
    b.delete_brief(shoot)
    assert not b.brief_path(shoot).exists()


def test_delete_brief_no_op_if_missing(tmp_path):
    b.delete_brief(tmp_path / "shoot")  # should not raise


# ── count_fields ──────────────────────────────────────────────────────────────


def test_count_fields_empty():
    result = b.count_fields({})
    assert result["filled"] == 0
    assert result["required_filled"] == 0


def test_count_fields_partial():
    data = {"subject": {"name": "Lauren"}, "tone": "warm"}
    result = b.count_fields(data)
    assert result["filled"] == 2
    assert result["required_filled"] == 2  # subject + tone are required


def test_count_fields_complete():
    data = {
        "subject": {"name": "Lauren"},
        "deliverables": [{"platform": "instagram_reel"}],
        "narrative_angle": "origin story",
        "tone": "warm",
        "themes": ["craft"],
        "word_boost_terms": ["ceramic"],
        "privacy": {"cloud_transcription_allowed": True, "cloud_vision_allowed": True},
        "notes": "some notes",
    }
    result = b.count_fields(data)
    assert result["filled"] == 8
    assert result["required_filled"] == 5


def test_count_fields_skips_empty_lists():
    data = {"themes": [], "deliverables": []}
    result = b.count_fields(data)
    assert result["filled"] == 0


# ── get_project_summary ───────────────────────────────────────────────────────


def test_get_project_summary_no_sidecar_dir(tmp_path):
    summary = b.get_project_summary(tmp_path / "shoot")
    assert "shoot" in summary.lower() or "No clips" in summary


def test_get_project_summary_counts_clips(tmp_path):
    shoot = tmp_path / "shoot"
    acam = shoot / "01_VIDEOS" / "A_CAM"
    acam.mkdir(parents=True)

    clip_a = acam / "a.mp4"
    clip_a.write_bytes(b"\x00")
    clip_b = acam / "b.mp4"
    clip_b.write_bytes(b"\x00")

    sid.merge_clip(shoot, str(clip_a), {"classification": "A-ROLL"}, new_stage="classify")
    sid.merge_clip(shoot, str(clip_b), {"classification": "B-ROLL"}, new_stage="classify")

    summary = b.get_project_summary(shoot)
    assert "1 A-roll" in summary
    assert "1 B-roll" in summary


# ── _deep_merge ───────────────────────────────────────────────────────────────


def test_deep_merge_overwrites_scalar():
    result = b._deep_merge({"tone": "old"}, {"tone": "new"})
    assert result["tone"] == "new"


def test_deep_merge_skips_none():
    result = b._deep_merge({"tone": "keep"}, {"tone": None})
    assert result["tone"] == "keep"


def test_deep_merge_nested_dict():
    base = {"subject": {"name": "X", "profession": "old"}}
    update = {"subject": {"profession": "ceramicist"}}
    result = b._deep_merge(base, update)
    assert result["subject"]["name"] == "X"
    assert result["subject"]["profession"] == "ceramicist"


def test_deep_merge_skips_empty_list():
    result = b._deep_merge({"themes": ["craft"]}, {"themes": []})
    assert result["themes"] == ["craft"]


def test_deep_merge_replaces_non_empty_list():
    result = b._deep_merge({"themes": ["old"]}, {"themes": ["craft", "origin"]})
    assert result["themes"] == ["craft", "origin"]


# ── send_message ──────────────────────────────────────────────────────────────


def _make_mock_response(text: str, tool_calls: list | None = None):
    """Build a mock Anthropic response object."""
    content = []
    if tool_calls:
        for name, input_data in tool_calls:
            tc = MagicMock()
            tc.type = "tool_use"
            tc.id = f"tc_{name}"
            tc.name = name
            tc.input = input_data
            content.append(tc)
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    content.append(text_block)

    response = MagicMock()
    response.content = content
    return response


def test_send_message_appends_to_log(tmp_path):
    shoot = tmp_path / "shoot"

    draft_update = {"tone": "warm"}
    mock_resp1 = _make_mock_response("", [("update_brief_draft", draft_update)])
    mock_resp2 = _make_mock_response("Great, warm it is!")

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [mock_resp1, mock_resp2]

    with (
        patch("anthropic.Anthropic", return_value=mock_client),
        patch.object(b, "_load_prompt", return_value="system prompt {{PROJECT_CONTEXT}}"),
    ):
        result = b.send_message(shoot, "warm tone please", "key")

    assert result["reply"] == "Great, warm it is!"
    assert result["draft"]["tone"] == "warm"
    assert not result["finalised"]

    log = result["draft"]["conversation_log"]
    assert log[-2]["role"] == "user"
    assert log[-1]["role"] == "assistant"

    # Persisted to disk
    on_disk = b.read_brief(shoot)
    assert on_disk is not None
    assert on_disk["tone"] == "warm"


def test_send_message_finalise_sets_finalised(tmp_path):
    shoot = tmp_path / "shoot"

    finalise_input = {
        "subject": {"name": "Lauren"},
        "deliverables": [{"platform": "instagram_reel"}],
        "narrative_angle": "origin story",
        "tone": "warm",
        "privacy": {"cloud_transcription_allowed": True, "cloud_vision_allowed": True},
    }
    mock_resp1 = _make_mock_response("", [("finalise_brief", finalise_input)])
    mock_resp2 = _make_mock_response("Brief finalised!")

    # Judge response
    judge_tc = MagicMock()
    judge_tc.type = "tool_use"
    judge_tc.name = "judge_brief"
    judge_tc.input = {"overall_score": 0.95, "invented_fields": [], "missing_fields": []}
    mock_judge_resp = MagicMock()
    mock_judge_resp.content = [judge_tc]

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [mock_resp1, mock_resp2, mock_judge_resp]

    with (
        patch("anthropic.Anthropic", return_value=mock_client),
        patch.object(b, "_load_prompt", return_value="prompt {{PROJECT_CONTEXT}}"),
    ):
        result = b.send_message(shoot, "yes that's right", "key")

    assert result["finalised"] is True
    assert result["draft"]["finalised"] is True
    assert result["judge"] is not None
    assert result["judge"]["overall_score"] == pytest.approx(0.95)


def test_send_message_no_tool_call_returns_text(tmp_path):
    """If Claude gives only a text response, return it without tool processing."""
    shoot = tmp_path / "shoot"

    mock_resp = _make_mock_response("Tell me more about the subject.")
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_resp

    with (
        patch("anthropic.Anthropic", return_value=mock_client),
        patch.object(b, "_load_prompt", return_value="system {{PROJECT_CONTEXT}}"),
    ):
        result = b.send_message(shoot, "hello", "key")

    assert result["reply"] == "Tell me more about the subject."
    assert not result["finalised"]


# ── save_brief ────────────────────────────────────────────────────────────────


def test_save_brief_merges_and_persists(tmp_path):
    shoot = tmp_path / "shoot"
    b.write_brief(shoot, {"schema_version": 1, "tone": "old", "conversation_log": []})

    result = b.save_brief(shoot, {"tone": "warm", "narrative_angle": "origin"})
    assert result["tone"] == "warm"
    assert result["narrative_angle"] == "origin"

    on_disk = b.read_brief(shoot)
    assert on_disk is not None
    assert on_disk["tone"] == "warm"

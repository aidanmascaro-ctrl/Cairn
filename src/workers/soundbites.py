"""
Stage 6 — Soundbite identification and Resolve marker placement.

For each A-roll clip with a completed transcript, reads the transcript and
project brief, asks Claude to propose soundbites, runs a judge pass, writes
the soundbites array to the sidecar, then places colour-coded markers on the
source clip in Resolve.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import sidecar as sid

# ── Constants ──────────────────────────────────────────────────────────────────

ANTHROPIC_MODEL = "claude-sonnet-4-6"

BEAT_TYPE_COLORS: dict[str, str] = {
    "origin-story": "Green",
    "emotional-peak": "Red",
    "factual": "Blue",
    "hook": "Yellow",
    "quote": "Cyan",
    "process-detail": "Purple",
    "humour": "Pink",
    "reflection": "Lavender",
    "technical": "Sky",
    "tension": "Fuchsia",
    "resolution": "Mint",
}

JUDGE_FLAG_THRESHOLD = 0.6
ANTHROPIC_COST_PER_CLIP_USD = 0.02  # rough estimate for selection + judge calls

# ── Tool definitions ───────────────────────────────────────────────────────────

_PROPOSE_SOUNDBITES_TOOL: dict[str, Any] = {
    "name": "propose_soundbites",
    "description": "Propose soundbite selections from the transcript.",
    "input_schema": {
        "type": "object",
        "properties": {
            "soundbites": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "beat_type": {"type": "string"},
                        "in_sec": {"type": "number"},
                        "out_sec": {"type": "number"},
                        "label": {"type": "string"},
                        "why_usable": {"type": "string"},
                        "extract": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    },
                    "required": [
                        "beat_type",
                        "in_sec",
                        "out_sec",
                        "label",
                        "why_usable",
                        "extract",
                        "confidence",
                    ],
                },
            }
        },
        "required": ["soundbites"],
    },
}

_JUDGE_SOUNDBITES_TOOL: dict[str, Any] = {
    "name": "judge_soundbites",
    "description": "Score each proposed soundbite for usability and beat-type accuracy.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "standalone_usability": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "beat_type_accuracy": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "judge_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "notes": {"type": "string"},
                    },
                    "required": [
                        "label",
                        "standalone_usability",
                        "beat_type_accuracy",
                        "judge_score",
                    ],
                },
            }
        },
        "required": ["scores"],
    },
}

# ── Prompt loading ─────────────────────────────────────────────────────────────


def _load_prompt(filename: str) -> str:
    p = Path(__file__).resolve().parent.parent / "orchestrator" / "prompts" / filename
    if p.exists():
        return p.read_text()
    return ""


# ── Frame / timecode helpers ───────────────────────────────────────────────────


def sec_to_frame(sec: float, fps: float) -> int:
    """Convert seconds to nearest whole frame at given fps."""
    return round(sec * fps)


def sec_to_tc(sec: float, fps: float) -> str:
    """Convert seconds to HH:MM:SS:FF display timecode string."""
    total_frames = round(sec * fps)
    ff = total_frames % round(fps)
    ss = int(sec) % 60
    mm = int(sec) // 60 % 60
    hh = int(sec) // 3600
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def beat_type_to_color(beat_type: str) -> str:
    """Return the Resolve marker color for a beat type. Falls back to Lemon."""
    return BEAT_TYPE_COLORS.get(beat_type, "Lemon")


def validate_duration(in_sec: float, out_sec: float) -> None:
    """Raise ValueError if bite duration is outside the hard limits [3, 60] s."""
    duration = out_sec - in_sec
    if duration < 3:
        raise ValueError(f"Soundbite duration {duration:.1f}s is below minimum 3s")
    if duration > 60:
        raise ValueError(f"Soundbite duration {duration:.1f}s exceeds maximum 60s")


def render_marker_note(bite: dict[str, Any]) -> str:
    """Format a multi-line marker note for Resolve."""
    lines = [
        bite["label"],
        "",
        f"WHY: {bite.get('why_usable', '')}",
        "",
        f"EXTRACT: {bite.get('extract', '')}",
        "",
        f"ID: {bite.get('id', '')}",
        f"TYPE: {bite.get('beat_type', '')}",
        f"CONFIDENCE: {bite.get('confidence', 0):.2f}",
    ]
    if bite.get("flagged_for_review"):
        lines.insert(0, "[REVIEW]")
    return "\n".join(lines)


# ── Clip scanning ──────────────────────────────────────────────────────────────


def scan_aroll_clips(shoot_folder: str | Path) -> list[str]:
    """
    Return absolute paths of A-roll clips that have a transcript but no
    soundbites stage complete yet.
    """
    root = sid.sidecar_root(shoot_folder)
    if not root.exists():
        return []
    clips: list[str] = []
    for f in root.glob("*.json"):
        if f.name == "brief.json":
            continue
        try:
            sc = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if sc.get("classification") != "A-ROLL":
            continue
        stages = sc.get("stages_complete", [])
        if "transcribe" not in stages:
            continue
        if "soundbites" in stages:
            continue
        path = sc.get("source_path")
        if path:
            clips.append(path)
    return clips


def estimate_soundbites(clip_paths: list[str]) -> dict[str, Any]:
    return {
        "clip_count": len(clip_paths),
        "estimated_cost_usd": round(len(clip_paths) * ANTHROPIC_COST_PER_CLIP_USD, 4),
    }


# ── Brief summary helper ───────────────────────────────────────────────────────


def _brief_summary(brief: dict[str, Any]) -> str:
    """Render a compact brief summary for injection into prompts."""
    parts: list[str] = []
    subject = brief.get("subject", {})
    if subject:
        name = subject.get("name", "")
        prof = subject.get("profession", "")
        if name:
            parts.append(f"Subject: {name}" + (f" ({prof})" if prof else ""))
    if brief.get("narrative_angle"):
        parts.append(f"Narrative angle: {brief['narrative_angle']}")
    if brief.get("tone"):
        parts.append(f"Tone: {brief['tone']}")
    if brief.get("themes"):
        parts.append(f"Themes: {', '.join(brief['themes'])}")
    deliverables = brief.get("deliverables", [])
    if deliverables:
        durations = []
        for d in deliverables:
            dur = d.get("target_duration_sec")
            plat = d.get("platform", "")
            if dur:
                durations.append(f"{plat} ({dur[0]}-{dur[-1]}s)")
            else:
                durations.append(plat)
        parts.append(f"Deliverables: {', '.join(durations)}")
    return "\n".join(parts) if parts else "(Brief not yet filled)"


# ── Transcript formatting ──────────────────────────────────────────────────────


def _format_transcript(sidecar_data: dict[str, Any]) -> str:
    """Render transcript utterances with speaker labels and timestamps."""
    transcript = sidecar_data.get("transcript", {})
    utterances = transcript.get("utterances", [])
    if not utterances:
        words = transcript.get("words", [])
        if not words:
            return transcript.get("text", "(No transcript available)")
        # Fallback: plain text
        return " ".join(w.get("text", "") for w in words)

    lines: list[str] = []
    speaker_labels: dict[str, str] = sidecar_data.get("speaker_labels", {})
    for u in utterances:
        speaker_id = u.get("speaker", "A")
        speaker_name = speaker_labels.get(speaker_id, f"Speaker {speaker_id}")
        start = u.get("start", 0) / 1000  # ms → sec
        end = u.get("end", 0) / 1000
        text = u.get("text", "")
        lines.append(f"[{start:.2f}-{end:.2f}] {speaker_name}: {text}")
    return "\n".join(lines)


# ── Claude selection call ──────────────────────────────────────────────────────


def _propose_soundbites_claude(
    sidecar_data: dict[str, Any],
    brief: dict[str, Any],
    api_key: str,
) -> list[dict[str, Any]]:
    """Call Claude with the propose_soundbites tool. Returns raw tool input list."""
    import anthropic

    template = _load_prompt("soundbite_select.md")
    brief_text = _brief_summary(brief)
    transcript_text = _format_transcript(sidecar_data)

    clip_filename = Path(sidecar_data.get("source_path", "unknown")).name
    clip_duration = sidecar_data.get("transcript", {}).get("audio_duration", 0)
    clip_fps = sidecar_data.get("fps", 24.0)
    speaker_labels_str = (
        ", ".join(f"{k}={v}" for k, v in sidecar_data.get("speaker_labels", {}).items()) or "None"
    )

    prompt = (
        template.replace("{{BRIEF_SUMMARY}}", brief_text)
        .replace("{{TRANSCRIPT}}", transcript_text)
        .replace("{{CLIP_FILENAME}}", clip_filename)
        .replace("{{CLIP_DURATION_SEC}}", str(round(clip_duration, 1)))
        .replace("{{CLIP_FPS}}", str(clip_fps))
        .replace("{{SPEAKER_LABELS}}", speaker_labels_str)
    )

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(  # type: ignore[no-matching-overload]
        model=ANTHROPIC_MODEL,
        max_tokens=2048,
        tools=[_PROPOSE_SOUNDBITES_TOOL],
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": prompt}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "propose_soundbites":
            return list(block.input.get("soundbites", []))  # type: ignore[attr-defined]
    return []


# ── Judge pass ─────────────────────────────────────────────────────────────────


def _judge_soundbites_claude(
    sidecar_data: dict[str, Any],
    brief: dict[str, Any],
    proposed: list[dict[str, Any]],
    api_key: str,
) -> dict[str, float]:
    """
    Run the LLM-as-judge. Returns {label: judge_score} mapping.
    Non-fatal: returns empty dict on failure so main pipeline can proceed.
    """
    import anthropic

    if not proposed:
        return {}

    template = _load_prompt("soundbite_judge.md")
    brief_text = _brief_summary(brief)
    transcript_text = _format_transcript(sidecar_data)

    clip_filename = Path(sidecar_data.get("source_path", "unknown")).name
    clip_duration = sidecar_data.get("transcript", {}).get("audio_duration", 0)
    clip_fps = sidecar_data.get("fps", 24.0)

    prompt = (
        template.replace("{{BRIEF_SUMMARY}}", brief_text)
        .replace("{{TRANSCRIPT}}", transcript_text)
        .replace("{{CLIP_FILENAME}}", clip_filename)
        .replace("{{CLIP_DURATION_SEC}}", str(round(clip_duration, 1)))
        .replace("{{CLIP_FPS}}", str(clip_fps))
        .replace("{{PROPOSED_BITES_JSON}}", json.dumps(proposed, indent=2))
    )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(  # type: ignore[no-matching-overload]
            model=ANTHROPIC_MODEL,
            max_tokens=1024,
            tools=[_JUDGE_SOUNDBITES_TOOL],
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == "judge_soundbites":
                scores = block.input.get("scores", [])  # type: ignore[attr-defined]
                return {s["label"]: float(s["judge_score"]) for s in scores if "label" in s}
    except Exception as exc:
        print(f"[soundbites.py] judge call failed: {exc}", file=sys.stderr)
    return {}


# ── Soundbite ID generation ────────────────────────────────────────────────────


def _make_soundbite_id(clip_id: str, index: int) -> str:
    return f"sb_{clip_id}_{index + 1:03d}"


# ── Overlap detection ──────────────────────────────────────────────────────────


def _has_non_nested_overlap(bites: list[dict[str, Any]]) -> bool:
    """
    Return True if any two bites overlap in a non-nested way.
    Nested = one bite's range is entirely within another's (allowed).
    """
    sorted_bites = sorted(bites, key=lambda b: b["in_sec"])
    for i, a in enumerate(sorted_bites):
        for b in sorted_bites[i + 1 :]:
            if b["in_sec"] >= a["out_sec"]:
                break  # no overlap
            # b starts inside a — check if b is nested inside a
            if not (b["in_sec"] >= a["in_sec"] and b["out_sec"] <= a["out_sec"]):
                return True
    return False


# ── Main processing pipeline ───────────────────────────────────────────────────


def process_clip(
    clip_path: str,
    shoot_folder: str | Path,
    brief: dict[str, Any],
    api_key: str,
) -> dict[str, Any]:
    """
    Run full soundbite pipeline for one clip. Returns event dict with
    event="clip_processed", soundbites list, and flagged bites.
    Raises on unrecoverable errors (schema validation, etc.).
    """
    sc = sid.read_clip(shoot_folder, clip_path)
    if sc is None:
        raise ValueError(f"No sidecar found for {clip_path}")
    if "transcribe" not in sc.get("stages_complete", []):
        raise ValueError(f"Clip {clip_path} has no transcript — run transcription first")

    cid = sid.clip_id(clip_path)
    fps = float(sc.get("fps", 24.0))

    # Propose soundbites
    raw_bites = _propose_soundbites_claude(sc, brief, api_key)

    # Validate and assign IDs
    validated: list[dict[str, Any]] = []
    for i, bite in enumerate(raw_bites):
        in_sec = float(bite.get("in_sec", 0))
        out_sec = float(bite.get("out_sec", 0))
        try:
            validate_duration(in_sec, out_sec)
        except ValueError as exc:
            print(f"[soundbites.py] Skipping bite {i}: {exc}", file=sys.stderr)
            continue
        validated.append(
            {
                **bite,
                "id": _make_soundbite_id(cid, i),
                "in_frame": sec_to_frame(in_sec, fps),
                "out_frame": sec_to_frame(out_sec, fps),
                "in_tc": sec_to_tc(in_sec, fps),
                "out_tc": sec_to_tc(out_sec, fps),
                "marker_color": beat_type_to_color(bite.get("beat_type", "")),
            }
        )

    # Overlap check — log warning but don't drop (editor can decide)
    if _has_non_nested_overlap(validated):
        print(
            f"[soundbites.py] Warning: non-nested overlapping bites detected for {clip_path}",
            file=sys.stderr,
        )

    # Judge pass
    judge_scores = _judge_soundbites_claude(sc, brief, validated, api_key)
    flagged: list[dict[str, Any]] = []
    for bite in validated:
        score = judge_scores.get(bite["label"])
        if score is not None:
            bite["judge_score"] = score
            if score < JUDGE_FLAG_THRESHOLD:
                bite["flagged_for_review"] = True
                flagged.append(bite)

    return {
        "event": "clip_processed",
        "file": clip_path,
        "soundbites": validated,
        "flagged": flagged,
        "fps": fps,
        "cid": cid,
    }


def process_all(
    clip_paths: list[str],
    shoot_folder: str | Path,
    api_key: str,
) -> Any:
    """
    Generator. Yields progress events for each clip, then soundbites_complete.
    Reads brief.json automatically from shoot_folder.
    """
    from brief import read_brief

    brief_data = read_brief(shoot_folder)
    if brief_data is None or not brief_data.get("finalised"):
        yield {
            "event": "error",
            "reason": "Project brief not finalised. Complete the brief before identifying soundbites.",
        }
        return

    total = len(clip_paths)
    yield {"event": "soundbites_started", "total_clips": total}

    processed = 0
    skipped = 0
    errors = 0
    total_bites = 0
    total_flagged = 0

    for clip_path in clip_paths:
        try:
            result = process_clip(clip_path, shoot_folder, brief_data, api_key)
        except Exception as exc:
            print(f"[soundbites.py] Error processing {clip_path}: {exc}", file=sys.stderr)
            errors += 1
            yield {
                "event": "clip_error",
                "file": clip_path,
                "reason": str(exc),
            }
            continue

        soundbites = result["soundbites"]
        fps = result["fps"]

        # Write sidecar first
        sid.merge_clip(
            shoot_folder,
            clip_path,
            updates={"soundbites": soundbites, "fps": fps},
            new_stage="soundbites",
        )

        processed += 1
        total_bites += len(soundbites)
        total_flagged += len(result["flagged"])

        yield {
            "event": "clip_processed",
            "file": clip_path,
            "soundbite_count": len(soundbites),
            "flagged_count": len(result["flagged"]),
            "soundbites": soundbites,
        }

    yield {
        "event": "soundbites_complete",
        "total": total,
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
        "total_bites": total_bites,
        "total_flagged": total_flagged,
    }

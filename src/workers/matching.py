"""
Stage 7 — Match B-roll clips to A-roll soundbites.

For each soundbite identified in Stage 6, proposes literal and thematic
B-roll matches from the vision-tagged library. Runs an LLM-as-judge pass
and drops matches below 0.6. Writes match data to sidecars, then writes
Shot and Description fields to B-roll clips in Resolve and appends
MATCHES: lines to A-roll soundbite marker notes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import sidecar as sid

# ── Constants ──────────────────────────────────────────────────────────────────

ANTHROPIC_MODEL = "claude-sonnet-4-6"
JUDGE_DROP_THRESHOLD = 0.6
SHOT_FIELD_CAP = 8
DESCRIPTION_FIELD_CAP = 6
ANTHROPIC_COST_PER_SOUNDBITE_USD = 0.015

# ── Tool definitions ───────────────────────────────────────────────────────────

_PROPOSE_MATCHES_TOOL: dict[str, Any] = {
    "name": "propose_matches",
    "description": "Propose B-roll matches (literal and thematic) for a soundbite.",
    "input_schema": {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "clip_id": {"type": "string"},
                        "kind": {"type": "string", "enum": ["literal", "thematic"]},
                        "rationale": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    },
                    "required": ["clip_id", "kind", "rationale", "confidence"],
                },
            }
        },
        "required": ["matches"],
    },
}

_JUDGE_MATCHES_TOOL: dict[str, Any] = {
    "name": "judge_matches",
    "description": "Score each proposed match for kind accuracy and tone fit.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "clip_id": {"type": "string"},
                        "kind": {"type": "string"},
                        "kind_accuracy": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "tone_fit": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "judge_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    },
                    "required": ["clip_id", "kind", "kind_accuracy", "tone_fit", "judge_score"],
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


# ── Library building ───────────────────────────────────────────────────────────


def build_broll_library(shoot_folder: str | Path) -> list[dict[str, Any]]:
    """
    Return a list of B-roll clips with vision tags, suitable for prompt injection.
    Each entry: {clip_id, clip_path, tags_line, tags}
    """
    root = sid.sidecar_root(shoot_folder)
    if not root.exists():
        return []
    library: list[dict[str, Any]] = []
    for f in root.glob("*.json"):
        if f.name == "brief.json":
            continue
        try:
            sc = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if sc.get("classification") != "B-ROLL":
            continue
        if "vision" not in sc.get("stages_complete", []):
            continue
        tags = sc.get("vision", {}).get("tags", {})
        if not tags:
            continue
        cid = sc.get("clip_id", sid.clip_id(sc.get("source_path", f.stem)))
        clip_path = sc.get("source_path", "")
        tags_line = _format_tags_line(cid, tags)
        library.append(
            {
                "clip_id": cid,
                "clip_path": clip_path,
                "tags_line": tags_line,
                "tags": tags,
            }
        )
    return library


def _format_tags_line(clip_id: str, tags: dict[str, Any]) -> str:
    """Flatten vision tags to a compact single-line summary."""
    parts: list[str] = []
    if tags.get("subjects"):
        parts.append("subjects: " + ", ".join(tags["subjects"]))
    if tags.get("actions"):
        parts.append("actions: " + ", ".join(tags["actions"]))
    if tags.get("shot_type"):
        parts.append(f"shot: {tags['shot_type']}")
    if tags.get("mood"):
        parts.append(f"mood: {tags['mood']}")
    if tags.get("environment"):
        parts.append(f"env: {tags['environment']}")
    if tags.get("colour_palette"):
        parts.append("palette: " + ", ".join(tags["colour_palette"]))
    if tags.get("camera_motion"):
        parts.append(f"motion: {tags['camera_motion']}")
    detail = " | ".join(parts) if parts else "no tags"
    return f"{clip_id} | {detail}"


def format_library_for_prompt(library: list[dict[str, Any]]) -> str:
    return "\n".join(entry["tags_line"] for entry in library)


# ── Soundbite scanning ─────────────────────────────────────────────────────────


def scan_aroll_for_matching(shoot_folder: str | Path) -> list[dict[str, Any]]:
    """
    Return A-roll clips that have soundbites but not matching stage complete.
    Returns list of {clip_path, sidecar, soundbites}.
    """
    root = sid.sidecar_root(shoot_folder)
    if not root.exists():
        return []
    clips: list[dict[str, Any]] = []
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
        if "soundbites" not in stages:
            continue
        if "matching" in stages:
            continue
        soundbites = sc.get("soundbites", [])
        if not soundbites:
            continue
        clip_path = sc.get("source_path")
        if clip_path:
            clips.append({"clip_path": clip_path, "sidecar": sc, "soundbites": soundbites})
    return clips


def count_soundbites(aroll_clips: list[dict[str, Any]]) -> int:
    return sum(len(c["soundbites"]) for c in aroll_clips)


def estimate_matching(
    aroll_clips: list[dict[str, Any]], broll_library: list[dict[str, Any]]
) -> dict[str, Any]:
    n_bites = count_soundbites(aroll_clips)
    return {
        "soundbite_count": n_bites,
        "broll_clip_count": len(broll_library),
        "estimated_cost_usd": round(n_bites * ANTHROPIC_COST_PER_SOUNDBITE_USD, 4),
    }


# ── Brief helpers ─────────────────────────────────────────────────────────────


def _brief_tone(brief: dict[str, Any]) -> str:
    parts = []
    if brief.get("tone"):
        parts.append(brief["tone"])
    if brief.get("narrative_angle"):
        parts.append(brief["narrative_angle"])
    return ", ".join(parts) if parts else "unspecified"


def _brief_summary(brief: dict[str, Any]) -> str:
    parts: list[str] = []
    subject = brief.get("subject", {})
    if subject.get("name"):
        prof = subject.get("profession", "")
        parts.append(f"Subject: {subject['name']}" + (f" ({prof})" if prof else ""))
    if brief.get("narrative_angle"):
        parts.append(f"Narrative angle: {brief['narrative_angle']}")
    if brief.get("tone"):
        parts.append(f"Tone: {brief['tone']}")
    return "\n".join(parts) if parts else "(Brief not yet filled)"


# ── Claude matching call ───────────────────────────────────────────────────────


def _propose_matches_claude(
    soundbite: dict[str, Any],
    library_text: str,
    brief: dict[str, Any],
    speaker_labels: dict[str, str],
    api_key: str,
) -> list[dict[str, Any]]:
    import anthropic

    template = _load_prompt("match_broll.md")
    speaker_id = soundbite.get("speaker", "A") if isinstance(soundbite.get("speaker"), str) else "A"
    speaker_name = speaker_labels.get(speaker_id, "Speaker")
    duration = soundbite.get("out_sec", 0) - soundbite.get("in_sec", 0)

    prompt = (
        template.replace("{{BRIEF_SUMMARY}}", _brief_summary(brief))
        .replace("{{SOUNDBITE_ID}}", soundbite.get("id", ""))
        .replace("{{SPEAKER_NAME}}", speaker_name)
        .replace("{{BEAT_TYPE}}", soundbite.get("beat_type", ""))
        .replace("{{IN_TC}}", soundbite.get("in_tc", ""))
        .replace("{{OUT_TC}}", soundbite.get("out_tc", ""))
        .replace("{{DURATION_SEC}}", str(round(duration, 1)))
        .replace("{{EXTRACT}}", soundbite.get("extract", ""))
        .replace("{{WHY_USABLE}}", soundbite.get("why_usable", ""))
        .replace("{{BROLL_LIBRARY}}", library_text)
    )

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(  # type: ignore[no-matching-overload]
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        tools=[_PROPOSE_MATCHES_TOOL],
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "propose_matches":
            return list(block.input.get("matches", []))  # type: ignore[attr-defined]
    return []


# ── Judge pass ─────────────────────────────────────────────────────────────────


def _judge_matches_claude(
    soundbite: dict[str, Any],
    proposed: list[dict[str, Any]],
    library: list[dict[str, Any]],
    brief: dict[str, Any],
    api_key: str,
) -> dict[tuple[str, str], float]:
    """
    Returns {(clip_id, kind): judge_score} for each proposed match.
    Non-fatal: returns empty dict on failure.
    """
    import anthropic

    if not proposed:
        return {}

    template = _load_prompt("match_judge.md")
    library_by_id = {entry["clip_id"]: entry for entry in library}
    broll_details_lines = []
    for m in proposed:
        entry = library_by_id.get(m["clip_id"])
        if entry:
            broll_details_lines.append(entry["tags_line"])
        else:
            broll_details_lines.append(f"{m['clip_id']} | (not found in library)")

    prompt = (
        template.replace("{{BRIEF_SUMMARY}}", _brief_summary(brief))
        .replace("{{BRIEF_TONE}}", _brief_tone(brief))
        .replace("{{SOUNDBITE_ID}}", soundbite.get("id", ""))
        .replace("{{BEAT_TYPE}}", soundbite.get("beat_type", ""))
        .replace("{{EXTRACT}}", soundbite.get("extract", ""))
        .replace("{{PROPOSED_MATCHES_JSON}}", json.dumps(proposed, indent=2))
        .replace("{{BROLL_DETAILS}}", "\n".join(broll_details_lines))
    )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(  # type: ignore[no-matching-overload]
            model=ANTHROPIC_MODEL,
            max_tokens=512,
            tools=[_JUDGE_MATCHES_TOOL],
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == "judge_matches":
                scores = block.input.get("scores", [])  # type: ignore[attr-defined]
                return {
                    (s["clip_id"], s["kind"]): float(s["judge_score"])
                    for s in scores
                    if "clip_id" in s and "kind" in s
                }
    except Exception as exc:
        print(f"[matching.py] judge call failed: {exc}", file=sys.stderr)
    return {}


# ── Aggregation helpers ────────────────────────────────────────────────────────


def _compute_shot_string(matches: list[dict[str, Any]]) -> str:
    """
    Build the Shot field string: `sb_xxx_001:L, sb_yyy_002:T` sorted by confidence.
    Caps at SHOT_FIELD_CAP entries.
    """
    sorted_matches = sorted(matches, key=lambda m: m.get("confidence", 0), reverse=True)
    parts: list[str] = []
    for m in sorted_matches[:SHOT_FIELD_CAP]:
        suffix = "L" if m.get("kind") == "literal" else "T"
        parts.append(f"{m['soundbite_id']}:{suffix}")
    extra = len(sorted_matches) - SHOT_FIELD_CAP
    result = ", ".join(parts)
    if extra > 0:
        result += f" +{extra} more"
    return result


def _compute_description_string(matches: list[dict[str, Any]]) -> str:
    """
    Build the Description field: one line per match, literal first, capped at 6 lines.
    """
    sorted_matches = sorted(
        matches,
        key=lambda m: (0 if m.get("kind") == "literal" else 1, -m.get("confidence", 0)),
    )
    lines: list[str] = []
    for m in sorted_matches[:DESCRIPTION_FIELD_CAP]:
        kind_label = "Literal" if m.get("kind") == "literal" else "Thematic"
        rationale = m.get("rationale", "")
        lines.append(f"{kind_label} match for {m['soundbite_id']} — {rationale}")
    return "\n".join(lines)


def _matches_line_for_marker(matches: list[dict[str, Any]]) -> str:
    """Build the MATCHES: annotation line appended to A-roll marker notes."""
    parts: list[str] = []
    for m in sorted(matches, key=lambda x: x.get("confidence", 0), reverse=True):
        cid = m.get("broll_clip_id", "")
        kind = m.get("kind", "")
        parts.append(f"{cid} ({kind})")
    return "MATCHES: " + ", ".join(parts)


# ── Main processing pipeline ───────────────────────────────────────────────────


def process_all(shoot_folder: str | Path, api_key: str) -> Any:
    """
    Generator. Yields progress events, then matching_complete.
    Reads brief.json automatically. Requires brief finalised.
    """
    from brief import read_brief

    brief_data = read_brief(shoot_folder)
    if brief_data is None or not brief_data.get("finalised"):
        yield {
            "event": "error",
            "reason": "Project brief not finalised. Complete the brief before running B-roll matching.",
        }
        return

    library = build_broll_library(shoot_folder)
    aroll_clips = scan_aroll_for_matching(shoot_folder)

    if not library:
        yield {
            "event": "error",
            "reason": "No vision-tagged B-roll clips found. Run vision tagging (Stage 4) first.",
        }
        return

    total_bites = count_soundbites(aroll_clips)
    yield {
        "event": "matching_started",
        "total_soundbites": total_bites,
        "broll_clips": len(library),
    }

    library_text = format_library_for_prompt(library)

    # Track matches per B-roll clip (for aggregated metadata writes)
    broll_matches: dict[str, list[dict[str, Any]]] = {}

    processed_bites = 0
    rejected_bites = 0
    errors = 0

    for clip_entry in aroll_clips:
        clip_path = clip_entry["clip_path"]
        sc = clip_entry["sidecar"]
        soundbites_list = clip_entry["soundbites"]
        speaker_labels: dict[str, str] = sc.get("speaker_labels", {})

        updated_soundbites = list(soundbites_list)

        for i, bite in enumerate(soundbites_list):
            try:
                raw_matches = _propose_matches_claude(
                    bite, library_text, brief_data, speaker_labels, api_key
                )

                # Empty rationale guard
                raw_matches = [m for m in raw_matches if m.get("rationale", "").strip()]

                judge_scores = _judge_matches_claude(
                    bite, raw_matches, library, brief_data, api_key
                )

                accepted: list[dict[str, Any]] = []
                rejected: list[dict[str, Any]] = []
                for m in raw_matches:
                    score = judge_scores.get((m["clip_id"], m["kind"]))
                    if score is not None and score < JUDGE_DROP_THRESHOLD:
                        rejected.append({**m, "judge_score": score})
                    else:
                        accepted.append(
                            {
                                **m,
                                "judge_score": score if score is not None else None,
                                "broll_clip_id": m["clip_id"],
                                "soundbite_id": bite.get("id", ""),
                            }
                        )

                rejected_bites += len(rejected)

                # Update per-bite matches in the soundbite entry
                updated_soundbites[i] = {
                    **bite,
                    "matches": [
                        {"clip_id": m["clip_id"], "kind": m["kind"], "confidence": m["confidence"]}
                        for m in accepted
                    ],
                    "matches_rejected": [
                        {
                            "clip_id": m["clip_id"],
                            "kind": m["kind"],
                            "judge_score": m.get("judge_score", 0),
                        }
                        for m in rejected
                    ],
                }

                # Accumulate B-roll matches
                for m in accepted:
                    broll_cid = m["clip_id"]
                    if broll_cid not in broll_matches:
                        broll_matches[broll_cid] = []
                    broll_matches[broll_cid].append(
                        {
                            "soundbite_id": bite.get("id", ""),
                            "kind": m["kind"],
                            "confidence": m["confidence"],
                            "judge_score": m.get("judge_score"),
                            "rationale": m.get("rationale", ""),
                        }
                    )

                processed_bites += 1
                yield {
                    "event": "soundbite_matched",
                    "soundbite_id": bite.get("id", ""),
                    "clip_file": clip_path,
                    "accepted": len(accepted),
                    "rejected": len(rejected),
                }

            except Exception as exc:
                errors += 1
                print(
                    f"[matching.py] Error matching bite {bite.get('id', '?')}: {exc}",
                    file=sys.stderr,
                )
                yield {
                    "event": "soundbite_error",
                    "soundbite_id": bite.get("id", ""),
                    "reason": str(exc),
                }

        # Write updated A-roll sidecar (soundbites with matches)
        sid.merge_clip(
            shoot_folder,
            clip_path,
            updates={"soundbites": updated_soundbites},
            new_stage="matching",
        )

    # Write B-roll sidecars with aggregated match data
    library_by_id = {entry["clip_id"]: entry for entry in library}
    for broll_cid, matches in broll_matches.items():
        entry = library_by_id.get(broll_cid)
        if not entry or not entry.get("clip_path"):
            continue
        sid.merge_clip(
            shoot_folder,
            entry["clip_path"],
            updates={"matches": matches},
            new_stage="matching",
        )

    # Coverage report
    all_soundbite_ids = [
        b.get("id", "") for clip_entry in aroll_clips for b in clip_entry["soundbites"]
    ]
    covered_ids = {
        b.get("id", "")
        for clip_entry in aroll_clips
        for b in clip_entry["soundbites"]
        if b.get("matches")
        or any(
            m.get("soundbite_id") == b.get("id")
            for matches in broll_matches.values()
            for m in matches
        )
    }
    uncovered = [bid for bid in all_soundbite_ids if bid not in covered_ids]

    yield {
        "event": "matching_complete",
        "total_soundbites": total_bites,
        "processed": processed_bites,
        "errors": errors,
        "total_matches": sum(len(m) for m in broll_matches.values()),
        "rejected": rejected_bites,
        "broll_clips_matched": len(broll_matches),
        "uncovered_soundbites": uncovered,
    }

"""
Stage 5 — Conversational project brief.

brief.json lives at <shoot_folder>/04_EXPORT/.cairn/brief.json.
Conversation is multi-turn: each call to send_message appends the user
message, calls Claude (with update_brief_draft / finalise_brief tools),
and saves the updated draft + conversation_log back to brief.json.

No Resolve calls. Pure Claude + disk I/O.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import sidecar as sid

# ── Constants ──────────────────────────────────────────────────────────────────

BRIEF_SCHEMA_VERSION = 1
ANTHROPIC_MODEL = "claude-sonnet-4-6"
REQUIRED_FIELDS = {"subject", "deliverables", "narrative_angle", "tone", "privacy"}
TOTAL_FIELDS = 8  # subject, deliverables, narrative_angle, tone, themes, word_boost, privacy, notes

# ── Tool definitions ───────────────────────────────────────────────────────────

_UPDATE_BRIEF_DRAFT_TOOL: dict[str, Any] = {
    "name": "update_brief_draft",
    "description": (
        "Extract and save any project brief fields from the user's latest message. "
        "Only set fields the user has explicitly stated — never invent values."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "subject": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "profession": {"type": "string"},
                    "location": {"type": "string"},
                    "one_liner": {"type": "string"},
                },
            },
            "deliverables": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "platform": {"type": "string"},
                        "target_duration_sec": {"type": "array", "items": {"type": "number"}},
                        "aspect_ratio": {"type": "string"},
                        "tone": {"type": "string"},
                    },
                },
            },
            "narrative_angle": {"type": "string"},
            "tone": {"type": "string"},
            "themes": {"type": "array", "items": {"type": "string"}},
            "word_boost_terms": {"type": "array", "items": {"type": "string"}},
            "privacy": {
                "type": "object",
                "properties": {
                    "cloud_transcription_allowed": {"type": "boolean"},
                    "cloud_vision_allowed": {"type": "boolean"},
                },
            },
            "notes": {"type": "string"},
        },
    },
}

_FINALISE_BRIEF_TOOL: dict[str, Any] = {
    "name": "finalise_brief",
    "description": (
        "Mark the brief as complete with all required fields confirmed by the user. "
        "Only call this after the user has explicitly confirmed the brief summary."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "subject": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "profession": {"type": "string"},
                    "location": {"type": "string"},
                    "one_liner": {"type": "string"},
                },
                "required": ["name"],
            },
            "deliverables": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "platform": {"type": "string"},
                        "target_duration_sec": {"type": "array", "items": {"type": "number"}},
                        "aspect_ratio": {"type": "string"},
                        "tone": {"type": "string"},
                    },
                    "required": ["platform"],
                },
            },
            "narrative_angle": {"type": "string"},
            "tone": {"type": "string"},
            "themes": {"type": "array", "items": {"type": "string"}},
            "word_boost_terms": {"type": "array", "items": {"type": "string"}},
            "privacy": {
                "type": "object",
                "properties": {
                    "cloud_transcription_allowed": {"type": "boolean"},
                    "cloud_vision_allowed": {"type": "boolean"},
                },
                "required": ["cloud_transcription_allowed", "cloud_vision_allowed"],
            },
            "notes": {"type": "string"},
        },
        "required": ["subject", "deliverables", "narrative_angle", "tone", "privacy"],
    },
}

_JUDGE_BRIEF_TOOL: dict[str, Any] = {
    "name": "judge_brief",
    "description": "Score the accuracy of the brief against the conversation log.",
    "input_schema": {
        "type": "object",
        "properties": {
            "overall_score": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Overall accuracy 0-1",
            },
            "invented_fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Fields that were inferred without explicit user confirmation",
            },
            "missing_fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Fields the user stated but that are missing from the brief",
            },
            "notes": {"type": "string"},
        },
        "required": ["overall_score", "invented_fields", "missing_fields"],
    },
}

# ── Brief file I/O ─────────────────────────────────────────────────────────────


def brief_path(shoot_folder: str | Path) -> Path:
    return sid.sidecar_root(shoot_folder) / "brief.json"


def read_brief(shoot_folder: str | Path) -> dict[str, Any] | None:
    p = brief_path(shoot_folder)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def write_brief(shoot_folder: str | Path, data: dict[str, Any]) -> None:
    p = brief_path(shoot_folder)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def delete_brief(shoot_folder: str | Path) -> None:
    p = brief_path(shoot_folder)
    if p.exists():
        p.unlink()


def count_fields(brief_data: dict[str, Any]) -> dict[str, int]:
    """Return {filled, total, required_filled, required_total}."""
    field_names = [
        "subject",
        "deliverables",
        "narrative_angle",
        "tone",
        "themes",
        "word_boost_terms",
        "privacy",
        "notes",
    ]
    filled = sum(1 for f in field_names if brief_data.get(f) not in (None, [], "", {}))
    req_filled = sum(1 for f in REQUIRED_FIELDS if brief_data.get(f) not in (None, [], "", {}))
    return {
        "filled": filled,
        "total": len(field_names),
        "required_filled": req_filled,
        "required_total": len(REQUIRED_FIELDS),
    }


# ── Project context for system prompt ─────────────────────────────────────────


def get_project_summary(shoot_folder: str | Path) -> str:
    """Build a short project summary for injection into the system prompt."""
    root = sid.sidecar_root(shoot_folder)
    if not root.exists():
        return "(No clips indexed yet.)"

    a_roll = b_roll = transcribed = vision_tagged = 0
    for f in root.glob("*.json"):
        if f.name == "brief.json":
            continue
        try:
            sc = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        cls = sc.get("classification", "")
        if cls == "A-ROLL":
            a_roll += 1
        elif cls == "B-ROLL":
            b_roll += 1
        stages = sc.get("stages_complete", [])
        if "transcribe" in stages:
            transcribed += 1
        if "vision" in stages:
            vision_tagged += 1

    shoot_name = Path(shoot_folder).name
    parts = [f'Project: "{shoot_name}"']
    if a_roll or b_roll:
        parts.append(f"{a_roll} A-roll clips, {b_roll} B-roll clips")
    if transcribed:
        parts.append(f"{transcribed} clips transcribed")
    if vision_tagged:
        parts.append(f"{vision_tagged} B-roll clips vision-tagged")
    if not (a_roll or b_roll):
        parts.append("(Clips not yet classified)")
    return " · ".join(parts)


# ── System prompt builder ─────────────────────────────────────────────────────


def _load_prompt(filename: str) -> str:
    p = Path(__file__).resolve().parent.parent / "orchestrator" / "prompts" / filename
    if p.exists():
        return p.read_text()
    return ""


def _build_system_prompt(shoot_folder: str | Path, brief: dict[str, Any]) -> str:
    template = _load_prompt("brief_system.md")
    context = get_project_summary(shoot_folder)
    return template.replace("{{PROJECT_CONTEXT}}", context)


# ── Draft update helpers ───────────────────────────────────────────────────────


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge update into base, overwriting at leaf level."""
    result = dict(base)
    for k, v in update.items():
        if v is None:
            continue
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        elif (isinstance(v, list) and v) or not isinstance(v, (list, dict)):
            result[k] = v
    return result


def _apply_tool_input(brief: dict[str, Any], tool_input: dict[str, Any]) -> dict[str, Any]:
    """Merge a tool call's input into the brief dict."""
    return _deep_merge(brief, tool_input)


# ── Main API: send_message ────────────────────────────────────────────────────


def send_message(
    shoot_folder: str | Path,
    user_message: str,
    api_key: str,
) -> dict[str, Any]:
    """
    Send a user message and return {reply, draft, finalised, fields, judge}.
    Appends to conversation_log and saves brief.json on each call.
    """
    import anthropic

    existing = read_brief(shoot_folder)
    brief: dict[str, Any] = (
        existing
        if existing is not None
        else {
            "schema_version": BRIEF_SCHEMA_VERSION,
            "finalised": False,
            "conversation_log": [],
        }
    )
    conversation_log: list[dict[str, Any]] = brief.get("conversation_log", [])

    # Build messages list for Claude
    api_messages: list[dict[str, Any]] = []
    for entry in conversation_log:
        api_messages.append({"role": entry["role"], "content": entry["content"]})
    api_messages.append({"role": "user", "content": user_message})

    system_prompt = _build_system_prompt(shoot_folder, brief)
    client = anthropic.Anthropic(api_key=api_key)

    # ── Initial call ──────────────────────────────────────────────────────────
    response = client.messages.create(  # type: ignore[no-matching-overload]
        model=ANTHROPIC_MODEL,
        max_tokens=2048,
        system=system_prompt,
        tools=[_UPDATE_BRIEF_DRAFT_TOOL, _FINALISE_BRIEF_TOOL],
        messages=api_messages,
    )

    # ── Process tool calls ────────────────────────────────────────────────────
    tool_uses = [b for b in response.content if b.type == "tool_use"]
    finalised = False

    for tc in tool_uses:
        tool_input = dict(tc.input)  # type: ignore[arg-type]
        if tc.name == "update_brief_draft":
            brief = _apply_tool_input(brief, tool_input)
        elif tc.name == "finalise_brief":
            brief = _apply_tool_input(brief, tool_input)
            brief["finalised"] = True
            finalised = True

    assistant_reply = ""

    if tool_uses:
        # Build tool_result messages and make a follow-up call for the text response
        assistant_content = [
            {
                "type": b.type,
                "id": getattr(b, "id", None),
                "name": getattr(b, "name", None),
                "input": dict(b.input) if b.type == "tool_use" else None,  # type: ignore[attr-defined]
                "text": b.text if b.type == "text" else None,
            }  # type: ignore[attr-defined]
            for b in response.content
        ]
        # Strip None values from each block
        assistant_content = [
            {k: v for k, v in block.items() if v is not None} for block in assistant_content
        ]

        tool_results = [
            {
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": "OK",
            }
            for tc in tool_uses
        ]

        api_messages.append({"role": "assistant", "content": assistant_content})
        api_messages.append({"role": "user", "content": tool_results})

        response2 = client.messages.create(  # type: ignore[no-matching-overload]
            model=ANTHROPIC_MODEL,
            max_tokens=1024,
            system=system_prompt,
            tools=[_UPDATE_BRIEF_DRAFT_TOOL, _FINALISE_BRIEF_TOOL],
            messages=api_messages,
        )
        for block in response2.content:
            if block.type == "text":
                assistant_reply += block.text
    else:
        for block in response.content:
            if block.type == "text":
                assistant_reply += block.text

    # ── Update conversation log ───────────────────────────────────────────────
    conversation_log.append({"role": "user", "content": user_message})
    conversation_log.append({"role": "assistant", "content": assistant_reply})
    brief["conversation_log"] = conversation_log
    write_brief(shoot_folder, brief)

    # ── LLM-as-judge on finalisation ──────────────────────────────────────────
    judge_result: dict[str, Any] | None = None
    if finalised:
        judge_result = _run_judge(shoot_folder, brief, client)

    return {
        "reply": assistant_reply,
        "draft": brief,
        "finalised": finalised,
        "fields": count_fields(brief),
        "judge": judge_result,
    }


def _run_judge(
    shoot_folder: str | Path,
    brief: dict[str, Any],
    client: Any,
) -> dict[str, Any] | None:
    """Run the LLM-as-judge to score brief accuracy. Non-fatal on failure."""
    template = _load_prompt("brief_finalise.md")
    if not template:
        return None

    # Strip conversation_log from brief copy for readability
    brief_for_judge = {k: v for k, v in brief.items() if k != "conversation_log"}
    conv_text = "\n".join(
        f"{entry['role'].upper()}: {entry['content']}"
        for entry in brief.get("conversation_log", [])
        if entry["role"] in ("user", "assistant")
    )

    judge_prompt = template.replace(
        "{{BRIEF_JSON}}", json.dumps(brief_for_judge, indent=2)
    ).replace("{{CONVERSATION_LOG}}", conv_text)

    try:
        response = client.messages.create(  # type: ignore[no-matching-overload]
            model=ANTHROPIC_MODEL,
            max_tokens=512,
            tools=[_JUDGE_BRIEF_TOOL],
            messages=[{"role": "user", "content": judge_prompt}],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == "judge_brief":
                return dict(block.input)  # type: ignore[return-value]
    except Exception:
        pass
    return None


# ── Save edited brief (from UI form) ─────────────────────────────────────────


def save_brief(shoot_folder: str | Path, updates: dict[str, Any]) -> dict[str, Any]:
    """Merge UI edits into the existing brief and save. Returns the updated brief."""
    existing = read_brief(shoot_folder) or {
        "schema_version": BRIEF_SCHEMA_VERSION,
        "finalised": False,
        "conversation_log": [],
    }
    merged = _apply_tool_input(existing, updates)
    write_brief(shoot_folder, merged)
    return merged

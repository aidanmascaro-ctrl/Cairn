# specs/brief.md — Stage 5: Conversational project brief

## Purpose

After indexing (Stages 1–4) finishes, open a chat panel that extracts a structured project brief from the user through conversation. The brief is what drives soundbite selection (Stage 6) and B-roll matching (Stage 7). It's stored as JSON, editable later, and persists across sessions.

## Inputs

- Indexing complete for the current project (UI gates the chat panel on this).
- Claude API key (keychain).
- The clip-level artefacts from Stages 1–4 are accessible — the brief chat can reference ("I can see you have 12 A-roll clips, 47 B-roll clips, about 34 minutes of dialogue") to ground the conversation.
- Existing `brief.json` if present (resume an in-progress or editable brief).

## Outputs

**One file:** `<project>/04_EXPORT/.cairn/brief.json`

```json
{
  "schema_version": 1,
  "subject": {
    "name": "Lauren",
    "profession": "ceramicist",
    "location": "local (Perth)",
    "one_liner": "Perth-based ceramicist with an origin story worth telling"
  },
  "deliverables": [
    {
      "platform": "instagram_reel",
      "target_duration_sec": [60, 90],
      "aspect_ratio": "9:16",
      "tone": "intimate, warm"
    },
    {
      "platform": "youtube",
      "target_duration_sec": [120, 180],
      "aspect_ratio": "16:9",
      "tone": "warm, craft-documentary"
    }
  ],
  "narrative_angle": "origin story + craft passion",
  "themes": ["origin", "craft", "flow-state", "community"],
  "desired_soundbite_types": ["origin-story", "emotional-peak", "factual", "hook"],
  "tone": "warm, intimate, unhurried",
  "word_boost_terms": ["ceramic", "wheel", "glaze", "kiln", "porcelain"],
  "privacy": {
    "cloud_transcription_allowed": true,
    "cloud_vision_allowed": true
  },
  "notes": "Free-text notes captured during the conversation.",
  "conversation_log": [
    {"role": "assistant", "content": "..."},
    {"role": "user", "content": "..."}
  ],
  "finalised": true
}
```

## Algorithm

### Conversation flow

One question at a time. Claude leads. Each Claude turn has:
- A short acknowledgement of the prior user message
- The next question, clearly scoped to one field

**Question order** (Claude may follow adaptively, but this is the default sequence):

1. "Who or what is this project about? Tell me about the subject in a sentence or two." → `subject.*`
2. "Where will this live — what platforms, what rough durations?" → `deliverables[]`
3. "What's the angle? Origin story, case study, promo, tutorial, something else?" → `narrative_angle`
4. "What emotional tone are you going for? (e.g. warm and intimate, sharp and urgent, playful)" → `tone`
5. "Are there specific themes or through-lines you want the edit to hit?" → `themes[]`
6. "Any jargon, names, or technical terms that might trip up transcription? I'll boost them so they're recognised correctly." → `word_boost_terms[]`
7. "Privacy check — is this under NDA or otherwise sensitive? If so I'll default to local-only transcription and vision." → `privacy.*`
8. "Anything else I should know before I pick soundbites?" → `notes`
9. Confirmation: Claude summarises the brief; user confirms or edits.

**Adaptivity:** if the user volunteers multiple answers in one message ("It's a reel + YouTube cut of Lauren the ceramicist, warm tone, origin story"), Claude parses all of it and skips the covered questions, moving to what's missing.

### Structured extraction

Two-pass:

1. **Turn-level**: per user message, Claude extracts any partial fields into a working draft. Tool use with a `update_brief_draft` tool that takes partial JSON.
2. **Final pass**: when the user says "that's it" or similar, Claude reviews the conversation log end-to-end and emits a clean final `brief.json` via a `finalise_brief` tool.

Both tool calls return the updated draft so the UI can show live progress ("4 / 9 fields captured").

### Prompts

All prompts in `src/orchestrator/prompts/`:
- `brief_system.md` — persona, constraints, question order, meta-reasoning block on common failure modes (leading questions, over-filling fields from one vague user answer)
- `brief_finalise.md` — final extraction pass instructions

### Edit mode

Any time after finalisation:
- User opens Settings → Brief → sees current JSON in a form UI (not raw JSON — a friendly form).
- Edits are written to `brief.json` directly.
- If `soundbites` or `matching` have already run, editing the brief surfaces a "Re-run matching with new brief?" prompt. Does not invalidate transcripts or vision tags — those are brief-independent.

### Referencing indexed content

The brief chat knows about the project state. Claude's system prompt includes a short summary: "You are helping with project `251125 sample`. It has 12 A-roll clips totaling ~34 min and 47 B-roll clips. Transcripts and vision tags are complete."

This helps Claude ground questions ("I notice a couple of B-roll clips look like they might be outdoors — was this partly shot on location?").

## Sensors / Validations

- **JSON schema validation** via Zod (Node) before writing `brief.json`. Malformed extraction → ask Claude to try again, don't save partial.
- **Field completeness gate** — `finalised: true` only after all required fields are non-null. Missing fields block Stages 6–7 with a UI banner ("Complete the brief first").
- **LLM-as-judge (loop-back)** on finalisation: second Claude call reads the conversation + final JSON and scores "Did the assistant capture what the user said, without inventing or omitting?" Logs below-threshold cases to `fix_plan.md`.
- **No-op protection**: if the user re-opens the chat after finalising, it loads the existing brief and offers "Edit" / "Start Over" — never silently overwrites.
- **Privacy consistency**: if `privacy.cloud_transcription_allowed: false` is set in the brief but Stage 3 already ran against the cloud for some clips, surface a clear warning in the UI and offer re-transcribe with the local path.

### Taste-invariant cross-checks

- #4 (no hardcoded keys): brief doesn't touch API keys; passes through keytar-backed settings.
- #7 (flag ambiguous): ambiguous or contradictory user input surfaces as a clarifying question, never silent assumption.
- #8 (cloud opt-in): privacy question is mandatory before finalisation. Default answer must be explicit — no implicit `true`.

## Open questions

- **When to open the chat** — spec says "after indexing completes". But indexing can take 15+ min on large shoots. Should we open the brief chat *during* indexing (so Claude can work in parallel) and gate only Stages 6–7 on both being done? Leaning: yes, parallel — improves perceived latency.
- **Voice input** — dictating the brief would feel natural. Out of scope v1; nice v2.
- **Brief templates** — editors tend to do similar projects (interview shorts, case-study reels). Offer "start from a template" in v2. Not v1.
- **How strict on required fields?** If a user doesn't want to set `word_boost_terms`, that's fine. Mark that field optional. Required: subject, deliverables, narrative_angle, tone, privacy.
- **Sharing briefs across projects** — not a v1 feature. Each project has its own.
- **Conversation log retention** — keep forever in `brief.json`? Helpful for debugging prompts. Disk cost negligible. Yes.

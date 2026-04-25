# specs/matching.md — Stage 7: Match B-roll to soundbites

## Purpose

For every identified soundbite (Stage 6), suggest B-roll clips that could cover it — in two flavours: **literal** (directly shows what the speaker is saying) and **thematic** (serves the emotional or conceptual beat without literal depiction). Write match IDs + notes into the B-roll clips' Resolve metadata so the editor can filter/sort in the media pool, and append match summaries to the A-roll soundbite marker notes.

## Inputs

- All A-roll soundbite sidecars (Stage 6 done)
- All B-roll vision sidecars (Stage 4 done)
- `brief.json` — the `tone`, `themes`, `narrative_angle` inform thematic judgements
- Claude API key

## Outputs

### Per B-roll clip

Three Resolve metadata fields (each serves a different workflow need):

- **`Keywords`** (already written by Stage 4) — visual tags, unchanged. Filterable in Media Pool searches.
- **`Shot`** — comma-joined list of soundbite IDs this clip matches, with suffix `:L` or `:T`:
  - Example: `origin_story:L, passion_quote:T`
  - Sortable as a column in List View — editor can group all clips that match a given bite.
- **`Description`** — free-text, one line per match:
  - Example:
    ```
    Literal match for origin_story — close-up hands shaping clay on wheel, warm tungsten key light.
    Thematic match for passion_quote — wide studio golden-hour, quiet focused mood.
    ```

### Per A-roll clip

Append a line to each soundbite marker's note:

```
MATCHES: clip_042 (literal), clip_081 (thematic), clip_113 (literal)
```

### Sidecars

**B-roll sidecar append:**
```json
{
  "matches": [
    {"soundbite_id": "sb_<a-roll-id>_001", "kind": "literal", "confidence": 0.91, "judge_score": 0.88, "rationale": "Close-up hands on wheel literally shows 'clay'."},
    {"soundbite_id": "sb_<a-roll-id>_004", "kind": "thematic", "confidence": 0.72, "judge_score": 0.78, "rationale": "Warm isolated studio tone matches the introspective register."}
  ],
  "stages_complete": [..., "matching"]
}
```

**A-roll sidecar append** (in the corresponding soundbite entry):
```json
{
  "id": "sb_<clip>_001",
  ...,
  "matches": [
    {"clip_id": "<broll-clip-id>", "kind": "literal", "confidence": 0.91},
    {"clip_id": "<broll-clip-id>", "kind": "thematic", "confidence": 0.72}
  ]
}
```

## Algorithm

### 1. Build the B-roll library summary

For Claude to reason over, flatten every B-roll clip's `vision.tags` into a compact line:

```
clip_042 | subjects: hands, potter's wheel, wet clay | actions: shaping, centering | shot: close-up | mood: intimate | env: indoor studio | palette: warm tungsten, earthy ochre | motion: static
clip_081 | subjects: studio, shelves, ceramics | actions: — | shot: wide | mood: serene | env: indoor golden-hour | palette: honey, amber | motion: static
...
```

One line per clip; human-scannable; small token cost.

### 2. For each soundbite, request matches

**Prompt:** `src/orchestrator/prompts/match_broll.md`

**Meta-reasoning block:**

```
<meta_reasoning>
Two failure modes:
1. Matching literally when the language is figurative — "I was on fire that week"
   paired with a literal shot of fire. Fails if the metaphor is obvious.
2. Matching thematically too broadly — tagging half the library as "thematic" for
   any emotional bite. Thematic must still have a specific visual rationale.
Address: require an explicit rationale per match; reject vague rationales.
</meta_reasoning>
```

**Prompt body (structure):**

```
Soundbite:
  ID: sb_<...>_001
  Speaker: Lauren
  Transcript: "So I started at 19 after a pottery class at uni..."
  Beat type: origin-story
  Tone of project: warm, intimate, unhurried

B-roll library:
<flat library listing>

Return, via the `propose_matches` tool:
- Up to 3 literal matches (visual directly references what's being said). Each with rationale.
- Up to 3 thematic matches (visual serves the emotional/conceptual register). Each with rationale.
- A clip may be both literal and thematic — return separate entries with different `kind`.
- Confidence 0.0–1.0 per match.
- If no strong matches exist, return fewer (including zero). Do not pad.
```

**Tool-use:** `propose_matches` — structured output, same shape as the sidecar match entry.

### 3. Judge pass

**Prompt:** `src/orchestrator/prompts/match_judge.md`

Second Claude call: "Here is the soundbite, the proposed matches, and each B-roll clip's tags. Score each match 0–1 on (a) literal/thematic accuracy and (b) fit with project tone. Any below 0.6?"

Writes `judge_score`. Matches with `judge_score < 0.6` are **dropped before writing to Resolve** — unlike soundbites, low-scoring matches are noise. Drop, log to sidecar under `"matches_rejected"`, surface aggregate in UI ("8 matches dropped during judging; view details").

### 4. Write

Order:
1. **Sidecars first** — all B-roll and A-roll sidecars updated with final match sets.
2. **Resolve writes** through the single serial worker:
   - For each B-roll clip: compute new `Shot` and `Description` strings from its aggregated matches; call `SetMetadata("Shot", …)` and `SetMetadata("Description", …)`. Do not touch `Keywords` (owned by Stage 4).
   - For each A-roll clip: rewrite the affected soundbite markers' notes to append the `MATCHES:` line. Marker replacement must preserve `customData`, `color`, `name`, `frame`, `duration`.

### 5. Aggregation semantics

One B-roll clip can match many soundbites. Aggregation rules:

- **Shot** field: all `sb_id:kind` tags, deduplicated, sorted by confidence descending. Cap at 8 (if more, take top 8 and append `+N more` — the full set is in the sidecar).
- **Description** field: one line per match, sorted by `kind` (literal first) then confidence. Cap at 6 lines.

Caps prevent Resolve from truncating or looking cluttered in List View.

### 6. Idempotency

Re-run strategy:
- If sidecars already have `"matches"`, diff against newly proposed. Write only if changed.
- Resolve fields are computed from the sidecar every time, so they stay consistent with the source of truth.

## Sensors / Validations

- **Rationale non-empty** — matches with empty/vague rationales rejected before judge.
- **Judge confidence threshold** — matches with `judge_score < 0.6` dropped; logged.
- **Coverage report** — after a run, sidebar shows avg matches per bite, bites with zero matches flagged (editor may need to shoot more B-roll). In the sidecar, project-level: `{ "bites_total": 12, "bites_with_matches": 11, "bites_uncovered": ["sb_..._007"] }`.
- **No B-roll stolen** — Stage 4 owns `Keywords`; Stage 7 must not touch it. Test.
- **Marker note preservation** — the appended `MATCHES:` line does not overwrite the original note; verified by test.
- **Prompt versioning** — same as Stage 6.
- **LLM-as-judge** applied per-clip rather than whole-project (token cost matters on shoots with hundreds of clips).

### Taste-invariant cross-checks

- #3 (serial Resolve): all metadata writes + marker rewrites through the single worker.
- #5 (no placeholders): low-confidence matches dropped entirely, not stored as "weak guess".
- #6 (sidecar first): all sidecars finalised before any Resolve write.
- #7 (flag ambiguous): zero-match bites surfaced prominently — never silently accept.

## Open questions

- **Confidence calibration** — are literal matches generally confident and thematic generally softer, or vice versa? Calibrate per-kind thresholds after first real shoot.
- **Clip reuse across bites** — no current penalty for reusing the same B-roll clip for many bites. In practice an editor wants variety. Add a "variety hint" in the prompt? "Prefer clips that haven't been suggested for other bites in the project." Low-priority v1.
- **User overrides** — if the user rejects a match via the sidebar UI, does that feed back into future runs (preference learning) or just apply locally? v1: local reject only, stored as `"user_rejected": true`. v2 could feed back.
- **Cost** — this stage is the most token-heavy (N bites × M clips in the library, both propose + judge). Batch strategies: one call per bite proposes over the full library; judge batched. Estimate on ceramics scale (12 bites × 47 clips) before launch.
- **`MediaPoolItem.SetMetadata("Shot", …)` field support** — confirm Resolve exposes `Shot` via the scripting API; if not, use a custom metadata key. Record findings in `AGENT.md`.
- **Rank ties** — two matches with identical judge scores. Tie-break by rationale-length proxy for specificity? Or alphabetical clip_id? v1: alphabetical; predictable is better than clever.

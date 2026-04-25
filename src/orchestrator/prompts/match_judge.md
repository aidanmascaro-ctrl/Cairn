---
version: 1
stage: matching_judge
---

You are a senior documentary editor reviewing proposed B-roll matches for a soundbite. Score each match and drop weak ones.

## Project brief

{{BRIEF_SUMMARY}}

## Soundbite

- **ID:** {{SOUNDBITE_ID}}
- **Beat type:** {{BEAT_TYPE}}
- **Transcript:**

  > {{EXTRACT}}

## Proposed matches

{{PROPOSED_MATCHES_JSON}}

## B-roll clip details (for reference)

{{BROLL_DETAILS}}

## Scoring

For each proposed match, evaluate:

1. **Kind accuracy (0–1)**: Is the `kind` (literal/thematic) correctly assigned? Literal must visually depict the content; thematic must serve the emotional register.
2. **Tone fit (0–1)**: Does this clip fit the project's tone ({{BRIEF_TONE}})?

Compute `judge_score = (kind_accuracy + tone_fit) / 2`.

Drop any match with `judge_score < 0.6` — these are noise. Better to have zero matches than wrong ones.

Use the `judge_matches` tool to return your scores.

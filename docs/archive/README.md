# docs/archive — deferred-stage material (NOT active)

These files describe pipeline stages that were **removed from scope** in commit
`966ce94` ("refactor: scope reset — strip stages 3-8, rebuild classifier as
duration+face"). They are preserved here — off the active read path — so the
work isn't lost if the stages return, and so a session reading `specs/*.md` or
`src/orchestrator/prompts/` does **not** mistake them for current work.

> **Do not implement anything in this folder as if it were in scope.** The
> shipping product is the 4-stage pipeline (Ingest → Classify → Colour → Build
> Assembly Timeline). See `specs/overview.md` for current architecture.

## What's here

### `specs/` — deferred stage specifications

| File            | Deferred stage                                         |
| --------------- | ------------------------------------------------------ |
| `transcribe.md` | Stage 3 — AssemblyAI / faster-whisper → subtitle track |
| `vision.md`     | Stage 4 (old) — frame extraction → Claude visual tags  |
| `brief.md`      | Stage 5 — conversational project brief                 |
| `soundbites.md` | Stage 6 — Claude soundbite selection → Resolve markers |
| `matching.md`   | Stage 7 — literal + thematic B-roll matching           |
| `sidebar.md`    | Stage 8 — story-map cockpit UI                         |

### `prompts/` — LLM prompt templates for the deferred AI stages

`vision_tag.md`, `brief_system.md`, `brief_finalise.md`, `soundbite_select.md`,
`soundbite_judge.md`, `match_broll.md`, `match_judge.md`. These had zero callers
in the current codebase. When the corresponding stage returns, move its prompt(s)
back to `src/orchestrator/prompts/` alongside the loader that reads them.

## Reviving a stage

1. Move the spec back to `specs/` and the prompt(s) back to
   `src/orchestrator/prompts/`.
2. Re-add the worker module to `packaging/workers.spec` `hiddenimports` and its
   dependencies (the ML/network deps were removed from the bundle — see the spec).
3. Re-add the relevant `ASSEMBLYAI_API_KEY` / `ANTHROPIC_API_KEY` wiring (the
   Settings UI already accepts them).
4. Update `specs/overview.md`, `spec.md`, and `CLAUDE.md` to move the stage from
   "deferred" back to "active".

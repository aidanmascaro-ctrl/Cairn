# Cairn Stage 2 — Auto-Cut Harness (experimental, walled-off)

> A separate experimental track. **This is NOT the shipped Cairn plugin.**

## What this is

A live Claude-Code harness that drives DaVinci Resolve (via the `mcp__davinci-resolve__*` MCP)
to **transcribe footage, select soundbites from a brief, and assemble an A-roll + B-roll rough
cut**. Shipped Cairn deliberately *identifies but never assembles*; this track is the sandbox
where that boundary is pushed. See [`docs/design.md`](docs/design.md) for the full design.

## Hard boundary (do not cross)

Everything for this experiment lives **inside `cairn-stage-2/`**. This track must never:

- edit `../src/` (the Electron plugin), `../spec.md`, or `../IMPLEMENTATION_PLAN.md`
- add features to the shipped product's scope (the April-2026 4-step scope reset still stands)
- write plugin code, manifest entries, or installers

Its own docs, helper scripts (`helpers/`), implementation plan, and sidecar cache (`.cache/`,
git-ignored) all stay within this directory.

## Why "Stage 2"

Conceptually the *next stage* of the Cairn idea — assembly, not just logging. The name is about
project phase, not the product's internal pipeline stages (Ingest / Classify / Color / Timeline).

## Status

Harness built and live (helpers, tests, runbook). Frame-accurate B-roll on V2 **solved** two
ways — FCPXML import (`build_fcpxml.py`, v1b) and native placement via the patched
davinci-resolve-mcp (v2, preferred). **Not yet done:** the v2 native path is documented but not
wired into harness code, and not re-verified live since the MCP patch.

See [`docs/HANDOFF.md`](docs/HANDOFF.md) to resume, then [`docs/runbook.md`](docs/runbook.md).

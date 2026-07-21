# Stage 2 — Session Handoff / Resume Note

**Last touched:** 2026-06-07 (paused for a computer restart)
**Branch:** `main` — working tree clean, everything below is committed.

This is the "pick it up later" note. Read this first, then `runbook.md` for detail.

---

## Where we are

The transcribe → select → assemble harness exists (`helpers/`, `tests/`, `runbook.md`). The
open problem this track was pushing on — **frame-accurate B-roll overlays on V2** (not just
markers) — is now **solved two different ways**, both confirmed working as of 2026-06-03:

| Path | What it is | Status | Where |
| --- | --- | --- | --- |
| **v1a** | Timeline markers, B-roll hand-placed by the editor | works | runbook (markers section) |
| **v1b** | Generate multi-track FCPXML, import into Resolve | **confirmed** | `build_fcpxml.py` + runbook "v1b CONFIRMED" |
| **v2** | Native V2 placement via the patched davinci-resolve-mcp | **confirmed, preferred** | runbook "v2 SOLVED" |

Both breakthroughs are written up in `runbook.md`. The two commits that captured this:
`51a5128` (feat: FCPXML generator) and `a9e9ff8` (docs: v1b + v2 paths).

### The two key unlocks (don't re-derive these)

1. **v1b gotcha:** `media_pool.import_timeline(path, {"importSourceClips": false})` — *without*
   `importSourceClips:false` the import fails with a bare "Failed to import timeline" because
   Resolve tries to re-import the source clips. With it false, Resolve relinks to clips already
   in the media pool. That flag was the entire blocker. Also: `timeline.export` returns
   `success:false` for every type on this MCP — **import works, export does not.**
2. **v2 unlock:** the rich `AppendToTimeline(clipInfo{trackIndex, recordFrame})` always existed
   in Resolve's own API; the **MCP wrapper** just didn't expose it. The connected MCP
   (`samuelgursky/davinci-resolve-mcp` v2.1.0 at `~/Documents/davinci-resolve-mcp/`) is now
   **patched** so `append_to_timeline` accepts `clip_infos: [{clip_id, startFrame, endFrame,
   mediaType (1=video/2=audio), trackIndex, recordFrame}]`, and `add_track(video)` no longer
   passes the empty `sub_type` Resolve rejected. See that repo's `PATCH_NOTES.md`.

---

## What is NOT done yet (this is the resume work)

1. **The v2 native path is documented but not wired into the harness code.** `build_fcpxml.py`
   only implements v1b (FCPXML). The preferred v2 flow — build the A-roll spine on V1 with the
   simple append, `add_track(video)` to make V2, then per matched B-roll
   `append_to_timeline(clip_infos=[{clip_id, startFrame, endFrame, mediaType:1, trackIndex:2,
   recordFrame:<beat>}])` — still lives only in prose in the runbook. Next session: implement it
   as a thin helper in `helpers/` and drive it from the runbook orchestration.
2. **v2 has not been re-verified end-to-end since the MCP patch.** Confirm a real assembly lands
   V1 + A1 + V2 frame-accurate on a fresh test timeline before trusting the harness.

---

## Before you can resume the live work (post-restart checklist)

- [ ] **External media drive mounted:** test footage lives on `/Volumes/Crucial 4TB/260502
      sample open day/01_VIDEOS` (paths hardcoded in `build_fcpxml.py`). Live tests need it.
- [ ] **Patched MCP is the one Resolve connects to:** patches in `~/Documents/davinci-resolve-mcp/`
      load on MCP/session restart — a computer restart reloads them, but verify with
      `resolve_get_version` and a probe `append_to_timeline(clip_infos=...)` call before trusting it.
- [ ] **External Scripting still enabled** in Resolve (confirmed enabled on this machine — if the
      MCP can't connect, that's the first thing to check).
- [ ] **DaVinci Resolve Studio running** with a test project open. Build/verify test timelines
      under a disposable timeline; never touch a source timeline (Safety Invariant 2).

## Suggested first move next session

1. Re-read this note + the runbook "v2 SOLVED" section.
2. Mount the drive, restart-verify the patched MCP exposes `clip_infos`.
3. Write a `helpers/assemble_v2.py` (or extend existing helper) for the native V1-spine + V2-overlay
   flow; keep `build_fcpxml.py` as the v1b fallback.
4. Dry-run on a test timeline, confirm V1 + A1 + V2 frame-accurate, then promote into the runbook.

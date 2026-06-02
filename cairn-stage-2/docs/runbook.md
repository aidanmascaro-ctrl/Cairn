# Cairn Stage 2 — Live Orchestration Runbook

> The live procedure Claude follows in a session to drive the harness. Section 1 records the
> Task-1 MCP capability spike; the end-to-end orchestration (Task 9) is appended below it.

## MCP assembly findings (Task 1, 2026-06-02)

Probed live against **DaVinci Resolve Studio 21.0** via `mcp__davinci-resolve__*` on a throwaway
`_cairn_test` timeline (created, tested, deleted; project state fully restored afterward).

### What works ✅

- **Marked sub-clip append.** `media_pool_item.set_mark_in_out(clip_id, mark_in, mark_out, type="all")`
  followed by `media_pool.append_to_timeline(clip_ids=[clip_id])` appends **only the marked range**.
  - Test: marks 50→100 produced a timeline item of **duration 51 frames** (endpoints inclusive),
    not the full 2.7-min clip.
  - **Implication:** `mark_out` is **inclusive**. To place a soundbite of exactly `N` frames
    starting at source frame `f`, use `mark_in=f`, `mark_out=f+N-1`. This matches
    `helpers/assemble.py` (`mark_out = seconds_to_frames(out) - 1`). ✔ no change needed.
- **A-roll spine assembly** is therefore fully achievable via MCP: create timeline → for each
  approved soundbite, `set_mark_in_out` then `append_to_timeline`. Clips land sequentially on V1.

### What does NOT work ❌ (decisive limitation)

- **No track / record-frame targeting.** `append_to_timeline` accepts only `clip_ids`; two
  successive appends landed sequentially on **V1** (0→51, then 51→112). There is no way to tell
  it "put this clip on V2 at record frame X."
- **`add_track(video)` returned `success:false`** — programmatic video-track creation via this
  MCP is unreliable.
- **Conclusion:** frame-accurate **B-roll overlay on V2 is not achievable through `append_to_timeline`
  alone.** The richer Resolve `AppendToTimeline(clipInfo{trackIndex, recordFrame})` form is not
  exposed by this MCP wrapper.

### Chosen B-roll strategy

- **v1a (default, ship this):** build the **A-roll spine on V1 via MCP append** (works perfectly),
  then place a **colour-coded timeline marker** at each record frame where matched B-roll should
  overlay. Marker note carries the B-roll clip name + `[LITERAL]`/`[THEMATIC]`. The operator drags
  B-roll onto V2 at the markers — fast, full creative control, and consistent with Cairn's
  "markers, not forced cuts" instinct for the overlay layer.
- **v1b (stretch, later):** generate a multi-track **FCPXML** (A-roll on V1, B-roll on V2 at exact
  record frames) and import it via `media_pool.import_timeline`. FCPXML supports per-track record
  positions, bypassing the append limitation entirely. Deferred to a follow-up iteration.

### Effect on the plan

- `helpers/assemble.py` (A-roll spine planner) is correct as specified — no change.
- Task 9 orchestration uses **v1a**: spine via append + B-roll markers. FCPXML (v1b) is out of
  scope for the first working loop.

---

<!-- Task 9 appends the end-to-end orchestration sections below this line. -->

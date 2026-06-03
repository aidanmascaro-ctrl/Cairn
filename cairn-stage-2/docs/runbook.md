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

## End-to-end live orchestration (Task 9)

> The session procedure Claude follows to drive the full pipeline. Every step names the exact
> MCP action (`tool.action(params)`) and helper function. The MCP is the older
> `mcp__davinci-resolve__*` server. All Resolve calls are serialised through it — never concurrent.
> Source media and the operator's existing timeline are read-only throughout; the only write is a
> brand-new timeline. Sidecar cache lives in `cairn-stage-2/.cache/` (git-ignored).

### 0. Preconditions

1. Resolve Studio open on the **Edit** page; verify with `resolve_control.get_version` (or
   `timeline.get_current` returning a name).
2. The clip the operator wants to cut from is the **current timeline** (e.g. "6"). Confirm with
   `timeline.get_current()` → record `name`, `id`, `start_frame`, `start_timecode`.
3. Footage drive mounted (the timeline's clip `File Path`s must resolve on disk).
4. Local tooling: `python3 -c "import faster_whisper"` exits 0 and `ffmpeg -version` succeeds.
5. `cairn-stage-2/.cache/` exists (helpers create it on first write; no action needed).

### 1. Read the timeline → clip manifest

1. `timeline.get_current()` → the source timeline handle.
2. `timeline.get_track_count("video")` → `N` video tracks.
3. For each video track index `1..N`: `timeline.get_items("video", index)` → list of timeline items
   (record their `item_index` positions on the track).
4. For each timeline item (addressed by `track_type="video"`, `track_index`, `item_index`):
   - `timeline_item.get_media_pool_item(track_type="video", track_index, item_index)` →
     `{name, id}` (this **media pool item id** is the `clip_id` used for marking/appending later).
   - `media_pool_item.get_clip_property(clip_id)` → read `File Path`, `FPS`, `Resolution`,
     `Duration`.
5. Build the manifest: `[{clip_id, name, file_path, fps, resolution, duration}]`. De-dup by
   `clip_id` (one media pool item may appear on the timeline more than once).
6. Cache each clip's manifest row: `helpers.cache.write(CACHE_ROOT, clip_id, {"manifest": row,
   "source_sig": helpers.cache.source_signature(file_path)})`.

`CACHE_ROOT = cairn-stage-2/.cache`.

### 2. Classify A-roll vs B-roll

1. For each manifest clip, check freshness first:
   `rec = helpers.cache.read(CACHE_ROOT, clip_id)`; if
   `helpers.cache.is_fresh(rec, file_path)` and `rec` already has a `"label"`, reuse it.
2. Otherwise `helpers.classify.analyze_clip(file_path)` →
   `{duration, speech_ratio, label}` where `label ∈ {A-ROLL, B-ROLL, AMBIGUOUS}`.
3. `helpers.cache.write(CACHE_ROOT, clip_id, {"label": label, "speech_ratio": ratio,
   "source_sig": helpers.cache.source_signature(file_path)})`.
4. Present the A/B/AMBIGUOUS tally to the operator.
5. **Checkpoint — operator confirms every AMBIGUOUS clip** as A-roll or B-roll before continuing.
   Never silently decide (Taste Invariant 7). Persist the confirmed label to cache.

### 3. Transcribe A-roll

1. `model = helpers.transcribe.load_model("base")` once (lazy faster-whisper import; bump to
   `"small"`/`"medium"` only if accuracy on accented/noisy audio is poor — cost is time, not money).
2. For each **A-ROLL** clip not already fresh-with-transcript in cache:
   `rec = helpers.transcribe.transcribe_file(file_path, model)` →
   `{words:[{text,start,end}], segments:[...]}` (single-speaker assumption — no diarization).
3. `helpers.cache.write(CACHE_ROOT, clip_id, {"transcript": rec,
   "source_sig": helpers.cache.source_signature(file_path)})`.
4. Report cost = wall-clock seconds only (zero API spend).

### 4. Collect the brief

Record the deliverable spec to `cairn-stage-2/.cache/brief.json`:
`{platform, aspect, target_duration_sec, angle, tone}` (e.g.
`{"platform":"reels","aspect":"9:16","target_duration_sec":45,"angle":"...","tone":"..."}`).
This drives selection (§5) and B-roll matching (§6).

### 5. Select soundbites ★ APPROVAL GATE (blocking)

1. Read the cached transcripts for all A-roll clips and `brief.json`.
2. Claude reasons over the transcript words/segments against the brief and proposes an **ordered
   radio edit**. For each pick show:
   - source clip **name** and `clip_id`,
   - `in` / `out` as **timecode** (`helpers.timecode.frames_to_timecode`, or seconds — keep the raw
     `src_in_sec`/`src_out_sec` too, they feed §8),
   - the exact transcript **text** spanned,
   - the **beat** it serves and **why** it earns a place.
3. **Present this ordered list and STOP.** Do **not** create a timeline, mark, or append anything
   until the operator approves. This is the single mandatory human checkpoint (design §3, §8).
   On request, revise the selection and re-present — still blocking.
4. On approval, freeze the list as the ordered `picks` for §8.

### 6. Tag + match B-roll

1. For each **B-ROLL** clip: `helpers.frames.extract_frames(file_path, out_dir, count=…)` →
   representative JPEGs (frames dir is git-ignored).
2. Claude views the JPEGs via the **Read** tool and assigns tags: subjects, action, shot type, mood.
   `helpers.cache.write(CACHE_ROOT, clip_id, {"tags": [...]})`.
3. For each approved soundbite, match B-roll:
   - `[LITERAL]` — the visual references the spoken words.
   - `[THEMATIC]` — the visual serves the beat without literal reference.
   Persist per-soundbite matches: `{soundbite_index, broll_clip_id, kind, why}` to cache.

### 7. Assemble (v1a — A-roll spine via append + B-roll markers)

Build a **new** timeline; never touch the source timeline (Safety Invariant 2).

1. Build `Soundbite` objects from the approved `picks`:
   `helpers.assemble.Soundbite(clip_id, src_in_sec, src_out_sec, fps)` (fps from the manifest).
2. `plan = helpers.assemble.assembly_plan(soundbites)` → ordered list of
   `{set_mark:{clip_id, mark_in, mark_out, type:"all"}, append:{clip_id}}`. (`mark_out` is the
   **inclusive** last frame — see Task 1 findings above.)
3. Create the destination timeline:
   `media_pool.create_timeline("CAIRN_AUTOCUT_<brief-slug>_<n>")` → new empty timeline; it becomes
   current. (If a name collides, increment `<n>`.)
4. For each plan entry **in order**:
   - `media_pool_item.set_mark_in_out(clip_id, mark_in, mark_out, type="all")`
   - `media_pool.append_to_timeline(clip_ids=[clip_id])`
   Each append lands the marked range sequentially on **V1** — this is the A-roll spine.
5. **B-roll overlay (markers, not forced cuts):** Task 1 proved `append_to_timeline` cannot target
   V2 or a record frame. So after the spine exists, for each soundbite that has a B-roll match,
   compute its record frame on the new timeline (cumulative sum of preceding clip lengths in frames)
   and drop a **colour-coded timeline marker** there (the new timeline is current, so
   `timeline_markers` targets it):
   `timeline_markers.add(frame=<record_frame>, color="Cyan", name=<broll name>,
   note="[LITERAL|THEMATIC] <broll_clip_id>", duration=1, custom_data=<broll_clip_id>)`.
   `duration` is required (1 frame is fine); `custom_data` lets you look the marker back up by
   B-roll id. The operator drags the named B-roll onto V2 at each marker — fast, full creative
   control.
   - *(v1b, deferred):* generate a multi-track FCPXML (A-roll V1 + B-roll V2 at exact record frames)
     and `media_pool.import_timeline(path)` instead. Out of scope for the first working loop.

### 8. Verify

1. `timeline.get_current()` → confirm it is the new `CAIRN_AUTOCUT_*` timeline (not the source).
2. `timeline.get_items("video", 1)` → assert the item **count** equals `len(plan)`.
3. For each item, check its duration in frames equals `mark_out - mark_in + 1` from the plan
   (endpoints inclusive). Report any mismatch instead of silently accepting it.
4. `timeline_markers.get_all()` → confirm one marker per matched B-roll soundbite.

### 9. Refine / render (optional)

1. The operator trims on the Edit page and slides the marked B-roll onto V2.
2. Optional draft export via the MCP render pipeline: `render.add_job(preset, target_dir)` then
   `render.start()`. The render writes to the operator's chosen output dir — never over source media.

## v1b CONFIRMED — frame-accurate B-roll on V2 via FCPXML import (2026-06-03)

The Task-1 limitation (append can't target V2/record-frame) is fully bypassed by generating a
multi-track FCPXML and importing it. **This now works and is the preferred assembly path when the
deliverable needs real B-roll overlays, not just markers.**

- **Generator:** `build_fcpxml.py` — A-roll = primary-storyline `<asset-clip>` (video +
  `audioRole="dialogue"`); B-roll = video-only `<video>` elements with `lane="1"` (→ V2), nested in
  the A-roll clip that contains each one's record frame. Connected-clip offset is in the **parent
  clip's source timebase**: `offset = parent.src_start + (broll_tl_pos − parent.tl_offset)`. Using
  `<video>` (not `<asset-clip>`) for B-roll keeps it video-only — no drone/ambient audio under the
  dialogue.
- **THE UNLOCK:** `media_pool.import_timeline(path, options={"importSourceClips": false})`. Without
  `importSourceClips:false` the import **fails with a bare "Failed to import timeline"** — Resolve
  tries to re-import the media-rep source clips and chokes. With it false, Resolve relinks to clips
  already in the media pool (matched by `media-rep src` path) and the import succeeds. This was the
  entire blocker; the FCPXML dialect itself was fine.
- **FCPXML dialect that imports cleanly into Resolve 21:** `version="1.9"`, `<!DOCTYPE fcpxml>`,
  `<format>` with `frameDuration`/`width`/`height` (a non-standard `name=` is best omitted),
  `<library><event><project><sequence><spine>`, rational times as `<frames>/25s`, portrait
  `width="2160" height="3840"`. `timeline.export` (FCPXML/DRT/EDL) returned `success:false` for every
  type tried — **import works, export does not** on this MCP.
- **Result:** one import yields V1 (A-roll spine) + A1 (synced dialogue) + V2 (8 B-roll overlays),
  all frame-accurate. Markers (v1a) remain the fallback when you'd rather hand-place B-roll.

### Reversibility

The entire assembly is one new timeline. Deleting it
(`media_pool.delete_timelines([new_timeline_id])`) fully undoes the run. Source clips, the source
timeline, and the sidecar cache are untouched.

## v2 SOLVED — native V2 placement via patched MCP (2026-06-03)

The whole FCPXML detour above was only necessary because the connected MCP
(**samuelgursky/davinci-resolve-mcp v2.1.0**, at `~/Documents/davinci-resolve-mcp/`) wrapped the
**simple** `AppendToTimeline(clips)` — V1-only, no track/frame targeting — and its `add_track`
passed an empty `sub_type` that Resolve rejects (so V2 couldn't even be created). Both are now
**patched** (see that repo's `PATCH_NOTES.md`):

- `media_pool.append_to_timeline` now accepts `clip_infos: [{clip_id, startFrame, endFrame,
  mediaType (1=video/2=audio), trackIndex, recordFrame}]` → **frame-accurate B-roll on V2**, audio
  kept off the overlay via `mediaType:1`.
- `timeline.add_track(video)` now works (omits the empty sub_type).

**New assembly path (preferred over FCPXML):** build the A-roll spine on V1 with the simple
append (as before), `add_track(video)` to make V2, then for each matched B-roll
`append_to_timeline(clip_infos=[{clip_id, startFrame, endFrame, mediaType:1, trackIndex:2,
recordFrame:<beat>}])`. No FCPXML, no offline-relink pain, all online. Patches load on MCP/session
restart. The diagnostic chain that found this: the rich `AppendToTimeline(clipInfo{trackIndex,
recordFrame})` exists in Resolve's API; it was the **MCP wrapper** that didn't expose it — a thin
wrapper limit, not a Resolve limit.


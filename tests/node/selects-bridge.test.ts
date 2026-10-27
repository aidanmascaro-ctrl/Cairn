// Stage 5 — Selects over the real Python bridge: presets, a full analyse →
// rescore → rate cycle on a generated clip, and error rejection.

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { PythonBridge, type SelectsProgressMessage } from "../../src/orchestrator/python-bridge.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCRIPT = join(__dirname, "../../src/workers/bridge.py");
const PYTHON = process.env["CAIRN_PYTHON"] ?? "python3";
const FFMPEG = join(__dirname, "../../assets/bin/ffmpeg");

describe("selects over the Python bridge", () => {
  let bridge: PythonBridge;
  let shoot: string;
  let clip: string;

  beforeAll(async () => {
    bridge = new PythonBridge({ pythonPath: PYTHON, scriptPath: SCRIPT, callTimeoutMs: 60_000 });
    await bridge.start();
    shoot = mkdtempSync(join(tmpdir(), "cairn-selects-"));
    const cam = join(shoot, "01_VIDEOS", "A_CAM");
    mkdirSync(cam, { recursive: true });
    clip = join(cam, "C0001.mp4");
    if (existsSync(FFMPEG)) {
      execFileSync(FFMPEG, [
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1280x720:rate=25:duration=4",
        "-pix_fmt",
        "yuv420p",
        clip,
      ]);
    }
  }, 30_000);

  afterAll(async () => {
    await bridge.stop();
    rmSync(shoot, { recursive: true, force: true });
  });

  it("lists built-in presets with All-Rounder first", async () => {
    const res = await bridge.call({ type: "selects_presets" });
    expect(res.type).toBe("selects_presets_result");
    if (res.type === "selects_presets_result") {
      expect(res.presets[0]?.id).toBe("all-rounder");
      expect(res.presets.length).toBeGreaterThanOrEqual(9);
    }
  });

  it("rejects a missing shoot folder instead of resolving", async () => {
    await expect(
      bridge.call({ type: "selects_rescore", payload: { path: "/definitely/not/here" } }),
    ).rejects.toThrow(/does not exist/);
  });

  it("refuses an unsupported sampling rate", async () => {
    await expect(
      bridge.stream(
        { type: "selects_analyse", payload: { path: shoot, sampling_rate: 3 } },
        () => {},
      ),
    ).rejects.toThrow(/sampling_rate/);
  });

  it.skipIf(!existsSync(FFMPEG))(
    "analyses, re-scores and stores a rating",
    async () => {
      const events: SelectsProgressMessage[] = [];
      const done = await bridge.stream(
        { type: "selects_analyse", payload: { path: shoot, sampling_rate: 4 } },
        (e) => {
          if (e.type === "selects_progress") events.push(e);
        },
      );
      expect(done.type).toBe("selects_analyse_complete");
      if (done.type === "selects_analyse_complete") {
        expect(done).toMatchObject({ analysed: 1, failed: 0, total: 1 });
      }
      expect(events.map((e) => e.event)).toContain("clip_analysed");

      const scored = await bridge.call({
        type: "selects_rescore",
        payload: { path: shoot, settings: { score_threshold_lo: 0, min_segment_sec: 0.5 } },
      });
      expect(scored.type).toBe("selects_rescore_result");
      if (scored.type !== "selects_rescore_result") return;
      const first = scored.clips[0];
      expect(first?.analysed).toBe(true);
      expect(first?.scores.length).toBeGreaterThan(10);
      expect(scored.summary.select_count).toBeGreaterThanOrEqual(1);

      const rated = await bridge.call({
        type: "selects_rate",
        payload: {
          path: shoot,
          clip_path: clip,
          start_sec: 0,
          end_sec: 2,
          rating: "keep",
          source: "select",
        },
      });
      expect(rated.type).toBe("selects_rate_result");
      if (rated.type === "selects_rate_result") {
        expect(Object.keys(rated.ratings)).toEqual(["0.00-2.00"]);
      }

      const context = await bridge.call({
        type: "selects_learn_context",
        payload: { path: shoot },
      });
      expect(context.type).toBe("selects_learn_context_result");
      if (context.type === "selects_learn_context_result") {
        expect(context.ready).toBe(false);
        expect(context.examples).toHaveLength(1);
        expect(JSON.stringify(context.examples)).not.toContain("C0001");
      }
    },
    60_000,
  );
});

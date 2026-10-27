// Stage 5 — taste learning loop, driven with a fake Claude client and a fake
// Python bridge so no API calls are made and no money is spent.

import { describe, it, expect, beforeAll } from "vitest";
import { createRequire } from "node:module";

interface Evaluation {
  total: number;
  correct: number;
  accuracy: number;
  keeps_kept: number;
  keeps_total: number;
  rejects_dropped: number;
  rejects_total: number;
  details: { id: number; rating: string; predicted: string; coverage: number; correct: boolean }[];
}

interface LearnModule {
  MODEL: string;
  learnTaste: (deps: unknown, payload: { path: string }) => Promise<Record<string, unknown>>;
  buildPrompt: (ctx: unknown, template?: string) => string;
  buildFeedback: (
    evaluation: Evaluation,
    examples: { id: number; clip: string; start_sec: number }[],
  ) => string;
  mergeSettings: (
    current: Record<string, unknown>,
    proposed: Record<string, unknown>,
    id: string,
  ) => Record<string, unknown>;
  renderPrompt: (template: string, values: Record<string, unknown>) => string;
  friendlyError: (err: unknown, sdk: unknown) => string;
  loadSdk: () => new (opts: { apiKey: string }) => unknown;
  _setFormatter: (fn: (schema: unknown) => unknown) => void;
}

const require = createRequire(import.meta.url);
const learn = require("../../src/orchestrator/selects-learn.js") as LearnModule;

const SETTINGS = {
  preset_id: "all-rounder",
  group_weights: { focus: 0.6, exposure: 0.2, motion: 0.2 },
  sub_weights: { sharpness: 0.5, camera_stillness: 0.4 },
  targets: { luma: 0.45, contrast: 0.4 },
  flags: { clipping: "exclude", shaky: "neutral" },
  camera_filter: { enabled: true, static_only: true },
  score_threshold_lo: 0.8,
};

function evaluation(correct: number, total = 6): Evaluation {
  return {
    total,
    correct,
    accuracy: correct / total,
    keeps_kept: correct,
    keeps_total: total,
    rejects_dropped: 0,
    rejects_total: 0,
    details: Array.from({ length: total }, (_, i) => ({
      id: i + 1,
      rating: "keep",
      predicted: i < correct ? "keep" : "reject",
      coverage: i < correct ? 1 : 0,
      correct: i < correct,
    })),
  };
}

const CONTEXT = {
  type: "selects_learn_context_result",
  ready: true,
  reason: null,
  examples: Array.from({ length: 6 }, (_, i) => ({
    id: i + 1,
    clip: `clip ${i + 1}`,
    start_sec: i,
    rating: "keep",
  })),
  settings: SETTINGS,
  baseline: evaluation(3),
  presets: [],
};

function proposal(threshold: number) {
  return {
    preset_name: "Loose",
    rationale: "You keep softer handheld moments.",
    changes: [`threshold to ${threshold}`],
    settings: {
      ...SETTINGS,
      score_threshold_lo: threshold,
      flags: { ...SETTINGS.flags, shaky: "favour" },
    },
  };
}

function fakeClient(responses: unknown[]) {
  const calls: { messages: { role: string; content: unknown }[]; [k: string]: unknown }[] = [];
  return {
    calls,
    beta: {
      messages: {
        parse: (params: (typeof calls)[number]) => {
          calls.push({ ...params, messages: [...params.messages] });
          const next = responses.shift();
          if (next instanceof Error) return Promise.reject(next);
          return Promise.resolve(next);
        },
      },
    },
  };
}

function reply(parsed: unknown, stop_reason = "end_turn") {
  return {
    stop_reason,
    parsed_output: parsed,
    content: [{ type: "text", text: JSON.stringify(parsed) }],
  };
}

describe("selects taste learning", () => {
  beforeAll(() => {
    learn._setFormatter((schema) => ({ type: "json_schema", schema }));
  });

  it("stops at round one when the proposal beats the baseline", async () => {
    const client = fakeClient([reply(proposal(0.7))]);
    const evaluations = [evaluation(5)];
    const python = (msg: { type: string; payload: { settings?: Record<string, unknown> } }) => {
      if (msg.type === "selects_learn_context") return Promise.resolve(CONTEXT);
      expect(msg.payload.settings?.["score_threshold_lo"]).toBe(0.7);
      return Promise.resolve(evaluations.shift());
    };
    const out = await learn.learnTaste({ python, client }, { path: "/shoot" });
    expect(out).toMatchObject({ ok: true, rounds: 1, improved: true, model: "claude-opus-5-5" });
    expect((out["after"] as Evaluation).correct).toBe(5);
    expect(out["after"]).not.toHaveProperty("details");
    const call = client.calls[0];
    expect(call).toMatchObject({
      model: "claude-opus-5-5",
      fallbacks: "default",
      betas: ["server-side-fallback-2026-07-01"],
      thinking: { type: "adaptive" },
      output_config: { effort: "high" },
    });
  });

  it("feeds the misses back for a second round and keeps the better proposal", async () => {
    const client = fakeClient([reply(proposal(0.75)), reply(proposal(0.6))]);
    const evaluations = [evaluation(3), evaluation(6)];
    const python = (msg: { type: string }) =>
      Promise.resolve(msg.type === "selects_learn_context" ? CONTEXT : evaluations.shift());
    const out = await learn.learnTaste({ python, client }, { path: "/shoot" });
    expect(out).toMatchObject({ ok: true, rounds: 2, improved: true });
    const second = client.calls[1]?.messages ?? [];
    expect(second.map((m) => m.role)).toEqual(["user", "assistant", "user"]);
    expect(String(second[2]?.content)).toContain("3 of 6");
    expect(
      (out["proposal"] as { settings: { score_threshold_lo: number } }).settings.score_threshold_lo,
    ).toBe(0.6);
  });

  it("keeps fields Claude does not own, like the camera filter", async () => {
    const client = fakeClient([reply(proposal(0.7))]);
    const python = (msg: { type: string }) =>
      Promise.resolve(msg.type === "selects_learn_context" ? CONTEXT : evaluation(6));
    const out = await learn.learnTaste({ python, client }, { path: "/shoot" });
    const settings = (out["proposal"] as { settings: Record<string, unknown> }).settings;
    expect(settings["camera_filter"]).toEqual(SETTINGS.camera_filter);
    expect(settings["preset_id"]).toBe("learned");
  });

  it("keeps the first proposal when the retry fails", async () => {
    const client = fakeClient([reply(proposal(0.75)), new Error("socket hang up")]);
    const evaluations = [evaluation(3)];
    const python = (msg: { type: string }) =>
      Promise.resolve(msg.type === "selects_learn_context" ? CONTEXT : evaluations.shift());
    const out = await learn.learnTaste({ python, client }, { path: "/shoot" });
    expect(out["ok"]).toBe(true);
    expect(String(out["warning"])).toMatch(/second attempt failed/);
  });

  it("reports the model that actually answered", async () => {
    const client = fakeClient([{ ...reply(proposal(0.7)), model: "claude-opus-4-8" }]);
    const python = (msg: { type: string }) =>
      Promise.resolve(msg.type === "selects_learn_context" ? CONTEXT : evaluation(6));
    const out = await learn.learnTaste({ python, client }, { path: "/shoot" });
    expect(out["model"]).toBe("claude-opus-4-8");
  });

  it("does not call Claude until there are enough ratings", async () => {
    const client = fakeClient([]);
    const python = () => Promise.resolve({ ...CONTEXT, ready: false, reason: "Rate at least 6" });
    const out = await learn.learnTaste({ python, client }, { path: "/shoot" });
    expect(out).toEqual({ ok: false, error: "Rate at least 6" });
    expect(client.calls).toHaveLength(0);
  });

  it.each([
    ["refusal", { stop_reason: "refusal", parsed_output: null, content: [] }, /declined/],
    ["truncation", { stop_reason: "max_tokens", parsed_output: null, content: [] }, /cut off/],
    ["schema miss", { stop_reason: "end_turn", parsed_output: null, content: [] }, /format/],
  ])("reports a %s cleanly", async (_label, response, pattern) => {
    const client = fakeClient([response]);
    const python = () => Promise.resolve(CONTEXT);
    const out = await learn.learnTaste({ python, client }, { path: "/shoot" });
    expect(out["ok"]).toBe(false);
    expect(String(out["error"])).toMatch(pattern);
  });

  it("maps SDK errors to plain English", async () => {
    const Anthropic = learn.loadSdk() as unknown as {
      AuthenticationError: { prototype: Error };
      APIConnectionError: new (opts: { message: string }) => Error;
    };
    const auth = Object.create(Anthropic.AuthenticationError.prototype) as Error;
    expect(learn.friendlyError(auth, Anthropic)).toMatch(/key was rejected/);
    const offline = new Anthropic.APIConnectionError({ message: "down" });
    expect(learn.friendlyError(offline, Anthropic)).toMatch(/internet/);
    expect(learn.friendlyError(new Error("boom"), Anthropic)).toBe("boom");

    const client = fakeClient([offline]);
    const python = () => Promise.resolve(CONTEXT);
    const out = await learn.learnTaste({ python, client, Anthropic }, { path: "/shoot" });
    expect(out).toEqual({
      ok: false,
      error: "Couldn't reach Claude. Check the internet connection.",
    });
  });

  it("renders every placeholder in the real prompt and refuses a missing one", () => {
    const text = learn.buildPrompt(CONTEXT);
    expect(text).not.toMatch(/\{\{/);
    expect(text).toContain("3 of 6");
    expect(text).not.toMatch(/^---/);
    expect(() => learn.renderPrompt("{{NOPE}}", {})).toThrow(/NOPE/);
  });

  it("feedback lists each wrong example", () => {
    const text = learn.buildFeedback(evaluation(4), CONTEXT.examples);
    expect(text).toContain("example 5 (clip 5 at 4s)");
    expect(text).toContain("example 6");
    expect(text).not.toContain("example 1 ");
  });
});

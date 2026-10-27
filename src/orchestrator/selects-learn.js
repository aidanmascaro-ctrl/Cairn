// Stage 5 — Selects: learn the editor's taste with Claude.
//
// Flow (one IPC call from the renderer):
//   1. Python builds the learning context: anonymised numeric summaries of every
//      rated range + current settings + how many ratings those settings get right.
//      No media and no file names leave the machine.
//   2. Claude proposes complete settings (structured output, schema-checked).
//   3. Python replays the proposal over the ratings — a deterministic check, not
//      a second opinion. If it is no better than the current settings, Claude
//      gets the misses back and one more attempt (the loop-back rule).
//   4. The best proposal is returned for the editor to accept or ignore.
//
// Everything Claude- or Python-specific is injected so this module is testable.

"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { z } = require("zod");

const MODEL = "claude-opus-5-5";
const MAX_ROUNDS = 2;
const MAX_TOKENS = 16000;
const PROMPT_PATH = path.join(__dirname, "prompts", "selects_learn.md");

const Tier = z.enum(["exclude", "disfavour", "neutral", "favour", "include"]);

const ProposalSchema = z.object({
  preset_name: z.string(),
  rationale: z.string(),
  changes: z.array(z.string()),
  settings: z.object({
    group_weights: z.object({ focus: z.number(), exposure: z.number(), motion: z.number() }),
    sub_weights: z.object({
      sharpness: z.number(),
      detail: z.number(),
      focus_stability: z.number(),
      headroom: z.number(),
      flicker: z.number(),
      luma_target: z.number(),
      contrast_target: z.number(),
      camera_stillness: z.number(),
      subject_stillness: z.number(),
      motion_smoothness: z.number(),
    }),
    targets: z.object({ luma: z.number(), contrast: z.number() }),
    score_threshold_lo: z.number(),
    min_segment_sec: z.number(),
    max_segment_sec: z.number().nullable(),
    max_segments_per_clip: z.number().int().nullable(),
    dip_sec: z.number(),
    flags: z.object({
      clipping: Tier,
      duplicate: Tier,
      solid_color: Tier,
      hard_cut: Tier,
      motion_blur: Tier,
      shaky: Tier,
      low_light: Tier,
    }),
  }),
});

/** Strip front matter and fill {{PLACEHOLDERS}}. */
function renderPrompt(template, values) {
  const body = template.replace(/^---[\s\S]*?---\s*/, "");
  return body.replace(/\{\{([A-Z_]+)\}\}/g, (_m, key) => {
    if (!(key in values)) throw new Error(`selects_learn prompt: missing value for ${key}`);
    return String(values[key]);
  });
}

function buildPrompt(context, template = fs.readFileSync(PROMPT_PATH, "utf8")) {
  return renderPrompt(template, {
    EXAMPLES_JSON: JSON.stringify(context.examples, null, 1),
    SETTINGS_JSON: JSON.stringify(context.settings, null, 1),
    BASELINE_CORRECT: context.baseline.correct,
    BASELINE_TOTAL: context.baseline.total,
  });
}

/** Feedback for the second round: which ratings the proposal still gets wrong. */
function buildFeedback(evaluation, examples) {
  const byId = new Map(examples.map((e) => [e.id, e]));
  const misses = evaluation.details
    .filter((d) => !d.correct)
    .map((d) => {
      const ex = byId.get(d.id);
      const where = ex ? `${ex.clip} at ${ex.start_sec}s` : `example ${d.id}`;
      return `- example ${d.id} (${where}): editor said ${d.rating}, your settings ${
        d.predicted === "keep" ? "select" : "drop"
      } it (${Math.round(d.coverage * 100)}% selected)`;
    });
  return [
    `Replaying your settings over the ratings gets ${evaluation.correct} of ${evaluation.total} right, ` +
      `which is no better than the current settings. These are still wrong:`,
    ...misses,
    "",
    "Revise the settings. Return the full object again.",
  ].join("\n");
}

/** Merge Claude's settings onto the current ones so fields it does not own (camera filter etc.) survive. */
function mergeSettings(current, proposed, presetId) {
  return {
    ...current,
    ...proposed,
    group_weights: { ...current.group_weights, ...proposed.group_weights },
    sub_weights: { ...current.sub_weights, ...proposed.sub_weights },
    targets: { ...current.targets, ...proposed.targets },
    flags: { ...current.flags, ...proposed.flags },
    preset_id: presetId,
  };
}

function friendlyError(err, Anthropic) {
  // Most specific first: APIConnectionError and the status errors all extend APIError.
  const is = (name) => typeof Anthropic?.[name] === "function" && err instanceof Anthropic[name];
  if (is("AuthenticationError"))
    return "Your Anthropic API key was rejected. Check it in Settings.";
  if (is("PermissionDeniedError")) return `This Anthropic API key isn't allowed to use ${MODEL}.`;
  if (is("RateLimitError")) return "Claude is rate-limiting this key. Try again in a minute.";
  if (is("APIConnectionError")) return "Couldn't reach Claude. Check the internet connection.";
  if (is("APIError")) {
    const status = Number(err.status) || 0;
    return status >= 500
      ? `Claude is having trouble right now (HTTP ${status}). Try again shortly.`
      : `Claude rejected the request (HTTP ${status}): ${err.message}`;
  }
  return err instanceof Error ? err.message : String(err);
}

/**
 * @param {object} deps
 * @param {(msg: object) => Promise<any>} deps.python  bridge call returning the parsed result
 * @param {any} deps.client                             Anthropic client (or a fake with beta.messages.parse)
 * @param {any} [deps.Anthropic]                        SDK namespace, for typed error mapping
 * @param {object} payload  { path, settings? }
 */
async function learnTaste({ python, client, Anthropic }, payload) {
  const context = await python({ type: "selects_learn_context", payload });
  if (!context.ready) return { ok: false, error: context.reason };

  const messages = [{ role: "user", content: buildPrompt(context) }];
  let best = null;
  let rounds = 0;

  try {
    while (rounds < MAX_ROUNDS) {
      rounds += 1;
      const response = await client.beta.messages.parse({
        model: MODEL,
        max_tokens: MAX_TOKENS,
        betas: ["server-side-fallback-2026-07-01"],
        fallbacks: "default",
        thinking: { type: "adaptive" },
        output_config: { effort: "high", format: betaZodOutputFormat(ProposalSchema) },
        messages,
      });
      if (response.stop_reason === "refusal") {
        return { ok: false, error: "Claude declined this request." };
      }
      if (response.stop_reason === "max_tokens" || !response.parsed_output) {
        return {
          ok: false,
          error: "Claude's answer was cut off or didn't match the settings format.",
        };
      }
      const proposal = response.parsed_output;
      // "learned", not the old preset id: no preset chip should claim these values.
      const settings = mergeSettings(context.settings, proposal.settings, "learned");
      const evaluation = await python({
        type: "selects_evaluate",
        payload: { path: payload.path, settings },
      });
      if (!best || evaluation.correct > best.after.correct) {
        // With server-side fallbacks another model may have answered; report the real one.
        best = {
          proposal: { ...proposal, settings },
          after: evaluation,
          model: response.model ?? MODEL,
        };
      }
      if (evaluation.correct > context.baseline.correct || rounds === MAX_ROUNDS) break;
      // Append the full assistant content (thinking blocks included) — never edit history.
      messages.push({ role: "assistant", content: response.content });
      messages.push({ role: "user", content: buildFeedback(evaluation, context.examples) });
    }
  } catch (err) {
    // A failure on the retry shouldn't throw away a good first proposal.
    if (!best) return { ok: false, error: friendlyError(err, Anthropic) };
    best = { ...best, warning: `Claude's second attempt failed: ${friendlyError(err, Anthropic)}` };
  }

  return {
    ok: true,
    model: best.model,
    rounds,
    ...(best.warning ? { warning: best.warning } : {}),
    proposal: best.proposal,
    before: stripDetails(context.baseline),
    after: stripDetails(best.after),
    improved: best.after.correct > context.baseline.correct,
    example_count: context.examples.length,
  };
}

function stripDetails(evaluation) {
  return Object.fromEntries(Object.entries(evaluation).filter(([key]) => key !== "details"));
}

/** Lazily require the SDK so the plugin still loads (and other stages work) if it is missing. */
function loadSdk() {
  const Anthropic = require("@anthropic-ai/sdk");
  return Anthropic.default ?? Anthropic;
}

let betaZodOutputFormat = (schema) =>
  require("@anthropic-ai/sdk/helpers/beta/zod").betaZodOutputFormat(schema);

module.exports = {
  MODEL,
  ProposalSchema,
  learnTaste,
  buildPrompt,
  buildFeedback,
  mergeSettings,
  renderPrompt,
  friendlyError,
  loadSdk,
  /** test seam */
  _setFormatter(fn) {
    betaZodOutputFormat = fn;
  },
};

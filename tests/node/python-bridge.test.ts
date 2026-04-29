import { describe, it, expect, beforeAll, afterAll } from "vitest";
import {
  PythonBridge,
  type OutboundCall,
  type ClassifyProgressEvent,
} from "../../src/orchestrator/python-bridge.js";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCRIPT = join(__dirname, "../../src/workers/bridge.py");

// Prefer the confirmed Python path; fall back to python3 on PATH.
const PYTHON = process.env["CAIRN_PYTHON"] ?? "python3";

describe("PythonBridge", () => {
  let bridge: PythonBridge;

  beforeAll(async () => {
    bridge = new PythonBridge({ pythonPath: PYTHON, scriptPath: SCRIPT });
    await bridge.start();
  }, 15_000);

  afterAll(async () => {
    await bridge.stop();
  });

  it("is running after start()", () => {
    expect(bridge.isRunning).toBe(true);
  });

  it("ping → pong round-trip returns python_version and timestamp", async () => {
    const response = await bridge.call({ type: "ping" });
    expect(response.type).toBe("pong");
    if (response.type === "pong") {
      expect(response.python_version).toMatch(/^\d+\.\d+\.\d+$/);
      expect(response.timestamp_iso).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    }
  });

  it("echo round-trip returns payload unchanged with timestamp", async () => {
    const response = await bridge.call({ type: "echo", payload: "hello cairn" });
    expect(response.type).toBe("echo_response");
    if (response.type === "echo_response") {
      expect(response.payload).toBe("hello cairn");
      expect(response.timestamp_iso).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    }
  });

  it("concurrent calls all resolve correctly", async () => {
    const payloads = ["alpha", "beta", "gamma", "delta", "epsilon"];
    const responses = await Promise.all(
      payloads.map((p) => bridge.call({ type: "echo", payload: p })),
    );
    for (const [i, response] of responses.entries()) {
      expect(response.type).toBe("echo_response");
      if (response.type === "echo_response") {
        expect(response.payload).toBe(payloads[i]);
      }
    }
  });

  it("unknown message type is rejected by Zod schema before reaching Python", async () => {
    // The OutboundMessageSchema discriminated union rejects unknown types at the
    // Node boundary — correct behaviour; nothing unsafe reaches the worker.
    await expect(
      bridge.call({ type: "unknown_type_xyz" } as unknown as OutboundCall),
    ).rejects.toThrow();
  });
});

// ── Error-rejection tests (isolated bridge using error-fixture script) ────────

const ERROR_SCRIPT = join(__dirname, "fixtures/error-bridge.py");

describe("PythonBridge — type:error rejection", () => {
  let errorBridge: PythonBridge;

  beforeAll(async () => {
    errorBridge = new PythonBridge({
      pythonPath: PYTHON,
      scriptPath: ERROR_SCRIPT,
      callTimeoutMs: 5_000,
    });
    await errorBridge.start();
  }, 10_000);

  afterAll(async () => {
    await errorBridge.stop();
  });

  it("call() rejects when Python sends type:error", async () => {
    await expect(errorBridge.call({ type: "echo", payload: "test" })).rejects.toThrow(
      "simulated error for type=echo",
    );
  });

  it("stream() rejects mid-stream when Python sends type:error", async () => {
    const progressEvents: ClassifyProgressEvent[] = [];
    await expect(
      errorBridge.stream({ type: "classify", payload: { path: "/fake" } }, (ev) => {
        if (ev.type === "classify_progress") progressEvents.push(ev);
      }),
    ).rejects.toThrow("simulated worker crash");
    // Progress events emitted before the error must have reached the callback
    expect(progressEvents).toHaveLength(1);
    expect(progressEvents[0]?.event).toBe("classify_started");
  });

  it("stream() error does not resolve (promise never settles as resolved)", async () => {
    // Ensure the rejection carries an Error instance, not a resolved value
    const result = await errorBridge
      .stream({ type: "classify", payload: { path: "/fake" } }, () => {})
      .then(
        () => "resolved",
        (err: unknown) => (err instanceof Error ? "rejected-error" : "rejected-other"),
      );
    expect(result).toBe("rejected-error");
  });
});

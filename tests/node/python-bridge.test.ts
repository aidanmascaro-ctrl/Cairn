import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { PythonBridge, type OutboundCall } from "../../src/orchestrator/python-bridge.js";
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

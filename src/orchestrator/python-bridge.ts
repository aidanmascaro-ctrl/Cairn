// Node↔Python bridge
//
// Spawns a single long-lived Python worker process and communicates with it
// via JSON-per-line on stdin/stdout. Each message is one JSON object followed
// by a newline. Responses are matched back to callers by `id`.
//
// Usage:
//   const bridge = new PythonBridge();
//   await bridge.start();
//   const result = await bridge.send({ type: "echo", payload: "hello" });
//   await bridge.stop();

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface } from "node:readline";
import { randomUUID } from "node:crypto";
import { z } from "zod";

// ── Message schemas ──────────────────────────────────────────────────────────

export const OutboundMessageSchema = z.discriminatedUnion("type", [
  z.object({
    id: z.string(),
    type: z.literal("echo"),
    payload: z.string(),
  }),
  z.object({
    id: z.string(),
    type: z.literal("ping"),
  }),
]);

export const InboundMessageSchema = z.discriminatedUnion("type", [
  z.object({
    id: z.string(),
    type: z.literal("echo_response"),
    payload: z.string(),
    timestamp_iso: z.string(),
  }),
  z.object({
    id: z.string(),
    type: z.literal("pong"),
    python_version: z.string(),
    timestamp_iso: z.string(),
  }),
  z.object({
    id: z.string(),
    type: z.literal("error"),
    message: z.string(),
  }),
]);

export type OutboundMessage = z.infer<typeof OutboundMessageSchema>;
export type InboundMessage = z.infer<typeof InboundMessageSchema>;

// Caller-facing type: OutboundMessage without the `id` field (bridge adds it).
export type OutboundCall = { type: "echo"; payload: string } | { type: "ping" };

// ── Bridge ───────────────────────────────────────────────────────────────────

type PendingCall = {
  resolve: (value: InboundMessage) => void;
  reject: (reason: Error) => void;
  timeoutHandle: ReturnType<typeof setTimeout>;
};

export class PythonBridge {
  private proc: ChildProcessWithoutNullStreams | null = null;
  private pending = new Map<string, PendingCall>();
  private callTimeoutMs: number;
  private pythonPath: string;
  private scriptPath: string;

  constructor({
    pythonPath = "python3",
    scriptPath = new URL("../../src/workers/bridge.py", import.meta.url).pathname,
    callTimeoutMs = 10_000,
  }: {
    pythonPath?: string;
    scriptPath?: string;
    callTimeoutMs?: number;
  } = {}) {
    this.pythonPath = pythonPath;
    this.scriptPath = scriptPath;
    this.callTimeoutMs = callTimeoutMs;
  }

  get isRunning(): boolean {
    return this.proc !== null && this.proc.exitCode === null;
  }

  async start(): Promise<void> {
    if (this.isRunning) return;

    const proc = spawn(this.pythonPath, [this.scriptPath], {
      stdio: ["pipe", "pipe", "pipe"],
    });
    this.proc = proc;

    const rl = createInterface({ input: proc.stdout });
    rl.on("line", (line) => {
      this.handleLine(line.trim());
    });

    proc.stderr.on("data", (chunk: Buffer) => {
      process.stderr.write(`[python-bridge] ${chunk.toString()}`);
    });

    proc.on("exit", (code) => {
      this.proc = null;
      for (const [, pending] of this.pending) {
        clearTimeout(pending.timeoutHandle);
        pending.reject(new Error(`Python worker exited with code ${String(code)}`));
      }
      this.pending.clear();
    });

    await this.call({ type: "ping" });
  }

  async stop(): Promise<void> {
    const proc = this.proc;
    if (!this.isRunning || proc === null) return;
    proc.stdin.end();
    await new Promise<void>((resolve) => {
      proc.once("exit", () => {
        resolve();
      });
      setTimeout(() => {
        proc.kill();
        resolve();
      }, 2_000);
    });
    this.proc = null;
  }

  /** Send a message and await the response. */
  async call(msg: OutboundCall): Promise<InboundMessage> {
    const proc = this.proc;
    if (!this.isRunning || proc === null) {
      throw new Error("PythonBridge is not running. Call start() first.");
    }

    const id = randomUUID();
    const outbound = OutboundMessageSchema.parse({ ...msg, id });

    return new Promise<InboundMessage>((resolve, reject) => {
      const timeoutHandle = setTimeout(() => {
        this.pending.delete(id);
        reject(
          new Error(
            `Python bridge call timed out after ${String(this.callTimeoutMs)}ms (type=${outbound.type}, id=${id})`,
          ),
        );
      }, this.callTimeoutMs);

      this.pending.set(id, { resolve, reject, timeoutHandle });
      proc.stdin.write(JSON.stringify(outbound) + "\n");
    });
  }

  private handleLine(line: string): void {
    if (!line) return;

    let parsed: unknown;
    try {
      parsed = JSON.parse(line);
    } catch {
      process.stderr.write(`[python-bridge] Non-JSON line from worker: ${line}\n`);
      return;
    }

    const result = InboundMessageSchema.safeParse(parsed);
    if (!result.success) {
      process.stderr.write(`[python-bridge] Unexpected message shape: ${line}\n`);
      return;
    }

    const msg = result.data;
    const pending = this.pending.get(msg.id);
    if (!pending) {
      process.stderr.write(`[python-bridge] No pending call for id=${msg.id}\n`);
      return;
    }

    clearTimeout(pending.timeoutHandle);
    this.pending.delete(msg.id);
    pending.resolve(msg);
  }
}

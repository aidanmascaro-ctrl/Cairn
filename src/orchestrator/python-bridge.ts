// Node↔Python bridge
//
// Spawns a single long-lived Python worker process and communicates with it
// via JSON-per-line on stdin/stdout. Each message is one JSON object followed
// by a newline. Responses are matched back to callers by `id`.
//
// Two call modes:
//   call()   — one request → one final response
//   stream() — one request → N progress events + one final response

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface } from "node:readline";
import { randomUUID } from "node:crypto";
import { z } from "zod";

// ── Message schemas ──────────────────────────────────────────────────────────

export const OutboundMessageSchema = z.discriminatedUnion("type", [
  z.object({ id: z.string(), type: z.literal("echo"), payload: z.string() }),
  z.object({ id: z.string(), type: z.literal("ping") }),
  z.object({
    id: z.string(),
    type: z.literal("ingest"),
    payload: z.object({
      path: z.string(),
      name: z.string().optional(),
      dry_run: z.boolean().optional(),
      write_sidecar: z.boolean().optional(),
      resolve: z.boolean().optional(),
    }),
  }),
]);

const ClipPathsSchema = z.object({
  a_cam: z.array(z.string()),
  photos: z.array(z.string()),
  assets: z.array(z.string()).optional(),
});

const MetadataSchema = z.object({
  shoot_date: z.string(),
  camera: z.string(),
  lens: z.string(),
  timecode_start: z.string(),
});

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
  // Ingest progress — intermediate; does not resolve the call
  z.object({
    id: z.string(),
    type: z.literal("ingest_progress"),
    event: z.string(),
    // Allow arbitrary extra fields for event-specific data
    src: z.string().optional(),
    dst: z.string().optional(),
    count: z.number().optional(),
    reason: z.string().optional(),
    dry_run: z.boolean().optional(),
  }),
  // Ingest complete — final; resolves the call
  z.object({
    id: z.string(),
    type: z.literal("ingest_complete"),
    folder: z.string(),
    files_moved: z.number(),
    shells_removed: z.number(),
    metadata: MetadataSchema,
    clip_paths: ClipPathsSchema,
    resolve_counts: z.record(z.string(), z.number()),
    dry_run: z.boolean(),
    timestamp_iso: z.string(),
  }),
]);

export type OutboundMessage = z.infer<typeof OutboundMessageSchema>;
export type InboundMessage = z.infer<typeof InboundMessageSchema>;
export type IngestProgressEvent = Extract<InboundMessage, { type: "ingest_progress" }>;
export type IngestCompleteEvent = Extract<InboundMessage, { type: "ingest_complete" }>;

// Caller-facing type: OutboundMessage without the `id` field (bridge adds it).
export type OutboundCall =
  | { type: "echo"; payload: string }
  | { type: "ping" }
  | {
      type: "ingest";
      payload: {
        path: string;
        name?: string;
        dry_run?: boolean;
        write_sidecar?: boolean;
        resolve?: boolean;
      };
    };

// ── Bridge ───────────────────────────────────────────────────────────────────

type PendingCall = {
  resolve: (value: InboundMessage) => void;
  reject: (reason: Error) => void;
  timeoutHandle: ReturnType<typeof setTimeout>;
  onProgress?: (event: IngestProgressEvent) => void;
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

  /** Send a message and await the single final response. */
  async call(msg: OutboundCall): Promise<InboundMessage> {
    return this._send(msg);
  }

  /**
   * Send a message, calling onProgress for each intermediate event,
   * and resolving with the final response.
   */
  async stream(
    msg: OutboundCall,
    onProgress: (event: IngestProgressEvent) => void,
  ): Promise<InboundMessage> {
    return this._send(msg, onProgress);
  }

  private _send(
    msg: OutboundCall,
    onProgress?: (event: IngestProgressEvent) => void,
  ): Promise<InboundMessage> {
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

      const entry: PendingCall = { resolve, reject, timeoutHandle };
      if (onProgress !== undefined) entry.onProgress = onProgress;
      this.pending.set(id, entry);
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

    // Progress events — call callback, do not resolve the promise yet
    if (msg.type === "ingest_progress") {
      pending.onProgress?.(msg);
      return;
    }

    clearTimeout(pending.timeoutHandle);
    this.pending.delete(msg.id);
    pending.resolve(msg);
  }
}

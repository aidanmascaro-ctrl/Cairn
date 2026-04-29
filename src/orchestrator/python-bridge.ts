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

const ClassifyLabelSchema = z.enum(["A-ROLL", "B-ROLL", "AMBIGUOUS", "UNKNOWN"]);

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
  z.object({
    id: z.string(),
    type: z.literal("classify"),
    payload: z.object({
      path: z.string(),
      dry_run: z.boolean().optional(),
    }),
  }),
  z.object({
    id: z.string(),
    type: z.literal("classify_confirm"),
    payload: z.object({
      clip_path: z.string(),
      label: ClassifyLabelSchema,
      shoot_folder: z.string(),
    }),
  }),
  z.object({
    id: z.string(),
    type: z.literal("build_timeline"),
    payload: z.object({
      path: z.string(),
      name: z.string().optional(),
    }),
  }),
]);

const ClipPathsSchema = z.object({
  a_cam: z.array(z.string()),
  c_cam: z.array(z.string()),
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
    src: z.string().optional(),
    dst: z.string().optional(),
    count: z.number().optional(),
    reason: z.string().optional(),
    dry_run: z.boolean().optional(),
    total: z.number().optional(),
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
  // Classify progress — intermediate; does not resolve the call
  z.object({
    id: z.string(),
    type: z.literal("classify_progress"),
    event: z.enum([
      "classify_started",
      "clip_classified",
      "clip_skipped",
      "clip_error",
      "classify_complete",
    ]),
    file: z.string().optional(),
    label: ClassifyLabelSchema.optional(),
    duration_sec: z.number().optional(),
    face_ratio: z.number().optional(),
    motion_score: z.number().optional(),
    resolve_ok: z.boolean().nullable().optional(),
    sidecar_ok: z.boolean().nullable().optional(),
    reason: z.string().optional(),
    total_clips: z.number().optional(),
    a_roll: z.number().optional(),
    b_roll: z.number().optional(),
    ambiguous: z.number().optional(),
    skipped: z.number().optional(),
    total: z.number().optional(),
  }),
  // Classify complete — final; resolves the call
  z.object({
    id: z.string(),
    type: z.literal("classify_complete"),
    event: z.string(),
    a_roll: z.number(),
    b_roll: z.number(),
    ambiguous: z.number(),
    skipped: z.number(),
    total: z.number(),
  }),
  // Classify confirm ok — final; resolves the call
  z.object({
    id: z.string(),
    type: z.literal("classify_confirm_ok"),
    clip_path: z.string(),
    label: ClassifyLabelSchema,
    resolve_ok: z.boolean(),
  }),
  // Build timeline result — final; resolves the call
  z.object({
    id: z.string(),
    type: z.literal("build_timeline_result"),
    ok: z.boolean(),
    name: z.string().nullable(),
    fps: z.number().nullable(),
    resolution: z.string().nullable(),
    a_count: z.number(),
    b_count: z.number(),
    ambiguous_count: z.number(),
    missing_paths: z.array(z.string()),
    warnings: z.array(z.string()),
    error: z.string().nullable(),
  }),
]);

export type OutboundMessage = z.infer<typeof OutboundMessageSchema>;
export type InboundMessage = z.infer<typeof InboundMessageSchema>;
export type IngestProgressEvent = Extract<InboundMessage, { type: "ingest_progress" }>;
export type IngestCompleteEvent = Extract<InboundMessage, { type: "ingest_complete" }>;
export type ClassifyProgressEvent = Extract<InboundMessage, { type: "classify_progress" }>;
export type ClassifyCompleteEvent = Extract<InboundMessage, { type: "classify_complete" }>;
export type BuildTimelineResultEvent = Extract<InboundMessage, { type: "build_timeline_result" }>;

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
    }
  | {
      type: "classify";
      payload: {
        path: string;
        dry_run?: boolean;
      };
    }
  | {
      type: "classify_confirm";
      payload: {
        clip_path: string;
        label: z.infer<typeof ClassifyLabelSchema>;
        shoot_folder: string;
      };
    }
  | {
      type: "build_timeline";
      payload: {
        path: string;
        name?: string;
      };
    };

// ── Bridge ───────────────────────────────────────────────────────────────────

type ProgressEvent = IngestProgressEvent | ClassifyProgressEvent;

type PendingCall = {
  resolve: (value: InboundMessage) => void;
  reject: (reason: Error) => void;
  timeoutHandle: ReturnType<typeof setTimeout>;
  onProgress?: (event: ProgressEvent) => void;
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
    onProgress: (event: ProgressEvent) => void,
  ): Promise<InboundMessage> {
    return this._send(msg, onProgress);
  }

  private _send(
    msg: OutboundCall,
    onProgress?: (event: ProgressEvent) => void,
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
    if (msg.type.endsWith("_progress")) {
      pending.onProgress?.(msg as ProgressEvent);
      return;
    }

    clearTimeout(pending.timeoutHandle);
    this.pending.delete(msg.id);

    // Error events — reject the promise so callers catch rather than silently receive an error object
    if (msg.type === "error") {
      pending.reject(new Error(msg.message));
      return;
    }

    pending.resolve(msg);
  }
}

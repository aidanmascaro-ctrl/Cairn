// Shape of the `window.cairn` bridge exposed by src/orchestrator/preload.js.
// Keep in sync with preload.js and the handlers registered in main.js.
//
// IPC contract — each interface maps to a Python handler in src/workers/bridge.py:
//   IngestPayload / IngestResult        → handle_ingest()   (streaming)
//   ClassifyPayload / ClassifyResult    → handle_classify() (streaming)
//   ClassifyConfirmPayload / Result     → handle_classify_confirm()
//   BuildTimelinePayload / Result       → handle_build_timeline()
//
// Zod schemas mirroring these types live in src/orchestrator/python-bridge.ts.

export interface ResolveSnapshot {
  available: boolean;
  projectName: string | null;
  timelineName: string | null;
  error?: string;
}

export interface PingResult {
  ok: true;
  pluginId: string;
  electron: string;
  node: string;
  chrome: string;
}

export interface EchoResult {
  id: string;
  type: "echo_response";
  payload: string;
  timestamp_iso: string;
}

export interface StoredKeys {
  anthropic: string;
  assemblyai: string;
}

export interface SetKeysPayload {
  anthropic?: string;
  assemblyai?: string;
}

export interface KeyValidateParams {
  type: "anthropic" | "assemblyai";
  key: string;
}

export interface KeyValidateResult {
  valid: boolean;
  error?: string;
}

export interface KeysApi {
  get(): Promise<StoredKeys>;
  set(payload: SetKeysPayload): Promise<{ ok: true }>;
  validate(params: KeyValidateParams): Promise<KeyValidateResult>;
}

export interface IngestPayload {
  path: string;
  name?: string;
  dry_run?: boolean;
  write_sidecar?: boolean;
  resolve?: boolean;
}

export interface IngestMetadata {
  shoot_date: string;
  camera: string;
  lens: string;
  timecode_start: string;
}

export interface IngestClipPaths {
  a_cam: string[];
  c_cam: string[];
  photos: string[];
  assets?: string[];
}

export interface IngestProgressEvent {
  event: string;
  src?: string;
  dst?: string;
  count?: number;
  reason?: string;
  dry_run?: boolean;
  total?: number;
}

export interface IngestResult {
  folder: string;
  files_moved: number;
  /** Count of files that hit OSError during shutil.move — 0 on a clean run. */
  move_failures?: number;
  shells_removed: number;
  metadata: IngestMetadata;
  clip_paths: IngestClipPaths;
  resolve_counts: Record<string, number>;
  dry_run: boolean;
  timestamp_iso: string;
}

export interface IngestApi {
  run(payload: IngestPayload): Promise<IngestResult>;
  onProgress(callback: (event: IngestProgressEvent) => void): () => void;
}

export type ClassifyLabel = "A-ROLL" | "B-ROLL" | "AMBIGUOUS" | "UNKNOWN";

export interface ClassifyPayload {
  path: string;
  dry_run?: boolean;
}

export interface ClassifyProgressEvent {
  event:
    | "classify_started"
    | "clip_classified"
    | "clip_skipped"
    | "clip_error"
    | "classify_complete";
  file?: string;
  label?: ClassifyLabel;
  duration_sec?: number;
  face_ratio?: number;
  motion_score?: number;
  resolve_ok?: boolean | null;
  sidecar_ok?: boolean | null;
  reason?: string;
  total_clips?: number;
  // summary fields (classify_complete)
  a_roll?: number;
  b_roll?: number;
  ambiguous?: number;
  skipped?: number;
  total?: number;
}

export interface ClassifyResult {
  a_roll: number;
  b_roll: number;
  ambiguous: number;
  skipped: number;
  total: number;
}

export interface ThumbnailResult {
  dataUrl: string | null;
}

export interface ClassifyConfirmPayload {
  clip_path: string;
  label: ClassifyLabel;
  shoot_folder: string;
}

export interface ClassifyConfirmResult {
  type: "classify_confirm_ok";
  clip_path: string;
  label: ClassifyLabel;
  resolve_ok: boolean;
  resolve_reason: string | null;
}

export interface ClassifyInspectFrame {
  frame_idx: number;
  timestamp_sec: number;
  face_count: number;
  motion_diff: number;
  data_url: string;
}

export interface ClassifyInspectResult {
  ok: boolean;
  duration_sec: number;
  face_ratio: number;
  motion_score: number;
  knn_label: ClassifyLabel | null;
  knn_confidence: number | null;
  frames: ClassifyInspectFrame[];
  error: string | null;
}

export interface ClassifyInspectPayload {
  clip_path: string;
}

export interface ClassifyApi {
  run(payload: ClassifyPayload): Promise<ClassifyResult>;
  onProgress(callback: (event: ClassifyProgressEvent) => void): () => void;
  thumbnail(clipPath: string): Promise<ThumbnailResult>;
  confirm(payload: ClassifyConfirmPayload): Promise<ClassifyConfirmResult>;
  inspect(payload: ClassifyInspectPayload): Promise<ClassifyInspectResult>;
}

export interface BuildTimelinePayload {
  path: string;
  name?: string;
}

export interface BuildTimelineResult {
  ok: boolean;
  name: string | null;
  fps: number | null;
  resolution: string | null;
  a_count: number;
  b_count: number;
  ambiguous_count: number;
  missing_paths: string[];
  warnings: string[];
  error: string | null;
}

export interface TimelineApi {
  build(payload: BuildTimelinePayload): Promise<BuildTimelineResult>;
}

export interface HealthResult {
  workflowIntegration: boolean;
  workflowError: string | null;
  scriptingOk: boolean;
  scriptingReason: string | null;
}

export interface BridgeCrashEvent {
  code: number | null;
  signal: string | null;
  stderrTail: string;
}

export interface CairnBridge {
  openFolder(): Promise<string | null>;
  /**
   * Resolve a File from a drop event to its absolute filesystem path.
   * Required in sandboxed renderers where the non-standard File.path is hidden.
   * Returns "" if the path can't be resolved.
   */
  getPathForFile(file: File): string;
  getResolveSnapshot(): Promise<ResolveSnapshot>;
  health(): Promise<HealthResult>;
  onBridgeCrash(callback: (event: BridgeCrashEvent) => void): () => void;
  ping(): Promise<PingResult>;
  echo(payload: string): Promise<EchoResult>;
  ingest: IngestApi;
  classify: ClassifyApi;
  timeline: TimelineApi;
  keys: KeysApi;
}

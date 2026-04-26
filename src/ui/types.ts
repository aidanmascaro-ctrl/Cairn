// Shape of the `window.cairn` bridge exposed by src/orchestrator/preload.js.
// Keep in sync with preload.js and the handlers registered in main.js.

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
  event: "classify_started" | "clip_classified" | "clip_skipped" | "classify_complete";
  file?: string;
  label?: ClassifyLabel;
  duration_sec?: number;
  face_ratio?: number;
  motion_score?: number;
  resolve_ok?: boolean | null;
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
}

export interface ClassifyApi {
  run(payload: ClassifyPayload): Promise<ClassifyResult>;
  onProgress(callback: (event: ClassifyProgressEvent) => void): () => void;
  thumbnail(clipPath: string): Promise<ThumbnailResult>;
  confirm(payload: ClassifyConfirmPayload): Promise<ClassifyConfirmResult>;
}

export interface CairnBridge {
  openFolder(): Promise<string | null>;
  getResolveSnapshot(): Promise<ResolveSnapshot>;
  ping(): Promise<PingResult>;
  echo(payload: string): Promise<EchoResult>;
  ingest: IngestApi;
  classify: ClassifyApi;
  keys: KeysApi;
}

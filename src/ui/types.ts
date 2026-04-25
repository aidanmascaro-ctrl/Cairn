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

export interface CairnBridge {
  getResolveSnapshot(): Promise<ResolveSnapshot>;
  ping(): Promise<PingResult>;
  echo(payload: string): Promise<EchoResult>;
  ingest: IngestApi;
  keys: KeysApi;
}

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
  // resolve_flat_import fields
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
  speech_ratio?: number;
  audio_stream?: boolean;
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

export interface TranscribeEstimateResult {
  type: "transcribe_estimate_result";
  clip_count: number;
  estimated_minutes: number;
  estimated_cost_usd: number;
}

export interface TranscribePayload {
  path: string;
  cloud_allowed?: boolean;
  dry_run?: boolean;
}

export interface TranscribeProgressEvent {
  event:
    | "transcribe_started"
    | "clip_transcribed"
    | "clip_skipped"
    | "clip_error"
    | "transcribe_complete";
  file?: string;
  provider?: string;
  utterance_count?: number;
  word_count?: number;
  srt_path?: string;
  speaker_count?: number;
  reason?: string;
  resolve_ok?: boolean | null;
  total_clips?: number;
  // transcribe_complete summary fields
  total?: number;
  transcribed?: number;
  skipped?: number;
  errors?: number;
}

export interface TranscribeResult {
  total: number;
  transcribed: number;
  skipped: number;
  errors: number;
}

export interface SpeakerLabelPayload {
  shoot_folder: string;
  speaker_labels: Record<string, string>;
}

export interface SpeakerLabelResult {
  type: "speaker_labels_updated";
  updated_count: number;
}

export interface TranscribeApi {
  estimate(payload: { path: string }): Promise<TranscribeEstimateResult>;
  run(payload: TranscribePayload): Promise<TranscribeResult>;
  onProgress(callback: (event: TranscribeProgressEvent) => void): () => void;
  labelSpeakers(payload: SpeakerLabelPayload): Promise<SpeakerLabelResult>;
}

// ── Brief types ────────────────────────────────────────────────────────────────

export interface BriefSubject {
  name?: string;
  profession?: string;
  location?: string;
  one_liner?: string;
}

export interface BriefDeliverable {
  platform: string;
  target_duration_sec?: number[];
  aspect_ratio?: string;
  tone?: string;
}

export interface BriefPrivacy {
  cloud_transcription_allowed: boolean;
  cloud_vision_allowed: boolean;
}

export interface BriefDraft {
  schema_version?: number;
  subject?: BriefSubject;
  deliverables?: BriefDeliverable[];
  narrative_angle?: string;
  tone?: string;
  themes?: string[];
  word_boost_terms?: string[];
  privacy?: BriefPrivacy;
  notes?: string;
  conversation_log?: Array<{ role: "user" | "assistant"; content: string }>;
  finalised?: boolean;
}

export interface BriefFields {
  filled: number;
  total: number;
  required_filled: number;
  required_total: number;
}

export interface BriefJudgeResult {
  overall_score: number;
  invented_fields: string[];
  missing_fields: string[];
  notes?: string;
}

export interface BriefMessageResult {
  reply: string;
  draft: BriefDraft;
  finalised: boolean;
  fields: BriefFields;
  judge?: BriefJudgeResult | null;
}

export interface BriefGetResult {
  brief: BriefDraft | null;
  fields: BriefFields;
}

export interface BriefApi {
  get(payload: { path: string }): Promise<BriefGetResult>;
  message(payload: { path: string; message: string }): Promise<BriefMessageResult>;
  save(payload: { path: string; updates: Partial<BriefDraft> }): Promise<{
    brief: BriefDraft;
    fields: BriefFields;
  }>;
  reset(payload: { path: string }): Promise<{ ok: boolean }>;
}

export interface VisionEstimateResult {
  type: "vision_estimate_result";
  clip_count: number;
  estimated_cost_usd: number;
}

export interface VisionTags {
  subjects: string[];
  actions: string[];
  shot_type: string;
  mood: string;
  environment: string;
  colour_palette: string[];
  time_of_day: string;
  lighting_style: string;
  camera_motion: string;
  confidence: number;
}

export interface VisionJudgeResult {
  subjects_score: number;
  actions_score: number;
  shot_type_score: number;
  mood_score: number;
  overall_score: number;
  low_confidence_fields: string[];
  notes?: string;
}

export interface VisionProgressEvent {
  event:
    | "vision_started"
    | "clip_tagged"
    | "clip_skipped"
    | "clip_error"
    | "judge_flagged"
    | "vision_complete";
  file?: string;
  tags?: VisionTags;
  keywords?: string;
  judge?: VisionJudgeResult;
  low_fields?: string[];
  reason?: string;
  resolve_ok?: boolean | null;
  total_clips?: number;
  // vision_complete summary fields
  total?: number;
  tagged?: number;
  skipped?: number;
  errors?: number;
  judge_samples?: number;
}

export interface VisionResult {
  total: number;
  tagged: number;
  skipped: number;
  errors: number;
  judge_samples: number;
}

export interface VisionApi {
  estimate(payload: { path: string }): Promise<VisionEstimateResult>;
  run(payload: { path: string; cloud_allowed?: boolean; dry_run?: boolean }): Promise<VisionResult>;
  onProgress(callback: (event: VisionProgressEvent) => void): () => void;
}

export interface CairnBridge {
  openFolder(): Promise<string | null>;
  getResolveSnapshot(): Promise<ResolveSnapshot>;
  ping(): Promise<PingResult>;
  echo(payload: string): Promise<EchoResult>;
  ingest: IngestApi;
  classify: ClassifyApi;
  transcribe: TranscribeApi;
  vision: VisionApi;
  brief: BriefApi;
  keys: KeysApi;
}

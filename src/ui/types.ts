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
  /** metadata_refused: clip metadata fields Resolve wouldn't store. */
  fields?: string[];
}

export interface IngestResult {
  folder: string;
  files_moved: number;
  /** Count of files that hit OSError during shutil.move — 0 on a clean run. */
  move_failures?: number;
  shells_removed: number;
  metadata: IngestMetadata;
  clip_paths: IngestClipPaths;
  /** "offload" = imported in place from an Offload shoot (Footage/<Camera>). */
  layout?: "organised" | "offload";
  /** Offload shoots only: camera folder name → clip paths (one Resolve bin each). */
  camera_clips?: Record<string, string[]>;
  resolve_counts: Record<string, number>;
  /** Clip metadata fields Resolve refused to store (empty = all written). */
  metadata_refused?: string[];
  dry_run: boolean;
  timestamp_iso: string;
}

/** A shoot the Offload menu-bar app copied off a card and Cairn hasn't sorted yet. */
export interface OffloadShoot {
  shoot_folder: string;
  added_at: string | null;
  /** false when the drive holding the shoot isn't plugged in. */
  available: boolean;
  client: string;
  shoot_name: string;
  camera: string;
  /** yyyy-MM-dd */
  date: string;
  file_count: number;
}

export interface OffloadApi {
  list(): Promise<OffloadShoot[]>;
  done(shootFolder: string): Promise<boolean>;
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

// ── Stage 5: Selects ─────────────────────────────────────────────────────────

export type FlagName =
  | "clipping"
  | "duplicate"
  | "solid_color"
  | "hard_cut"
  | "motion_blur"
  | "shaky"
  | "low_light";
export type FlagTier = "exclude" | "disfavour" | "neutral" | "favour" | "include";
export type SelectGroup = "focus" | "exposure" | "motion";
export type SubMetric =
  | "sharpness"
  | "detail"
  | "focus_stability"
  | "headroom"
  | "flicker"
  | "luma_target"
  | "contrast_target"
  | "camera_stillness"
  | "subject_stillness"
  | "motion_smoothness";
export type CameraAxis = "pan" | "tilt" | "zoom" | "twist";

export interface CameraFilter {
  enabled: boolean;
  static_only: boolean;
  pan: number;
  tilt: number;
  zoom: number;
  twist: number;
  strictness: number;
  mirror: boolean;
}

export interface SelectsSettings {
  preset_id: string;
  sampling_rate: number;
  group_weights: Record<SelectGroup, number>;
  sub_weights: Record<SubMetric, number>;
  targets: { luma: number; contrast: number };
  score_threshold_lo: number;
  score_threshold_hi: number;
  min_segment_sec: number;
  max_segment_sec: number | null;
  max_segments_per_clip: number | null;
  dip_sec: number;
  handles_samples: number;
  flags: Record<FlagName, FlagTier>;
  camera_filter: CameraFilter;
  metrics_enabled: boolean;
  flags_enabled: boolean;
}

export interface SelectsPreset {
  id: string;
  name: string;
  description: string;
  settings: SelectsSettings;
  builtin: boolean;
  rationale?: string;
  accuracy?: number | null;
}

export interface SelectSegment {
  start_sec: number;
  end_sec: number;
  duration_sec: number;
  core_start_sec: number;
  core_end_sec: number;
  mean_score: number;
  peak_score: number;
  peak_sec: number;
  group_scores: Record<SelectGroup, number | null>;
  flags: FlagName[];
  camera: Record<CameraAxis, number> & { confidence: number; class: string };
  forced: boolean;
}

export type SelectRatingValue = "keep" | "reject";

export interface SelectRating {
  rating: SelectRatingValue;
  start_sec: number;
  end_sec: number;
  source: "select" | "near_miss";
  rated_at: string;
}

export interface SelectsClip {
  file: string;
  name: string;
  classification: string | null;
  ratings: Record<string, SelectRating>;
  analysed: boolean;
  duration_sec: number;
  fps?: number;
  sampling_rate?: number;
  scores: number[];
  segments: SelectSegment[];
  near_misses: SelectSegment[];
  sample_count?: number;
  qualifying_count?: number;
}

export interface SelectsSummary {
  clip_count: number;
  analysed_count: number;
  select_count: number;
  selected_sec: number;
  footage_sec: number;
  selected_pct: number;
}

export interface SelectsRescoreResult {
  settings: SelectsSettings;
  clips: SelectsClip[];
  summary: SelectsSummary;
  /** Unreadable clip records or saved tuning, a failed save — shown, never swallowed. */
  warnings?: string[];
}

export interface SelectsProgressEvent {
  event:
    | "analyse_started"
    | "clip_started"
    | "clip_progress"
    | "clip_analysed"
    | "clip_skipped"
    | "clip_error";
  file?: string;
  fraction?: number;
  total_clips?: number;
  samples?: number;
  duration_sec?: number;
  reason?: string;
}

export interface SelectsAnalyseResult {
  analysed: number;
  skipped: number;
  failed: number;
  total: number;
}

export interface SelectsEvaluation {
  total: number;
  correct: number;
  accuracy: number;
  keeps_kept: number;
  keeps_total: number;
  rejects_dropped: number;
  rejects_total: number;
}

export interface SelectsLearnResult {
  ok: boolean;
  error?: string;
  /** Set when a retry failed but an earlier proposal is still being returned. */
  warning?: string;
  model?: string;
  rounds?: number;
  improved?: boolean;
  example_count?: number;
  before?: SelectsEvaluation;
  after?: SelectsEvaluation;
  proposal?: {
    preset_name: string;
    rationale: string;
    changes: string[];
    settings: SelectsSettings;
  };
}

export type SelectsLayout = "context" | "selects_only";
export type SelectsOrder =
  | "timeline"
  | "score_desc"
  | "score_asc"
  | "duration_desc"
  | "duration_asc";

export interface SelectsBuildPayload {
  path: string;
  settings: SelectsSettings;
  layout: SelectsLayout;
  order: SelectsOrder;
  include_markers: boolean;
  clip_markers: boolean;
  name?: string;
}

export interface SelectsBuildResult {
  ok: boolean;
  name: string | null;
  layout: SelectsLayout | null;
  fps: number | null;
  resolution: string | null;
  select_count: number;
  piece_count: number;
  linked_count: number;
  markers_added: number;
  clip_markers_added: number;
  tracks: Record<string, number>;
  missing_paths: string[];
  warnings: string[];
  error: string | null;
}

export interface SelectsApi {
  presets(): Promise<{ presets: SelectsPreset[] }>;
  analyse(payload: {
    path: string;
    sampling_rate: number;
    force?: boolean;
  }): Promise<SelectsAnalyseResult>;
  onProgress(callback: (event: SelectsProgressEvent) => void): () => void;
  rescore(payload: {
    path: string;
    settings?: SelectsSettings;
    preset_id?: string;
  }): Promise<SelectsRescoreResult>;
  rate(payload: {
    path: string;
    clip_path: string;
    start_sec: number;
    end_sec: number;
    rating: SelectRatingValue | null;
    source: "select" | "near_miss";
  }): Promise<{ clip_path: string; ratings: Record<string, SelectRating> }>;
  learn(payload: { path: string; settings: SelectsSettings }): Promise<SelectsLearnResult>;
  savePreset(payload: {
    name: string;
    settings: SelectsSettings;
    description?: string;
    rationale?: string;
    accuracy?: number;
  }): Promise<{ preset: SelectsPreset }>;
  build(payload: SelectsBuildPayload): Promise<SelectsBuildResult>;
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
  offload: OffloadApi;
  classify: ClassifyApi;
  timeline: TimelineApi;
  selects: SelectsApi;
  keys: KeysApi;
}

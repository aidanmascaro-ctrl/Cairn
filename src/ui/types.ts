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

export interface CairnBridge {
  getResolveSnapshot(): Promise<ResolveSnapshot>;
  ping(): Promise<PingResult>;
  echo(payload: string): Promise<EchoResult>;
}

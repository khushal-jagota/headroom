import type { ConversationBackendKey } from '../contracts.ts';
export type BackendUsageOutcome = 'succeeded' | 'unavailable' | 'unauthenticated' | 'failed';
export type BackendUsageWindowKind = 'five_hour' | 'seven_day';
export interface BackendUsageWindow {
  readonly kind: BackendUsageWindowKind;
  readonly used_percent: number;
  readonly resets_at: Date;
  readonly model_scope: string | null;
}
export interface BackendUsageResult {
  readonly backend_key: ConversationBackendKey;
  readonly outcome: BackendUsageOutcome;
  readonly detail: string | null;
  readonly observed_at: Date | null;
  readonly windows: readonly BackendUsageWindow[];
}
export interface CachedBackendUsage {
  readonly observed_at: Date;
  readonly windows: readonly BackendUsageWindow[];
}
export interface BackendUsageAdapter {
  refresh(options?: { signal?: AbortSignal }): Promise<BackendUsageResult>;
}
export interface UsageHttpResponse {
  readonly status_code: number;
  readonly body: unknown;
}
export interface UsageHttpGet {
  (
    url: string,
    headers: Readonly<Record<string, string>>,
    timeout_seconds: number,
    options?: { signal?: AbortSignal },
  ): Promise<UsageHttpResponse>;
}
export interface CodexUsageSource {
  /** Return the raw RPC result JSON. Do not parse and reserialize its numeric tokens. */
  (executable: string, options: { timeout_seconds: number; signal?: AbortSignal }): Promise<string>;
}
/** The Codex unit owns the short-lived app-server child and its cleanup. */
export class CodexUsageTimeout extends Error {}
export class CodexUsageRequestRejected extends Error {
  readonly code: number;
  constructor(code: number, message: string) {
    super(message);
    this.code = code;
  }
}
export class CodexUsageLoginIsDuplicated extends Error {}

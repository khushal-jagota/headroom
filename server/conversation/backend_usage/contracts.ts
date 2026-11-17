import type { BackendInstanceId } from '../../drivers/core/identity.ts';
export type BackendUsageOutcome = 'succeeded' | 'unavailable' | 'unauthenticated' | 'failed';
export type BackendUsageWindowKind = 'five_hour' | 'seven_day';
export interface BackendUsageWindow {
  readonly kind: BackendUsageWindowKind;
  readonly used_percent: number;
  readonly resets_at: Date;
  readonly model_scope: string | null;
  /** A model-family label the backend reports that may match no catalog model. */
  readonly scope_label: string | null;
}
export interface BackendUsageResult {
  readonly backend_key: BackendInstanceId;
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

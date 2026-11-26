import type { BackendInstanceId } from '../../drivers/core/identity.ts';
export type BackendUsageOutcome = 'succeeded' | 'unavailable' | 'unauthenticated' | 'failed';
export type BackendUsageWindowKind = 'five_hour' | 'seven_day';
/** One allowance window as its provider reports it, in T3 Code's `ServerProviderUsageWindow` shape. */
export interface BackendUsageWindow {
  /** Stable and unique within one observation, from the provider's own names: `seven_day_fable`. */
  readonly id: string;
  readonly kind: BackendUsageWindowKind;
  /** What the Limits page shows: `7 days · Fable`. */
  readonly label: string;
  readonly used_percent: number;
  /** Null when the provider names no reset, as for an untouched Antigravity bucket. */
  readonly resets_at: Date | null;
  readonly window_duration_mins: number | null;
  /** The model or model family the provider names for this window. Null: the whole account. */
  readonly model_scope: string | null;
}
export interface BackendUsageResult {
  readonly backend_key: BackendInstanceId;
  readonly outcome: BackendUsageOutcome;
  readonly detail: string | null;
  readonly observed_at: Date | null;
  readonly windows: readonly BackendUsageWindow[];
}
/** The account a machine's backend was signed in to when its allowance was read. */
export interface BackendUsageAccount {
  /** What the backend card named, usually an email. Null when it named none. */
  readonly account_label: string | null;
  /** The card's plan line, such as `max plan via claude.ai`, only for a confirmed sign-in. */
  readonly account_detail: string | null;
}
export interface CachedBackendUsage extends BackendUsageAccount {
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

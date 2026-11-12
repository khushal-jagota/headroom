/** Mechanical copy of the existing T3 wire DTOs and Python-shaped runtime contracts.
 * Copyright (c) 2026 T3 Tools Inc. MIT permission notice: ./T3-LICENSE.
 * Source pin: 75e4ceb96489a2fe92c710c79dbea59778cccfd1.
 * The frontend projection's extensionless imports require bundler resolution.
 */
import type { BackendStateStore } from '../backend_state.ts';
export type NonNegativeInt = number;
export type TrimmedNonEmptyString = string;
export type IsoDateTime = string;
export type UsageProviderKind = 'claude' | 'codex' | 'grok';
export type UsageDay = string & { readonly __brand: 'UsageDay' };
export type UsageResolution = 'day' | 'hour';
export type UsageCostSource = 'providerReported' | 'modelPriced' | 'unpriced';
export interface UsageTokenTotals {
  readonly uncachedInputTokens: NonNegativeInt;
  readonly cachedInputTokens: NonNegativeInt;
  readonly cacheCreationTokens: NonNegativeInt;
  readonly outputTokens: NonNegativeInt;
  readonly reasoningTokens: NonNegativeInt;
}
export interface UsageBucket {
  readonly day: UsageDay;
  readonly hourStart?: TrimmedNonEmptyString;
  readonly provider: UsageProviderKind;
  readonly model: TrimmedNonEmptyString;
  readonly totals: UsageTokenTotals;
  readonly costUsd: number;
  readonly cacheSavingsUsd: number;
  readonly costSource: UsageCostSource;
  readonly records: NonNegativeInt;
  readonly unpricedRecords: NonNegativeInt;
  readonly sessions: NonNegativeInt;
}
export interface UsageSourceFingerprint {
  readonly hostId: TrimmedNonEmptyString;
  readonly provider: UsageProviderKind;
  readonly resolvedHomePath: TrimmedNonEmptyString;
  readonly volumeId: string;
}
export type UsageSourceStatus = 'ok' | 'missing' | 'partial' | 'failed';
export interface UsageSource {
  readonly fingerprint: UsageSourceFingerprint;
  readonly status: UsageSourceStatus;
  readonly scannedFiles: NonNegativeInt;
  readonly skippedFiles: NonNegativeInt;
  readonly malformedRecords: NonNegativeInt;
  readonly distinctSessions: NonNegativeInt;
  readonly message: TrimmedNonEmptyString | null;
}
export type UsagePricingStatus = 'fresh' | 'cached' | 'unavailable';
export interface UsagePricing {
  readonly status: UsagePricingStatus;
  readonly source: TrimmedNonEmptyString;
  readonly fetchedAt: string | null;
  readonly knownModels: NonNegativeInt;
}
export interface UsageSummaryInput {
  readonly sinceDay: UsageDay;
  readonly untilDay: UsageDay;
  readonly timeZone: TrimmedNonEmptyString;
  readonly resolution?: UsageResolution;
  readonly sinceTime?: TrimmedNonEmptyString;
  readonly untilTime?: TrimmedNonEmptyString;
}
export interface UsageSummary {
  readonly contractVersion: number;
  readonly readAt: string;
  readonly timeZone: TrimmedNonEmptyString;
  readonly sinceDay: UsageDay;
  readonly untilDay: UsageDay;
  readonly buckets: ReadonlyArray<UsageBucket>;
  readonly sources: ReadonlyArray<UsageSource>;
  readonly pricing: UsagePricing;
  readonly scanDurationMs: NonNegativeInt;
}
export interface ServerProviderUsageWindow {
  readonly id: TrimmedNonEmptyString;
  readonly kind: 'session' | 'weekly' | 'monthly' | 'other';
  readonly label: TrimmedNonEmptyString;
  readonly usedPercent: number;
  readonly resetsAt?: IsoDateTime;
  readonly windowDurationMins?: NonNegativeInt;
}
export interface ServerProviderResetCredits {
  readonly availableCount: NonNegativeInt;
  readonly nextExpiresAt?: IsoDateTime;
  readonly nextCreditId?: TrimmedNonEmptyString;
}
export interface ServerProviderUsageLimits {
  readonly checkedAt: IsoDateTime;
  readonly windows: ReadonlyArray<ServerProviderUsageWindow>;
  readonly resetCredits?: ServerProviderResetCredits;
  readonly unavailable?: {
    readonly reason: 'unsupported' | 'probeFailed';
    readonly message?: TrimmedNonEmptyString;
  };
}
export const USAGE_CONTRACT_VERSION = 5;
export class UsageReadError extends Error {
  readonly reason: string;
  readonly detail: string;
  constructor(reason: string, detail: string) {
    super(`Usage read failed (${reason}): ${detail}`);
    this.reason = reason;
    this.detail = detail;
  }
}
/** The service no longer admits work after lifecycle shutdown starts. */
export class UsageServiceStopped extends Error {
  constructor() {
    super('Usage service is stopping.');
    this.name = 'UsageServiceStopped';
  }
}
export interface UsageModelPriceOverride {
  readonly inputCostPerMillionTokens: number;
  readonly outputCostPerMillionTokens: number;
  readonly cacheReadCostPerMillionTokens?: number | null;
  readonly cacheWriteCostPerMillionTokens?: number | null;
}
export interface UsageSettings {
  readonly usagePriceOverrides?: Readonly<Record<string, UsageModelPriceOverride>>;
}
export interface TranscriptDirectory {
  readonly provider: UsageProviderKind;
  readonly dir: string;
  readonly fileName?: string;
}
export type TranscriptDirectoryEntry = TranscriptDirectory | readonly [UsageProviderKind, string];
export type TranscriptDirectories =
  | readonly TranscriptDirectoryEntry[]
  | ((settings: UsageSettings) => readonly TranscriptDirectoryEntry[]);
export interface RatesFetcher {
  (options?: { signal?: AbortSignal }): unknown | Promise<unknown>;
}
export interface UsageServiceOptions {
  readonly ratesFetcher?: RatesFetcher;
  readonly settingsProvider?: () => UsageSettings;
  /** Epoch milliseconds. */
  readonly now?: () => number;
}
export interface RecordedUsageService {
  readSummary(input: UsageSummaryInput): Promise<UsageSummary>;
  refreshRates(): Promise<UsagePricing>;
  finish_operations(): Promise<void>;
  /** Shared absolute monotonic deadline, in performance.now()/1000 seconds. */
  stop?(options: { readonly deadline: number }): Promise<void>;
}
export interface UsageHttpRuntime {
  readonly conversation_runtime_available: boolean;
  readonly recorded_usage: RecordedUsageService;
  readonly backend_state: BackendStateStore | null;
}

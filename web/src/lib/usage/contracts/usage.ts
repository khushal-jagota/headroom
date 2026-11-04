/** Mechanical type projection of T3 75e4ceb9. See T3-LICENSE and frontend-translation.md. */
import type { NonNegativeInt, TrimmedNonEmptyString } from "./index";

// usage.ts:35
export type UsageProviderKind = "claude" | "codex" | "grok";

// usage.ts:46
export type UsageDay = string & { readonly __brand: "UsageDay" };

// usage.ts:51
export type UsageResolution = "day" | "hour";

// usage.ts:62
export type UsageCostSource = "providerReported" | "modelPriced" | "unpriced";

// usage.ts:73
export type UsageTokenTotals = {
  readonly uncachedInputTokens: NonNegativeInt;
  readonly cachedInputTokens: NonNegativeInt;
  readonly cacheCreationTokens: NonNegativeInt;
  readonly outputTokens: NonNegativeInt;
  readonly reasoningTokens: NonNegativeInt;
};

// usage.ts:91
export type UsageBucket = {
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
};

// usage.ts:120
export type UsageSourceFingerprint = {
  readonly hostId: TrimmedNonEmptyString;
  readonly provider: UsageProviderKind;
  readonly resolvedHomePath: TrimmedNonEmptyString;
  readonly volumeId: string;
};

// usage.ts:137
export type UsageSourceStatus = "ok" | "missing" | "partial" | "failed";

// usage.ts:140
export type UsageSource = {
  readonly fingerprint: UsageSourceFingerprint;
  readonly status: UsageSourceStatus;
  readonly scannedFiles: NonNegativeInt;
  readonly skippedFiles: NonNegativeInt;
  readonly malformedRecords: NonNegativeInt;
  readonly distinctSessions: NonNegativeInt;
  readonly message: (TrimmedNonEmptyString) | null;
};

// usage.ts:157
export type UsagePricingStatus = "fresh" | "cached" | "unavailable";

// usage.ts:164
export type UsagePricing = {
  readonly status: UsagePricingStatus;
  readonly source: TrimmedNonEmptyString;
  readonly fetchedAt: (string) | null;
  readonly knownModels: NonNegativeInt;
};

// usage.ts:172
export type UsageSummaryInput = {
  readonly sinceDay: UsageDay;
  readonly untilDay: UsageDay;
  readonly timeZone: TrimmedNonEmptyString;
  readonly resolution?: UsageResolution;
  readonly sinceTime?: TrimmedNonEmptyString;
  readonly untilTime?: TrimmedNonEmptyString;
};

// usage.ts:191
export type UsageSummary = {
  readonly contractVersion: number;
  readonly readAt: string;
  readonly timeZone: TrimmedNonEmptyString;
  readonly sinceDay: UsageDay;
  readonly untilDay: UsageDay;
  readonly buckets: ReadonlyArray<UsageBucket>;
  readonly sources: ReadonlyArray<UsageSource>;
  readonly pricing: UsagePricing;
  readonly scanDurationMs: NonNegativeInt;
};

export const USAGE_CONTRACT_VERSION = 5 as const;
export const USAGE_MERGE_COMPATIBLE_SINCE = 4 as const;
export const UsageDay = {
  make(value: string): UsageDay {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new TypeError("Expected YYYY-MM-DD");
    return value as UsageDay;
  },
};

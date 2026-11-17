/** Adapted from T3 Code 75e4ceb96489a2fe92c710c79dbea59778cccfd1.
 * Copyright (c) 2026 T3 Tools Inc. MIT permission notice: ./T3-LICENSE.
 */
/**
 * The usage record every transcript parser produces, and the helpers the
 * parsers share. Each source's own line or file format lives with its parser.
 *
 * @module usageTranscripts
 */
import type { UsageProviderKind, UsageTokenTotals } from './contracts.ts';
import { parseIsoDatetime } from '../../core/clock.ts';
import { numeric_value } from '../../core/http/numeric_json.ts';

export interface UsageRecord {
  readonly provider: UsageProviderKind;
  readonly timestampMs: number;
  readonly model: string;
  /** The rate-table name when it differs from the model the transcript reports. */
  readonly pricing_model?: string;
  readonly sessionId: string;
  readonly totals: UsageTokenTotals;
  readonly reportedCostUsd: number | null;
  readonly cacheCreation1hTokens?: number;
  readonly serviceTier?: string | null;
  readonly compactionCovered?: boolean;
  /**
   * Key for cross-file de-duplication, or `null` when the record is inherently
   * unique and needs no dedup.
   */
  readonly dedupeKey: string | null;
}

const EMPTY_TOTALS: UsageTokenTotals = {
  uncachedInputTokens: 0,
  cachedInputTokens: 0,
  cacheCreationTokens: 0,
  outputTokens: 0,
  reasoningTokens: 0,
};

/** A positive token count, truncated to an integer. Anything else counts as zero. */
export function tokenCount(value: unknown): number {
  const number = numeric_value(value);
  return number !== undefined && Number.isFinite(number) && number > 0 ? Math.trunc(number) : 0;
}

export function finiteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

export function parseTimestampMs(value: unknown): number | null {
  if (typeof value !== 'string') return null;
  try {
    const micros = parseIsoDatetime(value.replaceAll('Z', '+00:00')).epochMicroseconds;
    return Number(micros >= 0n ? micros / 1000n : (micros - 999n) / 1000n);
  } catch {
    return null;
  }
}

export function addTotals(a: UsageTokenTotals, b: UsageTokenTotals): UsageTokenTotals {
  return {
    uncachedInputTokens: a.uncachedInputTokens + b.uncachedInputTokens,
    cachedInputTokens: a.cachedInputTokens + b.cachedInputTokens,
    cacheCreationTokens: a.cacheCreationTokens + b.cacheCreationTokens,
    outputTokens: a.outputTokens + b.outputTokens,
    reasoningTokens: a.reasoningTokens + b.reasoningTokens,
  };
}

export function totalTokens(totals: UsageTokenTotals): number {
  // reasoningTokens is a subset of outputTokens and must not be added again.
  return (
    totals.uncachedInputTokens +
    totals.cachedInputTokens +
    totals.cacheCreationTokens +
    totals.outputTokens
  );
}

/** A line that yields no record. Shared, so a skipped line allocates nothing. */
export const NO_RECORDS: readonly UsageRecord[] = Object.freeze([]);

export { EMPTY_TOTALS };

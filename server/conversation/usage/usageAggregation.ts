/** Adapted from T3 Code 75e4ceb96489a2fe92c710c79dbea59778cccfd1.
 * Copyright (c) 2026 T3 Tools Inc. MIT permission notice: ./T3-LICENSE.
 */
/**
 * Folds parsed transcript records into `(day, hourStart?, provider, model)`
 * buckets.
 *
 * `Intl.DateTimeFormat` is the only reliable way to resolve a wall-clock day in
 * an arbitrary IANA zone, and it takes a `Date`. That is why the raw `Date`
 * construction is allowed here; nothing in this module reads the clock.
 *
 * Pure, so the bucketing and de-duplication rules are testable without touching
 * the filesystem or the network.
 *
 * @module usageAggregation
 */
import type { UsageBucket, UsageDay, UsageResolution, UsageTokenTotals } from './contracts.ts';
import { timezone } from './timeZone.ts';

import { addTotals, EMPTY_TOTALS, type UsageRecord } from './usageTranscripts.ts';
import { cacheSavingsUsd, priceUsage, type RateTable } from './usagePricing.ts';

/**
 * Formats an instant as a `YYYY-MM-DD` day in `timeZone`.
 *
 * `en-CA` yields ISO-ordered parts, which is why it is used here rather than
 * assembling the day from `Date` getters (those are host-local only).
 */
function makeDayFormatter(timeZone: string): (timestampMs: number) => string {
  const format = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone(timeZone),
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
  return (timestampMs) => {
    const parts = format.formatToParts(new Date(timestampMs));
    const value = (type: string) => parts.find((part) => part.type === type)!.value;
    return `${value('year').padStart(4, '0')}-${value('month')}-${value('day')}`;
  };
}

const HOUR_MS = 60 * 60 * 1000;

export function isoTimestamp(timestampMs: number): string {
  return new Date(Math.floor(timestampMs)).toISOString();
}

interface MutableBucket {
  totals: UsageTokenTotals;
  costUsd: number;
  cacheSavingsUsd: number;
  records: number;
  unpricedRecords: number;
  providerReportedRecords: number;
  sessions: Set<string>;
}

export interface AggregateOptions {
  readonly timeZone: string;
  readonly sinceDay: string;
  readonly untilDay: string;
  readonly rates: RateTable;
  readonly priceOverrides?: RateTable;
  readonly resolution?: UsageResolution;
  readonly sinceTimeMs?: number;
  readonly untilTimeMs?: number;
}

export interface AggregateResult {
  readonly buckets: readonly UsageBucket[];
  /** Records dropped because an earlier record carried the same dedupe key. */
  readonly duplicatesDropped: number;
  /** Records whose day fell outside the requested window. */
  readonly outOfWindow: number;
}

/**
 * Accumulates records across many files.
 *
 * De-duplication is global across the whole scan, not per file: Claude Code
 * copies a message's records forward when a session is resumed or forked, so
 * the same `dedupeKey` legitimately appears in several transcripts.
 */
export class UsageAggregator {
  readonly #buckets = new Map<string, MutableBucket>();
  readonly #seen = new Set<string>();
  readonly #toDay: (timestampMs: number) => string;
  readonly #hourlyWindow: { readonly sinceTimeMs: number; readonly untilTimeMs: number } | null;
  readonly #options: AggregateOptions;
  #duplicatesDropped = 0;
  #outOfWindow = 0;

  constructor(options: AggregateOptions) {
    this.#options = options;
    this.#toDay = makeDayFormatter(options.timeZone);
    if (options.resolution === 'hour') {
      if (options.sinceTimeMs === undefined || options.untilTimeMs === undefined) {
        throw new Error('Hourly usage aggregation requires exact time bounds');
      }
      this.#hourlyWindow = {
        sinceTimeMs: options.sinceTimeMs,
        untilTimeMs: options.untilTimeMs,
      };
    } else {
      this.#hourlyWindow = null;
    }
  }

  /**
   * Folds one record in. Returns whether it actually contributed, so callers
   * can derive per-window facts (distinct sessions, for one) from the records
   * that landed rather than everything the mtime prefilter happened to admit.
   */
  add(record: UsageRecord): boolean {
    if (record.dedupeKey !== null) {
      if (this.#seen.has(record.dedupeKey)) {
        this.#duplicatesDropped += 1;
        return false;
      }
      this.#seen.add(record.dedupeKey);
    }

    if (
      this.#hourlyWindow !== null &&
      (record.timestampMs < this.#hourlyWindow.sinceTimeMs ||
        record.timestampMs >= this.#hourlyWindow.untilTimeMs)
    ) {
      this.#outOfWindow += 1;
      return false;
    }

    const day = this.#toDay(record.timestampMs);
    if (
      this.#hourlyWindow === null &&
      (day < this.#options.sinceDay || day > this.#options.untilDay)
    ) {
      this.#outOfWindow += 1;
      return false;
    }

    const hourStart =
      this.#hourlyWindow === null
        ? ''
        : isoTimestamp(
            this.#hourlyWindow.sinceTimeMs +
              Math.floor((record.timestampMs - this.#hourlyWindow.sinceTimeMs) / HOUR_MS) * HOUR_MS,
          );
    const key = `${day}\u0000${hourStart}\u0000${record.provider}\u0000${record.model}`;
    let bucket = this.#buckets.get(key);
    if (bucket === undefined) {
      bucket = {
        totals: EMPTY_TOTALS,
        costUsd: 0,
        cacheSavingsUsd: 0,
        records: 0,
        unpricedRecords: 0,
        providerReportedRecords: 0,
        sessions: new Set<string>(),
      };
      this.#buckets.set(key, bucket);
    }

    const priced = priceUsage(
      this.#options.rates,
      record.model,
      record.totals,
      record.reportedCostUsd,
      this.#options.priceOverrides,
    );

    bucket.totals = addTotals(bucket.totals, record.totals);
    bucket.costUsd += priced.costUsd;
    bucket.cacheSavingsUsd += cacheSavingsUsd(
      this.#options.rates,
      record.model,
      record.totals,
      this.#options.priceOverrides,
    );
    bucket.records += 1;
    if (priced.costSource === 'unpriced') bucket.unpricedRecords += 1;
    if (priced.costSource === 'providerReported') bucket.providerReportedRecords += 1;
    if (record.sessionId.length > 0) bucket.sessions.add(record.sessionId);
    return true;
  }

  finish(): AggregateResult {
    const buckets: UsageBucket[] = [];
    for (const [key, bucket] of this.#buckets) {
      const [day = '', hourStart = '', provider = '', model = ''] = key.split('\u0000');
      buckets.push({
        day: day as UsageDay,
        ...(hourStart === '' ? {} : { hourStart }),
        provider: provider as UsageBucket['provider'],
        model,
        totals: bucket.totals,
        costUsd: bucket.costUsd,
        cacheSavingsUsd: bucket.cacheSavingsUsd,
        costSource: resolveCostSource(bucket),
        records: bucket.records,
        unpricedRecords: bucket.unpricedRecords,
        sessions: bucket.sessions.size,
      });
    }
    // Stable ordering keeps payloads diffable and snapshot tests meaningful.
    buckets.sort(
      (a, b) =>
        textOrder(a.day, b.day) ||
        textOrder(a.hourStart ?? '', b.hourStart ?? '') ||
        textOrder(a.provider, b.provider) ||
        textOrder(a.model, b.model),
    );

    return {
      buckets,
      duplicatesDropped: this.#duplicatesDropped,
      outOfWindow: this.#outOfWindow,
    };
  }
}

function textOrder(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

/**
 * A bucket mixes records from one model, but their cost provenance can differ
 * when only some records carried a reported cost. The weakest provenance in the
 * bucket wins so the UI never overstates confidence.
 */
function resolveCostSource(bucket: MutableBucket): UsageBucket['costSource'] {
  if (bucket.unpricedRecords === bucket.records) return 'unpriced';
  if (bucket.providerReportedRecords === bucket.records) return 'providerReported';
  return 'modelPriced';
}

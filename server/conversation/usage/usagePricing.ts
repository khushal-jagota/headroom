/** Adapted from T3 Code 75e4ceb96489a2fe92c710c79dbea59778cccfd1.
 * Copyright (c) 2026 T3 Tools Inc. MIT permission notice: ./T3-LICENSE.
 */
/**
 * Model rate lookup and cost arithmetic.
 *
 * Rates come from LiteLLM's `model_prices_and_context_window.json`, the same
 * table `ccusage` prices against. Everything here is pure: fetching and caching
 * the table lives in `UsageService`.
 *
 * @module usagePricing
 */
import type { UsageCostSource, UsageModelPriceOverride, UsageTokenTotals } from './contracts.ts';
import { is_record } from '../../core/records.ts';

/**
 * The subset of a LiteLLM entry we price against. All values are USD per token.
 *
 * Long-context rates apply per request, using its complete prompt token count.
 * Other service tiers retain the published base rate.
 */
export interface ModelRate {
  readonly inputCostPerToken: number;
  readonly outputCostPerToken: number;
  readonly cacheReadCostPerToken: number;
  readonly cacheCreationCostPerToken: number;
  readonly inputCostPerTokenAbove272k?: number;
  readonly outputCostPerTokenAbove272k?: number;
  readonly cacheReadCostPerTokenAbove272k?: number;
  readonly cacheCreationCostPerTokenAbove272k?: number;
  readonly cacheCreation1hCostPerToken?: number;
}

export type RateTable = ReadonlyMap<string, ModelRate>;

const LONG_CONTEXT_THRESHOLD = 272_000;
const LONG_CONTEXT_RATE_KEYS = {
  inputCostPerToken: 'input_cost_per_token_above_272k_tokens',
  outputCostPerToken: 'output_cost_per_token_above_272k_tokens',
  cacheReadCostPerToken: 'cache_read_input_token_cost_above_272k_tokens',
  cacheCreationCostPerToken: 'cache_creation_input_token_cost_above_272k_tokens',
} as const;
type BaseRateKey = keyof typeof LONG_CONTEXT_RATE_KEYS;
type ExtendedRateKey = `${BaseRateKey}Above272k` | 'cacheCreation1hCostPerToken';
const BASE_RATE_KEYS = Object.keys(LONG_CONTEXT_RATE_KEYS) as BaseRateKey[];
const EXTENDED_RATE_KEYS: readonly ExtendedRateKey[] = [
  ...BASE_RATE_KEYS.map((key): ExtendedRateKey => `${key}Above272k`),
  'cacheCreation1hCostPerToken',
];

/** Custom IDs keep their case, provider prefix, and variant suffix. */
export function createOverrideRateTable(
  overrides: Readonly<Record<string, UsageModelPriceOverride>>,
): RateTable {
  return new Map(
    Object.entries(overrides).map(([model, prices]) => [
      model.trim(),
      {
        inputCostPerToken: prices.inputCostPerMillionTokens / 1_000_000,
        outputCostPerToken: prices.outputCostPerMillionTokens / 1_000_000,
        cacheReadCostPerToken:
          (prices.cacheReadCostPerMillionTokens ?? prices.inputCostPerMillionTokens) / 1_000_000,
        cacheCreationCostPerToken:
          (prices.cacheWriteCostPerMillionTokens ?? prices.inputCostPerMillionTokens) / 1_000_000,
      },
    ]),
  );
}

/** Raw shape of one LiteLLM entry, narrowed to the fields we read. */
interface LiteLlmEntry {
  readonly input_cost_per_token?: unknown;
  readonly output_cost_per_token?: unknown;
  readonly cache_read_input_token_cost?: unknown;
  readonly cache_creation_input_token_cost?: unknown;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/**
 * Projects the LiteLLM document into a rate table.
 *
 * Entries without both an input and an output rate are dropped: a half-priced
 * model would silently under-report cost, which is worse than reporting the
 * model as unpriced.
 *
 * Entries keep their full normalized key; a bare name is aliased only when no
 * canonical entry exists and every qualified entry has the same rate.
 */
export function parseRateTable(document: unknown): RateTable {
  const table = new Map<string, ModelRate>();
  if (!is_record(document)) return table;

  for (const [name, raw] of Object.entries(document as Record<string, unknown>)) {
    if (!is_record(raw)) continue;
    const entry = raw as LiteLlmEntry;
    const input = finiteNumber(entry.input_cost_per_token);
    const output = finiteNumber(entry.output_cost_per_token);
    if (input === null || output === null) continue;

    const key = normalizeRateKey(name);
    if (key.length === 0) continue;
    const extended: Partial<Record<ExtendedRateKey, number>> = {};
    for (const rateKey of BASE_RATE_KEYS) {
      const value = finiteNumber(raw[LONG_CONTEXT_RATE_KEYS[rateKey]]);
      if (value !== null) extended[`${rateKey}Above272k`] = value;
    }
    const oneHourRate = finiteNumber(raw.cache_creation_input_token_cost_above_1hr);
    if (oneHourRate !== null) extended.cacheCreation1hCostPerToken = oneHourRate;
    table.set(key, {
      ...extended,
      inputCostPerToken: input,
      outputCostPerToken: output,
      // Anthropic bills cache reads at a discount and cache writes at a
      // premium. When a model omits them, cached input is priced as plain
      // input rather than as free.
      cacheReadCostPerToken: finiteNumber(entry.cache_read_input_token_cost) ?? input,
      cacheCreationCostPerToken: finiteNumber(entry.cache_creation_input_token_cost) ?? input,
    });
  }

  // `null` marks a bare name claimed at conflicting rates: no alias for it.
  const aliasCandidates = new Map<string, ModelRate | null>();
  for (const [key, rate] of table) {
    const alias = bareModelName(key);
    if (alias.length === 0 || alias === key || table.has(alias)) continue;
    const held = aliasCandidates.get(alias);
    if (held === undefined) {
      aliasCandidates.set(alias, rate);
    } else if (held !== null && !sameRate(held, rate)) {
      aliasCandidates.set(alias, null);
    }
  }
  for (const [alias, rate] of aliasCandidates) {
    if (rate !== null) table.set(alias, rate);
  }

  return table;
}

function sameRate(a: ModelRate, b: ModelRate): boolean {
  return [...BASE_RATE_KEYS, ...EXTENDED_RATE_KEYS].every((key) => a[key] === b[key]);
}

function normalizeRateKey(model: string): string {
  return model.trim().toLowerCase();
}

function bareModelName(key: string): string {
  const slash = key.lastIndexOf('/');
  return slash === -1 ? key : key.slice(slash + 1);
}

/**
 * Drops a bracketed variant suffix such as `claude-fable-5-1[1m]`, which
 * Claude Code writes for the 1M context tier. The rate table only knows the
 * base name. Prompt token counts select its context tier separately.
 */
function stripVariantSuffix(key: string): string {
  const bracket = key.indexOf('[');
  return bracket === -1 ? key : key.slice(0, bracket);
}

/**
 * Models we never price, regardless of the table.
 *
 * `<synthetic>` marks locally generated messages that were never billed. Bare
 * family names ("opus", "sonnet") are genuinely ambiguous across generations,
 * so we report them as unpriced instead of guessing a generation.
 */
const UNPRICEABLE_MODELS = new Set([
  '<synthetic>',
  'synthetic',
  'opus',
  'sonnet',
  'haiku',
  'fable',
]);

export function lookupRate(table: RateTable, model: string): ModelRate | null {
  const key = stripVariantSuffix(normalizeRateKey(model));
  const bareName = bareModelName(key);
  if (bareName.length === 0 || UNPRICEABLE_MODELS.has(bareName)) return null;
  return table.get(key) ?? null;
}

function selectRecordRate(
  table: RateTable,
  model: string,
  totals: UsageTokenTotals,
  overrides?: RateTable,
): ModelRate | null {
  const override = overrides?.get(model.trim());
  if (override !== undefined) return override;
  const rate = lookupRate(table, model);
  if (rate === null) return null;
  const promptTokens =
    totals.uncachedInputTokens + totals.cachedInputTokens + totals.cacheCreationTokens;
  if (promptTokens <= LONG_CONTEXT_THRESHOLD) return rate;
  const selected = { ...rate };
  for (const key of BASE_RATE_KEYS) {
    const value = rate[`${key}Above272k`];
    if (value !== undefined) selected[key] = value;
  }
  return selected;
}

export interface PricedUsage {
  readonly costUsd: number;
  readonly costSource: UsageCostSource;
}

/**
 * Prices one request's tokens before aggregation.
 *
 * `reasoningTokens` is intentionally not charged separately: it is already
 * counted inside `outputTokens`.
 */
export function priceUsage(
  table: RateTable,
  model: string,
  totals: UsageTokenTotals,
  reportedCostUsd: number | null,
  overrides?: RateTable,
  cacheCreation1hTokens = 0,
): PricedUsage {
  const override = overrides?.get(model.trim());
  if (override === undefined && reportedCostUsd !== null && Number.isFinite(reportedCostUsd)) {
    return { costUsd: reportedCostUsd, costSource: 'providerReported' };
  }

  const rate = selectRecordRate(table, model, totals, overrides);
  if (rate === null) return { costUsd: 0, costSource: 'unpriced' };

  const oneHourTokens = Math.min(totals.cacheCreationTokens, Math.max(0, cacheCreation1hTokens));
  const oneHourRate = rate.cacheCreation1hCostPerToken ?? rate.cacheCreationCostPerToken;
  const costUsd =
    totals.uncachedInputTokens * rate.inputCostPerToken +
    totals.cachedInputTokens * rate.cacheReadCostPerToken +
    (totals.cacheCreationTokens - oneHourTokens) * rate.cacheCreationCostPerToken +
    oneHourTokens * oneHourRate +
    totals.outputTokens * rate.outputCostPerToken;

  return { costUsd, costSource: 'modelPriced' };
}

/**
 * What the cached input would have cost at full input rates, minus what it
 * actually cost. Drives the "cache savings" figure.
 */
export function cacheSavingsUsd(
  table: RateTable,
  model: string,
  totals: UsageTokenTotals,
  overrides?: RateTable,
): number {
  const rate = selectRecordRate(table, model, totals, overrides);
  if (rate === null) return 0;
  return totals.cachedInputTokens * (rate.inputCostPerToken - rate.cacheReadCostPerToken);
}

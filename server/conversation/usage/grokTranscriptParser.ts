/** Adapted from T3 Code 75e4ceb96489a2fe92c710c79dbea59778cccfd1.
 * Copyright (c) 2026 T3 Tools Inc. MIT permission notice: ./T3-LICENSE.
 */
/**
 * The owner's own Grok Build CLI transcripts. Grok is a usage source, not a
 * driver: nothing launches it, and Panels only reads what it wrote.
 *
 * @module grokTranscriptParser
 */
import type { LineTranscriptParser } from '../../drivers/core/usage.ts';
import type { UsageTokenTotals } from './contracts.ts';
import { is_record } from '../../core/records.ts';
import { tokenCount, totalTokens, type UsageRecord } from './usageTranscripts.ts';

/**
 * Grok reports cost in integer ticks where `1 USD = 10^10` ticks. See Grok
 * headless `total_cost_usd_ticks`. Convert to dollars for pricing.
 */
export const GROK_COST_USD_TICKS_PER_DOLLAR = 10_000_000_000;

function grokCostTicksToUsd(ticks: unknown): number | null {
  if (typeof ticks !== 'number' || !Number.isFinite(ticks) || ticks < 0) return null;
  return ticks / GROK_COST_USD_TICKS_PER_DOLLAR;
}

interface GrokUsageTotals {
  readonly inputTokens: number;
  readonly outputTokens: number;
  readonly cachedReadTokens: number;
  readonly cacheCreationTokens: number;
  readonly reasoningTokens: number;
  readonly costUsdTicks: number | null;
}

function readGrokUsageTotals(value: unknown): GrokUsageTotals | null {
  if (!is_record(value)) return null;
  const record = value as Record<string, unknown>;
  return {
    inputTokens: tokenCount(record['inputTokens']),
    outputTokens: tokenCount(record['outputTokens']),
    cachedReadTokens: tokenCount(record['cachedReadTokens']),
    cacheCreationTokens: tokenCount(record['cacheCreationTokens']),
    reasoningTokens: tokenCount(record['reasoningTokens']),
    costUsdTicks:
      typeof record['costUsdTicks'] === 'number' && Number.isFinite(record['costUsdTicks'])
        ? record['costUsdTicks']
        : null,
  };
}

function grokTotalsToUsage(totals: GrokUsageTotals): UsageTokenTotals {
  const cachedInputTokens = totals.cachedReadTokens;
  const cacheCreationTokens = totals.cacheCreationTokens;
  // Grok reports `inputTokens` inclusive of the cached portion, matching Codex.
  const uncachedInputTokens = Math.max(
    0,
    totals.inputTokens - cachedInputTokens - cacheCreationTokens,
  );
  const outputTokens = totals.outputTokens;
  return {
    uncachedInputTokens,
    cachedInputTokens,
    cacheCreationTokens,
    outputTokens,
    reasoningTokens: Math.min(outputTokens, totals.reasoningTokens),
  };
}

/**
 * Parses one line of a Grok Build `updates.jsonl` session log.
 *
 * Usage lands on `turn_completed` session updates. Per-model breakdowns live
 * under `usage.modelUsage`; when present each model becomes its own record.
 *
 * Returns every record for the line (0 or more). Callers stream line-by-line
 * and flatten.
 */
export function parseGrokLine(line: string): readonly UsageRecord[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(line);
  } catch {
    return [];
  }
  if (!is_record(parsed)) return [];

  const record = parsed as Record<string, unknown>;
  const params = record['params'];
  if (!is_record(params)) return [];
  const paramsRecord = params as Record<string, unknown>;

  const update = paramsRecord['update'];
  if (!is_record(update)) return [];
  const updateRecord = update as Record<string, unknown>;
  if (updateRecord['sessionUpdate'] !== 'turn_completed') return [];

  const usage = updateRecord['usage'];
  if (!is_record(usage)) return [];
  const usageRecord = usage as Record<string, unknown>;

  const sessionId = typeof paramsRecord['sessionId'] === 'string' ? paramsRecord['sessionId'] : '';
  const promptId = typeof updateRecord['prompt_id'] === 'string' ? updateRecord['prompt_id'] : null;

  // Prefer the high-resolution agent clock; fall back to the outer unix seconds.
  const meta = paramsRecord['_meta'];
  let timestampMs: number | null = null;
  if (is_record(meta)) {
    const agentTimestampMs = (meta as Record<string, unknown>)['agentTimestampMs'];
    if (typeof agentTimestampMs === 'number' && Number.isFinite(agentTimestampMs)) {
      timestampMs = agentTimestampMs;
    }
  }
  if (timestampMs === null) {
    const timestamp = record['timestamp'];
    if (typeof timestamp === 'number' && Number.isFinite(timestamp)) {
      timestampMs = timestamp > 1e12 ? timestamp : timestamp * 1000;
    }
  }
  if (timestampMs === null) return [];

  const topLevel = readGrokUsageTotals(usageRecord);
  if (topLevel === null) return [];

  const modelUsage = usageRecord['modelUsage'];
  const modelEntries: Array<{ model: string; totals: GrokUsageTotals }> = [];
  if (is_record(modelUsage)) {
    for (const [model, raw] of Object.entries(modelUsage as Record<string, unknown>)) {
      if (model.length === 0) continue;
      const totals = readGrokUsageTotals(raw);
      if (totals === null) continue;
      modelEntries.push({ model, totals });
    }
  }

  if (modelEntries.length === 0) {
    if (totalTokens(grokTotalsToUsage(topLevel)) === 0) return [];
    return [
      {
        provider: 'grok',
        timestampMs,
        model: 'grok',
        sessionId,
        totals: grokTotalsToUsage(topLevel),
        reportedCostUsd: grokCostTicksToUsd(topLevel.costUsdTicks),
        // No prompt id means we cannot tell two same-second updates apart.
        dedupeKey: promptId === null ? null : `${sessionId}:${promptId}:grok`,
      },
    ];
  }

  // Cost allocation:
  // 1. Emitted models with their own costUsdTicks keep those values.
  // 2. Remaining aggregate cost (top-level minus those per-model ticks,
  //    clamped at 0) is pro-rated across emitted models that lack ticks,
  //    by token share among the unticked models only.
  // 3. When no model has per-model ticks, remaining equals the full
  //    aggregate and every emitted model gets a token-share slice.
  // Zero-token rows are never emitted and never count toward used ticks.
  const topLevelCostUsd = grokCostTicksToUsd(topLevel.costUsdTicks);
  let usedTickedCostUsd = 0;
  let untickedTokenDenominator = 0;
  for (const entry of modelEntries) {
    const tokens = totalTokens(grokTotalsToUsage(entry.totals));
    if (tokens === 0) continue;
    if (entry.totals.costUsdTicks !== null) {
      usedTickedCostUsd += grokCostTicksToUsd(entry.totals.costUsdTicks) ?? 0;
    } else {
      untickedTokenDenominator += tokens;
    }
  }
  const remainingCostUsd =
    topLevelCostUsd === null ? null : Math.max(0, topLevelCostUsd - usedTickedCostUsd);

  const results: UsageRecord[] = [];
  for (const entry of modelEntries) {
    const totals = grokTotalsToUsage(entry.totals);
    if (totalTokens(totals) === 0) continue;

    let reportedCostUsd = grokCostTicksToUsd(entry.totals.costUsdTicks);
    if (reportedCostUsd === null && remainingCostUsd !== null && untickedTokenDenominator > 0) {
      reportedCostUsd = remainingCostUsd * (totalTokens(totals) / untickedTokenDenominator);
    }

    results.push({
      provider: 'grok',
      timestampMs,
      model: entry.model,
      sessionId,
      totals,
      reportedCostUsd,
      dedupeKey: promptId === null ? null : `${sessionId}:${promptId}:${entry.model}`,
    });
  }
  return results;
}

/** Usage lands only on `turn_completed` updates, and Grok carries no state between lines. */
const grokTranscriptParser: LineTranscriptParser<null> = {
  kind: 'lines',
  initial_state: () => null,
  revive_state: () => null,
  might_carry_usage: (line) => line.includes('"turn_completed"'),
  parse_line: parseGrokLine,
};
export default grokTranscriptParser;

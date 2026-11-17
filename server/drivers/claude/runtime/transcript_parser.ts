/** Adapted from T3 Code 75e4ceb96489a2fe92c710c79dbea59778cccfd1.
 * Copyright (c) 2026 T3 Tools Inc. MIT permission notice: server/conversation/usage/T3-LICENSE.
 */
/** Token usage from Claude Code's session transcripts, one line at a time. */
import type { LineTranscriptParser } from '../../core/usage.ts';
import { is_record } from '../../../core/records.ts';
import {
  NO_RECORDS,
  parseTimestampMs,
  tokenCount,
  type UsageRecord,
} from '../../../conversation/usage/usageTranscripts.ts';

/**
 * Parses one line of a Claude Code transcript.
 *
 * T3 Code writes one record per assistant *content block*, and every one of
 * those records repeats the same complete `usage` object for the parent
 * message. Summing them overcounts by roughly 2.4x on a real workload, so the
 * caller must drop repeats by `dedupeKey` and keep the first.
 */
export function parseClaudeLine(line: string): UsageRecord | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(line);
  } catch {
    return null;
  }
  if (!is_record(parsed)) return null;

  const record = parsed as Record<string, unknown>;
  if (record['type'] !== 'assistant') return null;

  const message = record['message'];
  if (!is_record(message)) return null;
  const messageRecord = message as Record<string, unknown>;

  const usage = messageRecord['usage'];
  if (!is_record(usage)) return null;
  const usageRecord = usage as Record<string, unknown>;

  const timestampMs = parseTimestampMs(record['timestamp']);
  if (timestampMs === null) return null;

  const model = typeof messageRecord['model'] === 'string' ? messageRecord['model'] : '';
  if (model.length === 0) return null;

  const messageId = typeof messageRecord['id'] === 'string' ? messageRecord['id'] : null;
  const requestId = typeof record['requestId'] === 'string' ? record['requestId'] : null;
  // Matches ccusage: prefer the message/request pair, fall back to whichever
  // half exists. Records with neither cannot be de-duplicated.
  const dedupeKey =
    messageId === null && requestId === null ? null : `${messageId ?? ''}:${requestId ?? ''}`;

  const cost = record['costUSD'];
  const creation = usageRecord['cache_creation'];
  const cacheCreation1hTokens = is_record(creation)
    ? Math.min(
        tokenCount(usageRecord['cache_creation_input_tokens']),
        tokenCount(creation['ephemeral_1h_input_tokens']),
      )
    : 0;

  return {
    ...(cacheCreation1hTokens ? { cacheCreation1hTokens } : {}),
    provider: 'claude',
    timestampMs,
    model,
    sessionId: typeof record['sessionId'] === 'string' ? record['sessionId'] : '',
    totals: {
      uncachedInputTokens: tokenCount(usageRecord['input_tokens']),
      cachedInputTokens: tokenCount(usageRecord['cache_read_input_tokens']),
      cacheCreationTokens: tokenCount(usageRecord['cache_creation_input_tokens']),
      outputTokens: tokenCount(usageRecord['output_tokens']),
      // Anthropic folds thinking tokens into output and does not break them out.
      reasoningTokens: 0,
    },
    reportedCostUsd: typeof cost === 'number' && Number.isFinite(cost) ? cost : null,
    dedupeKey,
  };
}

/** Claude Code lines carry no state: each assistant line holds its message's whole usage. */
const claude_transcript_parser: LineTranscriptParser<null> = {
  kind: 'lines',
  initial_state: () => null,
  revive_state: () => null,
  might_carry_usage: (line) => line.includes('"usage"'),
  parse_line: (line) => {
    const record = parseClaudeLine(line);
    return record === null ? NO_RECORDS : [record];
  },
};
export default claude_transcript_parser;

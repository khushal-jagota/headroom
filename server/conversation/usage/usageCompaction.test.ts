import { expect, test } from 'vitest';
import { initialCodexScanState, parseCodexLine, parseClaudeLine } from './usageTranscripts.ts';
import { encodeScanCache, decodeScanCache } from './usageScanCache.ts';

const timestamp = '2026-08-01T10:00:00Z';
const line = (type: string, payload: unknown) => JSON.stringify({ type, timestamp, payload });
const context = line('turn_context', { model: 'test-model' });
const usage = { input_tokens: 100, cached_input_tokens: 20, output_tokens: 5 };
const response = (id = 'compact') => line('token_usage_record', { response_id: id, usage });
const marker = (id = 'compact') => line('compacted', { compaction_response_id: id });

// A response can straddle a persisted scan boundary. A matching cumulative
// usage event must suppress it, even when the marker precedes the response.
test.each(['response-first', 'marker-first'])(
  'compaction overlap survives cached state: %s',
  (order) => {
    const state = initialCodexScanState();
    parseCodexLine(context, state);
    parseCodexLine(
      line('event_msg', {
        type: 'thread_settings_applied',
        thread_settings: { service_tier: 'fast' },
      }),
      state,
    );
    parseCodexLine(
      line('event_msg', {
        type: 'token_count',
        info: {
          last_token_usage: { input_tokens: 10, output_tokens: 1 },
          total_token_usage: { input_tokens: 10, output_tokens: 1 },
        },
      }),
      state,
    );
    parseCodexLine(order === 'response-first' ? response() : marker(), state);
    parseCodexLine(
      line('event_msg', {
        type: 'token_count',
        info: {
          last_token_usage: usage,
          total_token_usage: { input_tokens: 110, output_tokens: 6 },
        },
      }),
      state,
    );
    const cache = new Map([
      [
        'file',
        {
          size: 1,
          mtimeMs: 1,
          provider: 'codex' as const,
          records: [],
          tailRecords: [],
          position: { resumeOffset: 1, guardLength: 0, guardHash: 0, codexState: state },
        },
      ],
    ]);
    const encoded = JSON.parse(JSON.stringify(encodeScanCache(cache)));
    const corrupt = structuredClone(encoded);
    if (order === 'response-first')
      corrupt.files.file.cs.compactions.compact.usage.input_tokens = -1;
    else corrupt.files.file.cs.compactionMarkers.compact[0].previous = 'invalid';
    expect(decodeScanCache(corrupt).size).toBe(0);
    const decoded = decodeScanCache(encoded);
    const resumed = decoded.get('file')!.position.codexState!;
    const record = parseCodexLine(order === 'response-first' ? marker() : response(), resumed)!;
    expect(record.compactionCovered).toBe(true);
    expect(record.totals.uncachedInputTokens).toBe(0);
    expect(record.serviceTier).toBe('fast');
    expect(parseCodexLine(response(), resumed)).toBeNull();
    expect(parseCodexLine(marker(), resumed)).toBeNull();
  },
);

test('uncovered compaction keeps tokens and one-hour writes survive record cache', () => {
  const state = initialCodexScanState();
  parseCodexLine(context, state);
  parseCodexLine(response(), state);
  const compaction = parseCodexLine(marker(), state)!;
  expect(compaction.totals).toEqual({
    uncachedInputTokens: 80,
    cachedInputTokens: 20,
    cacheCreationTokens: 0,
    outputTokens: 5,
    reasoningTokens: 0,
  });
  const claude = parseClaudeLine(
    JSON.stringify({
      type: 'assistant',
      timestamp,
      message: {
        model: 'test-model',
        usage: {
          cache_creation_input_tokens: 20,
          cache_creation: { ephemeral_1h_input_tokens: 30 },
        },
      },
    }),
  )!;
  expect(claude.cacheCreation1hTokens).toBe(20);
  const cache = new Map([
    [
      'file',
      {
        size: 1,
        mtimeMs: 1,
        provider: 'claude' as const,
        records: [claude],
        tailRecords: [],
        position: { resumeOffset: 1, guardLength: 0, guardHash: 0, codexState: null },
      },
    ],
  ]);
  const encoded = encodeScanCache(cache);
  expect(decodeScanCache(encoded).get('file')!.records).toEqual([claude]);
  const malformed = JSON.parse(JSON.stringify(encoded));
  malformed.files.file.r[0][10] = 21;
  expect(decodeScanCache(malformed).size).toBe(0);
});

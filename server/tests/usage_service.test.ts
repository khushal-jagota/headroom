/** Direct parity owns parsing. These gates prove shared work and cache ownership. */
import { afterEach, expect, test, vi } from 'vitest';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { setImmediate } from 'node:timers/promises';
import { Deferred } from '../conversation/runtime/state.ts';
import { UsageService } from '../conversation/usage/UsageService.ts';
import type { UsageSettings, UsageSummaryInput } from '../conversation/usage/contracts.ts';
import type { TranscriptParseResult } from '../conversation/usage/usageTranscriptReader.ts';
import { RATES_TIMEOUT_MS } from '../conversation/usage/configuration.ts';

const hooks = vi.hoisted(() => ({
  read: undefined as ((path: unknown) => Promise<Buffer> | undefined) | undefined,
  write: undefined as ((path: unknown, data: unknown) => Promise<void> | undefined) | undefined,
  parse: undefined as ((path: string) => Promise<TranscriptParseResult | null>) | undefined,
}));
vi.mock('node:fs/promises', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:fs/promises')>();
  return {
    ...actual,
    readFile: ((...args: Parameters<typeof actual.readFile>) =>
      hooks.read?.(args[0]) ?? actual.readFile(...args)) as typeof actual.readFile,
    writeFile: (...args: Parameters<typeof actual.writeFile>) =>
      hooks.write?.(args[0], args[1]) ?? actual.writeFile(...args),
  };
});
vi.mock('../conversation/usage/usageTranscriptReader.ts', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../conversation/usage/usageTranscriptReader.ts')>();
  return {
    ...actual,
    readTranscriptRecords: (...args: Parameters<typeof actual.readTranscriptRecords>) =>
      hooks.parse?.(args[0]) ?? actual.readTranscriptRecords(...args),
  };
});

const roots: string[] = [];
function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'panels-usage-service-'));
  roots.push(root);
  const state = join(root, 'state');
  const transcripts = join(root, 'claude');
  mkdirSync(state);
  mkdirSync(transcripts);
  writeFileSync(
    join(transcripts, 'session.jsonl'),
    JSON.stringify({
      type: 'assistant',
      timestamp: '2026-08-01T10:00:00Z',
      sessionId: 'session',
      requestId: 'request',
      message: {
        id: 'message',
        model: 'test-model',
        usage: { input_tokens: 10, output_tokens: 5 },
      },
    }) + '\n',
  );
  return { root, state, transcripts };
}
const window: UsageSummaryInput = {
  sinceDay: '2026-08-01' as UsageSummaryInput['sinceDay'],
  untilDay: '2026-08-02' as UsageSummaryInput['untilDay'],
  timeZone: 'UTC',
};
const now = Date.parse('2026-08-01T12:00:00Z');
const rates = { 'test-model': { input_cost_per_token: 0.01, output_cost_per_token: 0.02 } };
afterEach(() => {
  hooks.read = undefined;
  hooks.write = undefined;
  hooks.parse = undefined;
  vi.restoreAllMocks();
  vi.useRealTimers();
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

// Gap: sequential HTTP reads cannot prove that a departed caller leaves the shared scan owned.
test('identical readers share one scan and shutdown drains it after a caller leaves', async () => {
  const paths = fixture();
  const entered = new Deferred<void>();
  const release = new Deferred<void>();
  let fetches = 0;
  const service = new UsageService(paths.state, [['claude', paths.transcripts]], {
    now: () => now,
    ratesFetcher: async () => {
      fetches++;
      entered.resolve();
      await release.promise;
      return rates;
    },
  });
  const scan = vi.spyOn(service, 'scanSummary');
  const first = service.readSummary(window);
  const second = service.readSummary(window);
  try {
    await entered.promise;
    const caller = new AbortController();
    const departed = Promise.race([
      first,
      new Promise<never>((_resolve, reject) =>
        caller.signal.addEventListener('abort', () => reject(caller.signal.reason), { once: true }),
      ),
    ]);
    caller.abort(new Error('Synthetic caller departure'));
    await expect(departed).rejects.toThrow('Synthetic caller departure');
    let drained = false;
    const drain = service.finish_operations().then(() => {
      drained = true;
    });
    await setImmediate();
    expect(drained).toBe(false);
    expect(scan).toHaveBeenCalledTimes(1);
    expect(fetches).toBe(1);
    release.resolve();
    const summary = await second;
    await drain;
    expect(summary.buckets[0]?.totals.outputTokens).toBe(5);
    expect(drained).toBe(true);
  } finally {
    release.resolve();
    await Promise.allSettled([first, second]);
    await service.finish_operations();
  }
});

// Gap: separate scan keys can overlap initial cache load and persistence. A lost dirty
// marker can then persist only one source. Recording file boundaries make the race exact.
test('different scan keys share cache loading and serialize mutations with persistence', async () => {
  const paths = fixture();
  const firstPath = join(paths.root, 'alpha', 'session.jsonl');
  const secondPath = join(paths.root, 'beta', 'session.jsonl');
  let settings: UsageSettings = { usagePriceOverrides: {} };
  const service = new UsageService(paths.state, [], {
    now: () => now,
    ratesFetcher: () => rates,
    settingsProvider: () => settings,
  });
  const loadEntered = new Deferred<void>();
  const releaseLoad = new Deferred<void>();
  const writeEntered = new Deferred<void>();
  const releaseWrite = new Deferred<void>();
  const secondCollected = new Deferred<void>();
  const releaseSecond = new Deferred<void>();
  let cacheReads = 0;
  let cacheWrites = 0;
  const persistence: { document: { files: Record<string, unknown> } | null } = { document: null };
  const collected: string[] = [];
  hooks.read = (path) =>
    path === service.scanCachePath
      ? (async () => {
          cacheReads++;
          loadEntered.resolve();
          await releaseLoad.promise;
          return Buffer.from(JSON.stringify({ version: 3, models: [], sessions: [], files: {} }));
        })()
      : undefined;
  hooks.write = (path, data) =>
    path === service.scanCachePath
      ? (async () => {
          cacheWrites++;
          if (cacheWrites === 1) {
            writeEntered.resolve();
            await releaseWrite.promise;
          }
          persistence.document = JSON.parse(String(data)) as { files: Record<string, unknown> };
        })()
      : undefined;
  hooks.parse = async (path) => ({
    records: [
      {
        provider: 'claude',
        timestampMs: now,
        model: path === firstPath ? 'alpha' : 'beta',
        sessionId: path,
        totals: {
          uncachedInputTokens: 10,
          cachedInputTokens: 0,
          cacheCreationTokens: 0,
          outputTokens: 5,
          reasoningTokens: 0,
        },
        reportedCostUsd: null,
        dedupeKey: path,
      },
    ],
    tailRecords: [],
    position: { resumeOffset: 1, guardLength: 1, guardHash: 1, codexState: null },
    resumed: false,
  });
  vi.spyOn(service, 'collectDirs').mockImplementation(async (_start, current) => {
    const path = current.usagePriceOverrides?.beta ? secondPath : firstPath;
    collected.push(path);
    if (path === secondPath) {
      secondCollected.resolve();
      await releaseSecond.promise;
    }
    const records = await service.readFileRecords(path, 1, now, 'claude');
    return [
      { provider: 'claude', dir: dirname(path), volumeId: 'synthetic', files: [{ path, records }] },
    ];
  });
  const first = service.readSummary(window);
  let second: ReturnType<UsageService['readSummary']> | undefined;
  try {
    await loadEntered.promise;
    settings = {
      usagePriceOverrides: {
        beta: { inputCostPerMillionTokens: 1, outputCostPerMillionTokens: 1 },
      },
    };
    second = service.readSummary(window);
    await setImmediate();
    expect(cacheReads).toBe(1);
    expect(collected).toEqual([]);
    releaseLoad.resolve();
    await Promise.all([writeEntered.promise, secondCollected.promise]);
    releaseSecond.resolve();
    await setImmediate();
    expect(persistence.document).toBeNull();
    releaseWrite.resolve();
    await Promise.all([first, second]);
    expect(persistence.document).not.toBeNull();
    expect(Object.keys(persistence.document!.files).sort()).toEqual([firstPath, secondPath].sort());
    expect(service.cacheDirty).toBe(false);
  } finally {
    releaseLoad.resolve();
    releaseSecond.resolve();
    releaseWrite.resolve();
    await Promise.allSettled([first, ...(second ? [second] : [])]);
    await service.finish_operations();
  }
});

// Gap: a recording/custom fetcher can hang independently of the production HTTP timeout.
test('the service bounds an injected fetcher and releases retained work after timeout', async () => {
  const paths = fixture();
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  const entered = new Deferred<void>();
  const release = new Deferred<unknown>();
  let signal: AbortSignal | undefined;
  let calls = 0;
  const service = new UsageService(paths.state, [], {
    now: () => now,
    ratesFetcher: (options) => {
      calls++;
      signal = options?.signal;
      entered.resolve();
      return calls === 1 ? release.promise : rates;
    },
  });
  const refresh = service.refreshRates();
  try {
    await entered.promise;
    const drain = service.finish_operations();
    await vi.advanceTimersByTimeAsync(RATES_TIMEOUT_MS);
    expect((await refresh).status).toBe('unavailable');
    expect(signal?.aborted).toBe(true);
    await drain;
    expect((await service.refreshRates()).status).toBe('fresh');
    expect(calls).toBe(2);
  } finally {
    release.resolve(null);
    await service.finish_operations();
    vi.useRealTimers();
  }
});

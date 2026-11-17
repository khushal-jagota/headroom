/** Direct parity owns parsing. These gates prove shared work and cache ownership. */
import { afterEach, expect, test, vi } from 'vitest';
import { copyFileSync, mkdtempSync, mkdirSync, rmSync, utimesSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { setImmediate } from 'node:timers/promises';
import { Worker } from 'node:worker_threads';
import { Deferred } from '../core/deferred.ts';
import { UsageEngine as UsageService } from '../conversation/usage/UsageEngine.ts';
import { UsageService as WorkerUsageService } from '../conversation/usage/UsageService.ts';
import type {
  TranscriptDirectory,
  UsageSettings,
  UsageSummaryInput,
} from '../conversation/usage/contracts.ts';
import { UsageServiceStopped } from '../conversation/usage/contracts.ts';
import type { TranscriptParseResult } from '../conversation/usage/usageTranscriptReader.ts';
import { RATES_TIMEOUT_MS } from '../conversation/usage/configuration.ts';
import { timezone } from '../conversation/usage/timeZone.ts';
import claudeParser from '../drivers/claude/runtime/transcript_parser.ts';
import { claude_transcript_usage } from '../drivers/claude/runtime/transcript_usage.ts';
import { codex_transcript_usage } from '../drivers/codex/runtime/transcript_usage.ts';

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
/** A directory read with the named driver's own transcript parser. */
function directory(provider: 'claude' | 'codex', dir: string): TranscriptDirectory {
  const { kind, parser_module } = (
    provider === 'claude' ? claude_transcript_usage : codex_transcript_usage
  )(dir);
  return { provider, dir, kind, parser_module };
}
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

// Gap: per-file parser proofs cannot prove global suppression before date filtering.
test('a covered parent outside the window suppresses its copied compaction in either source order', async () => {
  const stamp = '2026-08-01T10:00:00Z';
  const usage = {
    input_tokens: 100,
    cached_input_tokens: 30,
    output_tokens: 20,
    total_tokens: 120,
  };
  const line = (type: string, payload: object) =>
    JSON.stringify({ type, timestamp: stamp, payload }) + '\n';
  const context =
    line('session_meta', { id: 'session' }) + line('turn_context', { model: 'test-model' });
  const response = line('token_usage_record', {
    response_id: 'response',
    usage,
    thread_token_usage: usage,
  });
  const marker = line('compacted', { compaction_response_id: 'response' });
  const count = line('event_msg', {
    type: 'token_count',
    info: { last_token_usage: usage, total_token_usage: usage },
  });
  const nextDay = { ...window, sinceDay: '2026-08-02' as UsageSummaryInput['sinceDay'] };
  for (const coveredFirst of [false, true]) {
    const paths = fixture();
    const parent = join(paths.root, 'parent');
    const copy = join(paths.root, 'copy');
    mkdirSync(parent);
    mkdirSync(copy);
    writeFileSync(join(parent, 'session.jsonl'), context + response + count + marker);
    writeFileSync(
      join(copy, 'session.jsonl'),
      (context + response + marker).replaceAll(stamp, '2026-08-02T10:00:00Z'),
    );
    // Each source has one file, so the source order fixes both traversal orders.
    const directories = coveredFirst ? [parent, copy] : [copy, parent];
    const options = { now: () => now, ratesFetcher: () => rates };
    const isolated = new UsageService(
      join(paths.root, 'copy-state'),
      [directory('codex', copy)],
      options,
    );
    const combined = new UsageService(
      paths.state,
      directories.map((dir) => directory('codex', dir)),
      options,
    );
    try {
      const control = await isolated.readSummary(nextDay);
      expect(control.buckets[0]?.totals.outputTokens).toBe(20);
      expect((await combined.readSummary(nextDay)).buckets).toEqual([]);
    } finally {
      await Promise.all([isolated.finish_operations(), combined.finish_operations()]);
    }
  }
});

// Gap: line sources reuse a file on size and mtime. Nothing proves that a whole-file source
// follows its fingerprint across a restart, or that a record's pricing name sets its price.
test('a whole-file source is read again only when its fingerprint changes', async () => {
  const paths = fixture();
  const files = join(paths.root, 'files');
  mkdirSync(files);
  const file = join(files, 'session.json');
  const write = (outputTokens: number, mtimeSeconds: number) => {
    const record = {
      provider: 'scripted',
      timestampMs: now - 60 * 60 * 1000,
      model: 'Scripted Display Name',
      pricing_model: 'test-model',
      sessionId: 'session',
      totals: {
        uncachedInputTokens: 10,
        cachedInputTokens: 0,
        cacheCreationTokens: 0,
        outputTokens,
        reasoningTokens: 0,
      },
      reportedCostUsd: null,
      dedupeKey: null,
    };
    writeFileSync(file, JSON.stringify([record]));
    utimesSync(file, mtimeSeconds, mtimeSeconds);
  };
  const source: TranscriptDirectory = {
    provider: 'scripted',
    dir: files,
    kind: 'files',
    parser_module: new URL('../conversation/usage/testing/files_parser.ts', import.meta.url).href,
  };
  // Each read is a fresh engine, so a skipped file can only come from the persisted cache.
  const read = async () => {
    const service = new UsageService(paths.state, [source], {
      now: () => now,
      ratesFetcher: () => rates,
    });
    try {
      const [bucket, ...rest] = (await service.readSummary(window)).buckets;
      expect(rest).toEqual([]);
      return bucket!;
    } finally {
      await service.finish_operations();
    }
  };
  const mtime = Date.now() / 1000;
  write(5, mtime);
  const first = await read();
  expect(first).toMatchObject({ provider: 'scripted', model: 'Scripted Display Name' });
  expect(first.costSource).toBe('modelPriced');
  expect(first.costUsd).toBeCloseTo(10 * 0.01 + 5 * 0.02);
  // The scripted fingerprint is the size: a same-length rewrite with a new mtime is stable.
  write(6, mtime + 10);
  const stable = await read();
  expect(stable.totals.outputTokens).toBe(5);
  expect(stable.costUsd).toBeCloseTo(first.costUsd);
  write(50, mtime + 20);
  expect((await read()).totals.outputTokens).toBe(50);
});

// Gap: ordinary IANA names cannot prove the accepted installed-but-ICU-unknown fallback.
test('an installed custom zone uses the accepted UTC fallback without admitting missing keys', async () => {
  const paths = fixture();
  const zones = join(paths.root, 'zones');
  mkdirSync(zones);
  // One small public fixed-offset file. Its +14 offset would move the fixture to August 2.
  copyFileSync('/usr/share/zoneinfo/Etc/GMT-14', join(zones, 'PanelsCustom'));
  vi.stubEnv('PYTHONTZPATH', zones);
  const service = new UsageService(paths.state, [directory('claude', paths.transcripts)], {
    now: () => now,
    ratesFetcher: () => rates,
  });
  try {
    expect(timezone('PanelsCustom')).toBe('UTC');
    expect(() => timezone('MissingCustom')).toThrow('Invalid usage time zone');
    const summary = await service.readSummary({ ...window, timeZone: 'PanelsCustom' });
    expect(summary.timeZone).toBe('PanelsCustom');
    expect(summary.buckets[0]?.day).toBe('2026-08-01');
  } finally {
    await service.finish_operations();
    vi.unstubAllEnvs();
  }
});

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
  const service = new UsageService(paths.state, [directory('claude', paths.transcripts)], {
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
    position: { resumeOffset: 1, guardLength: 1, guardHash: 1, state: null },
    resumed: false,
  });
  vi.spyOn(service, 'collectDirs').mockImplementation(async (_start, current) => {
    const path = current.usagePriceOverrides?.beta ? secondPath : firstPath;
    collected.push(path);
    if (path === secondPath) {
      secondCollected.resolve();
      await releaseSecond.promise;
    }
    const records = await service.readFileRecords(path, 1, now, 'claude', claudeParser);
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

// Gap: engine-only concurrency checks do not prove that shutdown owns the actual worker.
test('shutdown aborts the injected rate operation and joins the worker within the shared deadline', async () => {
  const paths = fixture();
  const entered = new Deferred<void>();
  const release = new Deferred<void>();
  const cleanupEntered = new Deferred<void>();
  let signal: AbortSignal | undefined;
  const service = new WorkerUsageService(paths.state, [], {
    ratesFetcher: async (options) => {
      signal = options?.signal;
      entered.resolve();
      await new Promise<void>((resolve) =>
        signal!.addEventListener('abort', () => resolve(), { once: true }),
      );
      cleanupEntered.resolve();
      await release.promise;
      return null;
    },
  });
  const result = service.refreshRates().then(
    () => null,
    (error: unknown) => error,
  );
  let stop: { deadline: number } | undefined;
  try {
    await entered.promise;
    const started = performance.now();
    let stopped = false;
    const stopping = service.stop((stop = { deadline: performance.now() / 1000 + 2 })).then(() => {
      stopped = true;
    });
    await cleanupEntered.promise;
    await setImmediate();
    expect(signal?.aborted).toBe(true);
    expect(stopped).toBe(false);
    release.resolve();
    await stopping;
    expect(await result).toBeInstanceOf(UsageServiceStopped);
    await service.finish_operations(stop);
    await expect(service.refreshRates()).rejects.toBeInstanceOf(UsageServiceStopped);
    await expect(service.readSummary(window)).rejects.toBeInstanceOf(UsageServiceStopped);
    console.info(JSON.stringify({ usage_worker_stop_ms: performance.now() - started }));
  } finally {
    release.resolve();
    await service.stop((stop ??= { deadline: performance.now() / 1000 + 2 }));
  }
});

// Gap: abort cleanup can ignore cancellation. Shutdown must report retained ownership.
test('an unsettled injected callback makes shutdown fail at its shared deadline', async () => {
  const paths = fixture();
  const entered = new Deferred<void>();
  const release = new Deferred<unknown>();
  const settled = new Deferred<void>();
  const service = new WorkerUsageService(paths.state, [], {
    ratesFetcher: async () => {
      entered.resolve();
      try {
        return await release.promise;
      } finally {
        settled.resolve();
      }
    },
  });
  const request = service.refreshRates().catch((error: unknown) => error);
  try {
    await entered.promise;
    await expect(service.stop({ deadline: performance.now() / 1000 + 0.05 })).rejects.toThrow(
      'Usage resources did not stop before the shutdown deadline.',
    );
    expect(await request).toBeInstanceOf(UsageServiceStopped);
  } finally {
    release.resolve(null);
    await settled.promise;
    await service.settled();
  }
});

// Gap: tiny transcript comparisons cannot detect a cache-size-dependent event-loop stall.
// The record-bearing cache is generated inside the worker, never cloned or written.
test('synthetic 30000-file cache serialization leaves the main event loop responsive', async () => {
  const cacheModule = new URL('../conversation/usage/usageScanCache.ts', import.meta.url).href;
  const worker = new Worker(
    `
    const { parentPort, workerData } = require('node:worker_threads');
    (async () => {
      const { encodeScanCache, decodeScanCache } = await import(workerData);
      parentPort.once('message', () => {
        const start = performance.now();
        const cache = new Map();
        for (let index = 0; index < 30000; index++) cache.set('/synthetic/' + index + '.jsonl', {
          size: 8000, mtimeMs: 1785585600000, provider: 'claude',
          records: Array.from({ length: 8 }, (_, slot) => ({
            provider: 'claude', timestampMs: 1785585600000 + slot,
            model: 'synthetic-model-' + (slot % 4), sessionId: 'session-' + index + '-' + slot,
            dedupeKey: 'request-' + index + '-' + slot, reportedCostUsd: null,
            totals: { uncachedInputTokens: 100, cachedInputTokens: 50,
              cacheCreationTokens: 5, outputTokens: 20, reasoningTokens: 0 }
          })), tailRecords: [],
          position: { resumeOffset: 0, guardLength: 0, guardHash: 0, state: null }
        });
        const constructed = performance.now();
        const bytes = JSON.stringify(encodeScanCache(cache));
        const encoded = performance.now();
        cache.clear();
        const decoded = decodeScanCache(JSON.parse(bytes));
        const finished = performance.now();
        let records = 0;
        for (const entry of decoded.values()) records += entry.records.length;
        parentPort.postMessage({ files: decoded.size, records, bytes: Buffer.byteLength(bytes),
          construction_ms: constructed - start, encode_ms: encoded - constructed,
          decode_ms: finished - encoded, elapsed_ms: finished - start });
      });
      parentPort.postMessage('ready');
    })().catch(error => { throw error; });
  `,
    { eval: true, workerData: cacheModule },
  );
  let ticks = 0;
  let largestGap = 0;
  let last = performance.now();
  let timer: ReturnType<typeof setInterval> | undefined;
  try {
    type Measurement = {
      files: number;
      records: number;
      bytes: number;
      construction_ms: number;
      encode_ms: number;
      decode_ms: number;
      elapsed_ms: number;
    };
    const measurement = await new Promise<Measurement>((resolve, reject) => {
      worker.on('error', reject);
      worker.on('message', (message: 'ready' | Measurement) => {
        if (message !== 'ready') {
          largestGap = Math.max(largestGap, performance.now() - last);
          resolve(message);
          return;
        }
        last = performance.now();
        timer = setInterval(() => {
          const current = performance.now();
          largestGap = Math.max(largestGap, current - last);
          last = current;
          ticks++;
        }, 1);
        worker.postMessage('run');
      });
    });
    expect(measurement.files).toBe(30000);
    expect(measurement.records).toBe(240000);
    expect(ticks).toBeGreaterThan(0);
    console.info(
      JSON.stringify({
        usage_cache_worker: measurement,
        main_loop_ticks: ticks,
        largest_tick_gap_ms: largestGap,
      }),
    );
  } finally {
    clearInterval(timer);
    await worker.terminate();
  }
}, 15000);

// One controlled line exercises the production worker, without any native provider files.
test('a multi-megabyte native line is parsed by the owned worker', async () => {
  const paths = fixture();
  const line = {
    type: 'assistant',
    timestamp: '2026-08-01T10:00:00Z',
    sessionId: 'synthetic-line',
    requestId: 'synthetic-request',
    message: {
      id: 'synthetic-message',
      model: 'test-model',
      usage: { input_tokens: 10, output_tokens: 5 },
      content: 'x'.repeat(2 * 1024 * 1024),
    },
  };
  writeFileSync(join(paths.transcripts, 'session.jsonl'), JSON.stringify(line) + '\n');
  const service = new WorkerUsageService(paths.state, [directory('claude', paths.transcripts)], {
    now: () => now,
    ratesFetcher: () => rates,
  });
  try {
    const summary = await service.readSummary(window);
    expect(summary.buckets[0]?.totals.outputTokens).toBe(5);
  } finally {
    await service.stop({ deadline: performance.now() / 1000 + 2 });
  }
});

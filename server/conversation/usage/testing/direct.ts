/** Export-only usage proof over caller-owned tiny transcripts and recorded rates. */
import { appendFile, readFile, realpath, stat, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import type { UsageProviderKind, UsageSettings, UsageSummaryInput } from '../contracts.ts';
import { parseClaudeLine, parseTimestampMs } from '../usageTranscripts.ts';
import { parseRateTable } from '../usagePricing.ts';
import {
  listTranscriptFiles,
  readDirectoryVolumeId,
  readTranscriptRecords,
} from '../usageTranscriptReader.ts';
import {
  decodeScanCache,
  dedupeWithinFile,
  encodeScanCache,
  pruneScanCache,
} from '../usageScanCache.ts';
import type { CachedFile, ScanCache } from '../usageScanCache.ts';
import { UsageService } from '../UsageService.ts';
import { makeUsageService, transcript_home } from '../composition.ts';
import { rates_proxy_for } from '../proxy.ts';
import { createUsageRoutes } from '../routes.ts';
import { BackendStateStore } from '../../backend_state.ts';
import { createApp } from '../../../app.ts';
import { HttpError } from '../../../core/http/errors.ts';
import { createDatabase } from '../../../core/database/index.ts';
import { loadConfig } from '../../../core/config.ts';
import { TestClock } from '../../../core/clock.ts';

interface Source {
  provider: UsageProviderKind;
  dir: string;
  fileName?: string;
}
interface ReaderSpec {
  homes: readonly { path: string; child: string }[];
  proxy_inputs: readonly { target: string; environment: Readonly<Record<string, string>> }[];
  sources: readonly Source[];
  selected: Readonly<Record<UsageProviderKind | 'fork', string>>;
  composition_db_link: string;
  fork_append_hex: string;
  since_ms: number;
  mtime_ms: number;
  append_hex: string;
  invalid_claude: string;
}
export async function compare_reader(spec: ReaderSpec) {
  const inventory = [];
  for (const source of spec.sources)
    inventory.push({
      provider: source.provider,
      files: await listTranscriptFiles(
        source.dir,
        spec.since_ms,
        source.fileName ? { fileName: source.fileName } : undefined,
      ),
      volume: await readDirectoryVolumeId(source.dir),
    });
  const codex = spec.selected.codex;
  const cold = (await readTranscriptRecords(codex, 'codex'))!;
  await appendFile(codex, Buffer.from(spec.append_hex, 'hex'));
  const resumed = (await readTranscriptRecords(codex, 'codex', cold.position))!;
  const rewritten = await readFile(codex);
  rewritten[resumed.position.resumeOffset - 8] = 0x20;
  await writeFile(codex, rewritten);
  const rejected_guard = (await readTranscriptRecords(codex, 'codex', resumed.position))!;
  const forkPath = spec.selected.fork;
  const forkCold = (await readTranscriptRecords(forkPath, 'codex'))!;
  const forkEncoded = encodeScanCache(
    new Map([
      [
        forkPath,
        {
          size: (await stat(forkPath)).size,
          mtimeMs: spec.mtime_ms,
          provider: 'codex' as const,
          records: forkCold.records,
          tailRecords: forkCold.tailRecords,
          position: forkCold.position,
        },
      ],
    ]),
  );
  await appendFile(forkPath, Buffer.from(spec.fork_append_hex, 'hex'));
  const forkResumed = await readTranscriptRecords(
    forkPath,
    'codex',
    decodeScanCache(forkEncoded).get(forkPath)!.position,
  );
  const compositionService = makeUsageService(
    loadConfig(undefined, {
      PLAN_TEST_MODE: '1',
      PLAN_DB_PATH: spec.composition_db_link,
    }),
    { ratesFetcher: () => null, now: () => new Date('2026-11-12T12:00:00Z') },
  );
  const directoryExists = async (path: string) =>
    (await stat(path).catch(() => null))?.isDirectory() ?? false;
  const composition = {
    state_dir: compositionService.stateDir,
    target_created: await directoryExists(
      join(dirname(await realpath(spec.composition_db_link)), 'usage'),
    ),
    lexical_created: await directoryExists(join(dirname(spec.composition_db_link), 'usage')),
  };
  await compositionService.finish_operations();
  const cache: ScanCache = new Map();
  for (const provider of ['claude', 'codex', 'grok'] as const) {
    const path = spec.selected[provider];
    const parsed = (await readTranscriptRecords(path, provider))!;
    const seen = new Set<string>();
    const entry: CachedFile = {
      size: (await readFile(path)).length,
      mtimeMs: spec.mtime_ms,
      provider,
      records: dedupeWithinFile(parsed.records, seen),
      tailRecords: dedupeWithinFile(parsed.tailRecords, seen),
      position: parsed.position,
    };
    cache.set(path, entry);
  }
  const encoded = encodeScanCache(cache);
  const decoded = decodeScanCache(encoded);
  const prune = new Map(decoded);
  const example = prune.values().next().value!;
  prune.set(spec.sources[0]!.dir + '/aged.jsonl', { ...example, mtimeMs: 0 });
  prune.set(spec.sources[0]!.dir + '-unwalked/retained.jsonl', { ...example });
  const removed = pruneScanCache(prune, {
    livePaths: new Set([spec.selected.claude, spec.selected.grok]),
    walkedRoots: spec.sources.map((source) => source.dir),
    windowStartMs: spec.since_ms,
    retentionCutoffMs: spec.since_ms - 90 * 86400000,
  });
  return {
    inventory,
    fork: { cold: forkCold, encoded: forkEncoded, resumed: forkResumed },
    composition,
    home_paths: spec.homes.map((value) => transcript_home(value.path, value.child)),
    proxy_choices: spec.proxy_inputs.map((value) =>
      rates_proxy_for(value.target, value.environment),
    ),
    cold,
    resumed,
    rejected_guard,
    encoded,
    decoded: Object.fromEntries(decoded),
    pruning: { removed, paths: [...prune.keys()] },
    bad_version_entries: decodeScanCache({ ...encoded, version: 12 }).size,
    invalid_timestamp: parseTimestampMs('2026-02-30T12:00:00Z'),
    invalid_claude: parseClaudeLine(spec.invalid_claude),
    array_rate_models: [
      ...parseRateTable([{ input_cost_per_token: 1, output_cost_per_token: 2 }]).keys(),
    ],
  };
}

interface Operation {
  name: string;
  method: 'GET' | 'POST';
  path: string;
  advance_ms?: number;
  settings?: UsageSettings;
  restart?: boolean;
  corrupt_cache?: boolean;
  runtime_available?: boolean;
}
interface HttpSpec {
  db_path: string;
  state_dir: string;
  now: string;
  now_ms: number;
  sources: readonly Source[];
  rates: readonly unknown[];
  operations: readonly Operation[];
}
export async function compare_http(spec: HttpSpec) {
  const database = createDatabase(spec.db_path);
  let now = spec.now_ms;
  let settings: UsageSettings = {};
  let runtimeAvailable = true;
  const calls: { now: number; result: number }[] = [];
  const services: UsageService[] = [];
  const make = () => {
    const service = new UsageService(spec.state_dir, spec.sources, {
      ratesFetcher: () => {
        const result = calls.length;
        calls.push({ now, result });
        return spec.rates[result] ?? null;
      },
      settingsProvider: () => settings,
      now: () => now,
    });
    services.push(service);
    return service;
  };
  let current = make();
  const app = await createApp(
    loadConfig(undefined, {
      PLAN_TEST_MODE: '1',
      PLAN_DB_PATH: spec.db_path,
    }),
    {
      database,
      clock: new TestClock(spec.now),
      areas: [
        {
          prefix: '/api/conversation/usage',
          routes: createUsageRoutes({
            get conversation_runtime_available() {
              return runtimeAvailable;
            },
            recorded_usage: {
              readSummary: (input: UsageSummaryInput) => current.readSummary(input),
              refreshRates: () => current.refreshRates(),
              finish_operations: () => current.finish_operations(),
            },
            backend_state: new BackendStateStore(database),
          }),
        },
      ],
    },
  );
  app.addHook('onError', async (_request, _reply, error) => {
    if (!(error instanceof HttpError)) console.error('Usage direct route error:', error);
  });
  try {
    const answers = [];
    for (const operation of spec.operations) {
      runtimeAvailable = operation.runtime_available ?? true;
      now += operation.advance_ms ?? 0;
      if (operation.settings !== undefined) settings = operation.settings;
      if (operation.corrupt_cache) {
        for (const path of [current.ratesCachePath, current.scanCachePath]) {
          let original = await readFile(path);
          if (path === current.ratesCachePath) {
            const document = JSON.parse(original.toString('utf8')) as { fetchedAtMs: number };
            document.fetchedAtMs = now;
            original = Buffer.from(JSON.stringify(document));
          }
          const index = original.indexOf(Buffer.from('gpt-6.1'));
          if (index < 0) throw new Error('Expected recorded model in tiny cache fixture');
          original[index] = 0xff;
          await writeFile(path, original);
        }
      }
      if (operation.restart) {
        await current.stop({ deadline: performance.now() / 1000 + 2 });
        current = make();
      }
      const response = await app.inject({ method: operation.method, url: operation.path });
      if (response.statusCode === 500)
        throw new Error(`${operation.name}: ${response.statusCode} ${response.body}`);
      answers.push({
        name: operation.name,
        status: response.statusCode,
        body: response.json() as unknown,
        rates_calls: calls.length,
      });
    }
    return {
      answers,
      calls,
      scan_cache: JSON.parse(await readFile(current.scanCachePath, 'utf8')) as unknown,
      rates_cache: JSON.parse(await readFile(current.ratesCachePath, 'utf8')) as unknown,
      rows: database.read((sql) =>
        Object.fromEntries(
          ['backend_usage_snapshots', 'backend_usage_windows'].map((table) => [
            table,
            sql.all(`SELECT * FROM ${table} ORDER BY rowid`),
          ]),
        ),
      ),
    };
  } finally {
    for (const service of services) await service.stop({ deadline: performance.now() / 1000 + 2 });
    await app.close();
    database.close();
  }
}

/**
 * UsageService adapted from T3 75e4ceb96489a2fe92c710c79dbea59778cccfd1.
 * Copyright (c) 2026 T3 Tools Inc. MIT. See T3-LICENSE.
 * Effect construction becomes injected asynchronous dependencies with Python's cache policy.
 */
import { access, readFile, writeFile } from 'node:fs/promises';
import { hostname } from 'node:os';
import { join } from 'node:path';
import { FifoLock } from '../runtime/state.ts';
import { USAGE_CONTRACT_VERSION, UsageReadError } from './contracts.ts';
import type {
  RecordedUsageService,
  TranscriptDirectories,
  TranscriptDirectory,
  UsagePricing,
  UsageProviderKind,
  UsageServiceOptions,
  UsageSettings,
  UsageSource,
  UsageSummary,
  UsageSummaryInput,
} from './contracts.ts';
import {
  CACHE_RETENTION_DAYS,
  LITELLM_RATES_URL,
  MAX_HOURLY_WINDOW_MS,
  MTIME_SLACK_MS,
  RATES_REFRESH_FLOOR_MS,
  RATES_TIMEOUT_MS,
  RATES_TTL_MS,
} from './configuration.ts';
import { UsageAggregator, isoTimestamp } from './usageAggregation.ts';
import { createOverrideRateTable, parseRateTable } from './usagePricing.ts';
import type { RateTable } from './usagePricing.ts';
import {
  decodeScanCache,
  dedupeWithinFile,
  encodeScanCache,
  pruneScanCache,
} from './usageScanCache.ts';
import type { ScanCache } from './usageScanCache.ts';
import {
  listTranscriptFiles,
  readDirectoryVolumeId,
  readTranscriptRecords,
} from './usageTranscriptReader.ts';
import { finiteNumber, parseTimestampMs } from './usageTranscripts.ts';
import type { UsageRecord } from './usageTranscripts.ts';
import { fetchRates } from './http.ts';

interface ScannedDirectory {
  readonly provider: UsageProviderKind;
  readonly dir: string;
  readonly volumeId: string;
  readonly files: readonly { path: string; records: readonly UsageRecord[] }[] | null;
}
const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
function decodeCacheDocument(raw: Uint8Array): unknown {
  // Path.read_text rejects invalid UTF-8 and leaves a text BOM for json.loads to reject.
  const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw);
  return JSON.parse(text);
}

export class UsageService implements RecordedUsageService {
  readonly stateDir: string;
  readonly transcriptDirs: TranscriptDirectories;
  readonly ratesCachePath: string;
  readonly scanCachePath: string;
  readonly fileCache: ScanCache = new Map();
  cacheDirty = false;
  rates: RateTable = new Map();
  ratesFetchedAtMs: number | null = null;
  ratesStatus: UsagePricing['status'] = 'unavailable';
  private readonly options: UsageServiceOptions;
  private readonly now: () => number;
  private readonly ratesLock = new FifoLock();
  private readonly cacheLock = new FifoLock();
  private scanCacheLoaded = false;
  private readonly inflightScans = new Map<string, Promise<UsageSummary>>();
  private readonly operations = new Set<Promise<unknown>>();

  constructor(
    stateDir: string,
    transcriptDirs: TranscriptDirectories,
    options: UsageServiceOptions = {},
  ) {
    this.stateDir = stateDir;
    this.transcriptDirs = transcriptDirs;
    this.options = options;
    this.now = options.now ?? (() => Date.now());
    this.ratesCachePath = join(stateDir, 'usage-model-rates.json');
    this.scanCachePath = join(stateDir, 'usage-scan-cache.json');
  }

  pricing(): UsagePricing {
    return {
      status: this.ratesStatus,
      source: LITELLM_RATES_URL,
      fetchedAt: this.ratesFetchedAtMs === null ? null : isoTimestamp(this.ratesFetchedAtMs),
      knownModels: this.rates.size,
    } as UsagePricing;
  }

  private retain<T>(task: Promise<T>): Promise<T> {
    this.operations.add(task);
    void task.then(
      () => this.operations.delete(task),
      () => this.operations.delete(task),
    );
    return task;
  }

  async finish_operations(): Promise<void> {
    while (this.operations.size !== 0) await Promise.allSettled([...this.operations]);
  }

  private async readRates(): Promise<unknown> {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const fetched = Promise.resolve().then(() =>
        (this.options.ratesFetcher ?? fetchRates)({ signal: controller.signal }),
      );
      return (
        (await Promise.race([
          fetched,
          new Promise<never>((_resolve, reject) => {
            timer = setTimeout(() => {
              const error = new Error('Usage rates did not answer in time.');
              controller.abort(error);
              reject(error);
            }, RATES_TIMEOUT_MS);
          }),
        ])) ?? null
      );
    } catch {
      return null;
    } finally {
      clearTimeout(timer);
    }
  }

  async loadRates(force: boolean): Promise<void> {
    const now = this.now();
    const maximumAge = force ? RATES_REFRESH_FLOOR_MS : RATES_TTL_MS;
    if (this.ratesFetchedAtMs !== null && now - this.ratesFetchedAtMs < maximumAge) return;
    if (this.ratesFetchedAtMs === null) {
      let document: unknown;
      try {
        document = decodeCacheDocument(await readFile(this.ratesCachePath));
      } catch {
        document = null;
      }
      if (object(document) && finiteNumber(document.fetchedAtMs) && 'document' in document) {
        const parsed = parseRateTable(document.document);
        if (parsed.size !== 0) {
          this.rates = parsed;
          this.ratesFetchedAtMs = document.fetchedAtMs as number;
          this.ratesStatus = 'cached';
          if (now - this.ratesFetchedAtMs < maximumAge) return;
        }
      }
    }
    const fetched = await this.readRates();
    if (fetched === null) {
      if (this.rates.size !== 0) this.ratesStatus = 'cached';
      return;
    }
    const parsed = parseRateTable(fetched);
    if (parsed.size === 0) return;
    this.rates = parsed;
    this.ratesFetchedAtMs = now;
    this.ratesStatus = 'fresh';
    try {
      await writeFile(this.ratesCachePath, JSON.stringify({ fetchedAtMs: now, document: fetched }));
    } catch {
      // An unwritable cache does not invalidate a successful observation.
    }
  }

  async ensureRates(force: boolean): Promise<void> {
    await this.ratesLock.run(() => this.loadRates(force));
  }

  refreshRates(): Promise<UsagePricing> {
    return this.retain(
      Promise.resolve().then(async () => {
        await this.ensureRates(true);
        return this.pricing();
      }),
    );
  }

  async ensureScanCacheLoaded(): Promise<void> {
    await this.cacheLock.run(async () => {
      if (this.scanCacheLoaded) return;
      let document: unknown;
      try {
        document = decodeCacheDocument(await readFile(this.scanCachePath));
      } catch {
        document = null;
      }
      if (document !== null)
        for (const [path, entry] of decodeScanCache(document)) this.fileCache.set(path, entry);
      this.scanCacheLoaded = true;
    });
  }

  async persistScanCache(): Promise<void> {
    await this.cacheLock.run(async () => {
      if (!this.cacheDirty) return;
      try {
        // The cache lock spans serialization and persistence. A concurrent mutation
        // cannot lose its dirty marker while this asynchronous write completes.
        await writeFile(this.scanCachePath, JSON.stringify(encodeScanCache(this.fileCache)));
        this.cacheDirty = false;
      } catch {
        // Keep the dirty marker so the next scan retries this write.
      }
    });
  }

  async readFileRecords(
    filePath: string,
    size: number,
    mtimeMs: number,
    provider: UsageProviderKind,
  ): Promise<readonly UsageRecord[]> {
    const cached = await this.cacheLock.run(() => this.fileCache.get(filePath));
    if (
      cached &&
      cached.size === size &&
      cached.mtimeMs === mtimeMs &&
      cached.provider === provider
    )
      return cached.tailRecords.length === 0
        ? cached.records
        : [...cached.records, ...cached.tailRecords];
    const resumeFrom =
      cached !== undefined && cached.provider === provider && size > cached.size
        ? cached.position
        : undefined;
    const parsed = await readTranscriptRecords(filePath, provider, resumeFrom);
    if (parsed === null) return [];
    const base = parsed.resumed && cached !== undefined ? cached.records : [];
    const seen = new Set<string>();
    const records = dedupeWithinFile([...base, ...parsed.records], seen);
    const tailRecords = dedupeWithinFile(parsed.tailRecords, seen);
    await this.cacheLock.run(() => {
      this.fileCache.set(filePath, {
        size,
        mtimeMs,
        provider,
        records,
        tailRecords,
        position: parsed.position,
      });
      this.cacheDirty = true;
    });
    return tailRecords.length === 0 ? records : [...records, ...tailRecords];
  }

  async collectDirs(
    windowStartMs: number,
    settings: UsageSettings,
  ): Promise<readonly ScannedDirectory[]> {
    const raw =
      typeof this.transcriptDirs === 'function'
        ? this.transcriptDirs(settings)
        : this.transcriptDirs;
    const directories = raw.map((entry): TranscriptDirectory =>
      Array.isArray(entry) ? { provider: entry[0], dir: entry[1] } : (entry as TranscriptDirectory),
    );
    const scanned: ScannedDirectory[] = [];
    for (const { provider, dir, fileName } of directories) {
      const volumeId = await readDirectoryVolumeId(dir);
      let exists = true;
      try {
        await access(dir);
      } catch {
        exists = false;
      }
      if (!exists) {
        scanned.push({ provider, dir, volumeId, files: null });
        continue;
      }
      const files = await listTranscriptFiles(
        dir,
        windowStartMs,
        fileName === undefined ? undefined : { fileName },
      );
      const parsedFiles = [];
      for (const file of files)
        parsedFiles.push({
          path: file.path,
          records: await this.readFileRecords(file.path, file.size, file.mtimeMs, provider),
        });
      scanned.push({ provider, dir, volumeId, files: parsedFiles });
    }
    return scanned;
  }

  async scanSummary(input: UsageSummaryInput, settings: UsageSettings): Promise<UsageSummary> {
    if (input.sinceDay > input.untilDay)
      throw new UsageReadError(
        'invalidWindow',
        `sinceDay '${input.sinceDay}' is after untilDay '${input.untilDay}'`,
      );
    let hourlyWindow: { sinceTimeMs: number; untilTimeMs: number } | undefined;
    if (input.resolution === 'hour') {
      const sinceTimeMs = parseTimestampMs(input.sinceTime);
      const untilTimeMs = parseTimestampMs(input.untilTime);
      if (sinceTimeMs === null || untilTimeMs === null)
        throw new UsageReadError(
          'invalidWindow',
          'Hourly usage requires valid sinceTime and untilTime instants',
        );
      const duration = untilTimeMs - sinceTimeMs;
      if (duration <= 0 || duration > MAX_HOURLY_WINDOW_MS)
        throw new UsageReadError(
          'invalidWindow',
          'Hourly usage window must be greater than zero and at most 24 hours',
        );
      hourlyWindow = { sinceTimeMs, untilTimeMs };
    }
    const startedAtMs = this.now();
    await this.ensureScanCacheLoaded();
    const hostId = hostname();
    const windowStart = parseTimestampMs(`${input.sinceDay}T00:00:00Z`);
    if (windowStart === null)
      throw new UsageReadError('invalidWindow', `sinceDay '${input.sinceDay}' is not a valid date`);
    const windowStartMs = (hourlyWindow?.sinceTimeMs ?? windowStart) - MTIME_SLACK_MS;
    const [, scannedDirs] = await Promise.all([
      this.ensureRates(false),
      this.collectDirs(windowStartMs, settings),
    ]);
    const aggregator = new UsageAggregator({
      timeZone: input.timeZone,
      sinceDay: input.sinceDay,
      untilDay: input.untilDay,
      resolution: input.resolution ?? 'day',
      ...hourlyWindow,
      rates: this.rates,
      priceOverrides: createOverrideRateTable(settings.usagePriceOverrides ?? {}),
    });
    const sources: UsageSource[] = [];
    const livePaths = new Set<string>();
    const walkedRoots: string[] = [];
    for (const { provider, dir, volumeId, files } of scannedDirs) {
      const fingerprint = { hostId, provider, resolvedHomePath: dir, volumeId };
      if (files === null) {
        sources.push({
          fingerprint,
          status: 'missing',
          scannedFiles: 0,
          skippedFiles: 0,
          malformedRecords: 0,
          distinctSessions: 0,
          message: 'No transcript directory on this environment.',
        } as UsageSource);
        continue;
      }
      walkedRoots.push(dir);
      let scannedFiles = 0;
      let skippedFiles = 0;
      const sessionIds = new Set<string>();
      for (const file of files) {
        livePaths.add(file.path);
        if (file.records.length === 0) {
          skippedFiles += 1;
          continue;
        }
        scannedFiles += 1;
        for (const record of file.records)
          if (aggregator.add(record) && record.sessionId) sessionIds.add(record.sessionId);
      }
      sources.push({
        fingerprint,
        status: 'ok',
        scannedFiles,
        skippedFiles,
        malformedRecords: 0,
        distinctSessions: sessionIds.size,
        message: null,
      } as UsageSource);
    }
    await this.cacheLock.run(() => {
      const pruned = pruneScanCache(this.fileCache, {
        livePaths,
        walkedRoots,
        windowStartMs,
        retentionCutoffMs: startedAtMs - CACHE_RETENTION_DAYS * 24 * 60 * 60 * 1000,
      });
      if (pruned > 0) this.cacheDirty = true;
    });
    await this.persistScanCache();
    const aggregated = aggregator.finish();
    const readAt = isoTimestamp(this.now());
    const finishedAtMs = this.now();
    return {
      contractVersion: USAGE_CONTRACT_VERSION,
      readAt,
      timeZone: input.timeZone,
      sinceDay: input.sinceDay,
      untilDay: input.untilDay,
      buckets: aggregated.buckets,
      sources,
      pricing: this.pricing(),
      scanDurationMs: Math.max(0, Math.trunc(finishedAtMs - startedAtMs)),
    } as UsageSummary;
  }

  readSummary(input: UsageSummaryInput): Promise<UsageSummary> {
    let settings: UsageSettings;
    try {
      settings = (this.options.settingsProvider ?? (() => ({ usagePriceOverrides: {} })))();
    } catch {
      return Promise.reject(new UsageReadError('scanFailed', 'Server settings could not be read.'));
    }
    const key = JSON.stringify([
      input.timeZone,
      input.sinceDay,
      input.untilDay,
      input.resolution ?? 'day',
      input.sinceTime ?? null,
      input.untilTime ?? null,
      settings.usagePriceOverrides ?? {},
    ]);
    const existing = this.inflightScans.get(key);
    if (existing !== undefined) return existing;
    // Enroll before any await. A departed HTTP caller cannot cancel the owned scan.
    const task = Promise.resolve().then(() => this.scanSummary(input, settings));
    this.inflightScans.set(key, task);
    this.retain(task);
    void task.then(
      () => this.inflightScans.delete(key),
      () => this.inflightScans.delete(key),
    );
    return task;
  }
}

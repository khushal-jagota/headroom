/** Request/lifecycle facade. The worker owns every transcript record and cache byte. */
import { Worker } from 'node:worker_threads';
import { join } from 'node:path';
import { UsageReadError, UsageServiceStopped } from './contracts.ts';
import type {
  RecordedUsageService,
  TranscriptDirectories,
  TranscriptDirectory,
  UsagePricing,
  UsageServiceOptions,
  UsageSummary,
  UsageSummaryInput,
} from './contracts.ts';
import type { UsageWorkerRequest, UsageWorkerResponse } from './workerProtocol.ts';
import type { StopOptions } from '../../lifecycle/contracts.ts';
import { observe_shutdown } from '../shutdown.ts';

/** File-backed workers inherit runtime switches, never the parent's eval entry. */
function workerExecArgv(): string[] {
  const retained: string[] = [];
  const entryWithValue = new Set(['--input-type', '--eval', '-e', '--print', '-p', '-pe', '-ep']);
  for (let index = 0; index < process.execArgv.length; index++) {
    const argument = process.execArgv[index]!;
    if (entryWithValue.has(argument)) {
      index++;
      continue;
    }
    if (/^--(?:input-type|eval|print)=/.test(argument) || /^-[ep].+/.test(argument)) continue;
    retained.push(argument);
  }
  return retained;
}

export class UsageService implements RecordedUsageService {
  readonly stateDir: string;
  readonly transcriptDirs: TranscriptDirectories;
  private readonly options: UsageServiceOptions;
  readonly ratesCachePath: string;
  readonly scanCachePath: string;
  private worker: Worker | undefined;
  private sequence = 0;
  private closed = false;
  private stopCleanup: Promise<void> | undefined;
  private resourceJoin: Promise<void> | undefined;
  private workerExit: Promise<void> | undefined;
  private readonly operations = new Set<Promise<unknown>>();
  private readonly pending = new Map<
    number,
    { resolve(value: UsageSummary | UsagePricing): void; reject(error: Error): void }
  >();
  private readonly callbacks = new Map<number, AbortController>();
  // Abort notification does not settle a provider's asynchronous cleanup.
  private readonly callbackOperations = new Set<Promise<unknown>>();
  constructor(
    stateDir: string,
    transcriptDirs: TranscriptDirectories,
    options: UsageServiceOptions = {},
  ) {
    this.stateDir = stateDir;
    this.transcriptDirs = transcriptDirs;
    this.options = options;
    this.ratesCachePath = join(stateDir, 'usage-model-rates.json');
    this.scanCachePath = join(stateDir, 'usage-scan-cache.json');
  }
  private fail(error: Error): void {
    for (const pending of this.pending.values()) pending.reject(error);
    this.pending.clear();
    for (const controller of this.callbacks.values()) controller.abort(error);
    this.callbacks.clear();
  }
  private start(): Worker {
    if (this.closed) throw new UsageServiceStopped();
    if (this.worker) return this.worker;
    const worker = new Worker(new URL('./usageWorker.ts', import.meta.url), {
      execArgv: workerExecArgv(),
      workerData: {
        stateDir: this.stateDir,
        injectedRates: this.options.ratesFetcher !== undefined,
        injectedClock: this.options.now !== undefined,
      },
    });
    this.worker = worker;
    this.workerExit = new Promise<void>((resolve) => worker.once('exit', () => resolve()));
    worker.on('message', (message: UsageWorkerResponse) => this.receive(worker, message));
    worker.on('error', (error) => {
      if (this.worker === worker) this.fail(error);
    });
    worker.on('exit', (code) => {
      if (this.worker !== worker) return;
      this.worker = undefined;
      this.fail(new UsageReadError('scanFailed', `Usage worker exited (${code}).`));
    });
    return worker;
  }
  private receive(worker: Worker, message: UsageWorkerResponse): void {
    if (message.kind === 'cancel-callback') {
      this.callbacks.get(message.id)?.abort(new Error('Usage rates request ended.'));
      this.callbacks.delete(message.id);
      return;
    }
    if (message.kind === 'callback') {
      if (this.closed) return;
      const controller = new AbortController();
      this.callbacks.set(message.id, controller);
      const value = Promise.resolve().then(() =>
        message.callback === 'clock'
          ? (this.options.now ?? Date.now)()
          : this.options.ratesFetcher!({ signal: controller.signal }),
      );
      this.callbackOperations.add(value);
      void value.then(
        () => this.callbackOperations.delete(value),
        () => this.callbackOperations.delete(value),
      );
      const reply = (
        result: Omit<Extract<UsageWorkerRequest, { kind: 'callback-result' }>, 'kind' | 'id'>,
      ) => {
        if (this.worker !== worker || !this.callbacks.delete(message.id) || this.closed) return;
        try {
          worker.postMessage({
            kind: 'callback-result',
            id: message.id,
            ...result,
          } satisfies UsageWorkerRequest);
        } catch (error) {
          worker.postMessage({
            kind: 'callback-result',
            id: message.id,
            error: { name: 'Error', message: String(error) },
          } satisfies UsageWorkerRequest);
        }
      };
      void value.then(
        (result) => reply({ value: result }),
        (error: unknown) => reply({ error: { name: 'Error', message: String(error) } }),
      );
      return;
    }
    const pending = this.pending.get(message.id);
    this.pending.delete(message.id);
    if (message.kind === 'result') pending?.resolve(message.value);
    else {
      const body = message.error;
      const error =
        body.reason !== undefined && body.detail !== undefined
          ? new UsageReadError(body.reason, body.detail)
          : new Error(body.message);
      error.name = body.name;
      pending?.reject(error);
    }
    if (this.pending.size === 0) worker.unref();
  }
  private request<T extends UsageSummary | UsagePricing>(message: UsageWorkerRequest): Promise<T> {
    const task = new Promise<T>((resolve, reject) => {
      try {
        const worker = this.start();
        worker.ref();
        this.pending.set(message.id, { resolve: (value) => resolve(value as T), reject });
        worker.postMessage(message);
      } catch (error) {
        this.pending.delete(message.id);
        if (this.pending.size === 0) this.worker?.unref();
        reject(error);
      }
    });
    this.operations.add(task);
    void task.then(
      () => this.operations.delete(task),
      () => this.operations.delete(task),
    );
    return task;
  }
  readSummary(input: UsageSummaryInput): Promise<UsageSummary> {
    if (this.closed) return Promise.reject(new UsageServiceStopped());
    try {
      const settings = (this.options.settingsProvider ?? (() => ({ usagePriceOverrides: {} })))();
      const raw =
        typeof this.transcriptDirs === 'function'
          ? this.transcriptDirs(settings)
          : this.transcriptDirs;
      const directories = raw.map((entry): TranscriptDirectory =>
        Array.isArray(entry)
          ? { provider: entry[0], dir: entry[1] }
          : (entry as TranscriptDirectory),
      );
      return this.request({ kind: 'summary', id: ++this.sequence, input, settings, directories });
    } catch {
      return Promise.reject(new UsageReadError('scanFailed', 'Server settings could not be read.'));
    }
  }
  refreshRates(): Promise<UsagePricing> {
    return this.request({ kind: 'refresh', id: ++this.sequence });
  }
  private async finish_current_operations(): Promise<void> {
    while (this.operations.size !== 0) await Promise.allSettled([...this.operations]);
    if (this.closed) await this.resourceJoin;
  }
  finish_operations(options: StopOptions): Promise<void> {
    return observe_shutdown(
      this.finish_current_operations(),
      options,
      'Usage operations remain unfinished at the shutdown deadline.',
    );
  }
  async settled(): Promise<void> {
    this.worker?.ref();
    await this.finish_current_operations();
    while (this.callbackOperations.size !== 0)
      await Promise.allSettled([...this.callbackOperations]);
    await (this.resourceJoin ?? this.workerExit);
  }
  stop(options: StopOptions): Promise<void> {
    if (this.stopCleanup !== undefined) return this.stopCleanup;
    this.closed = true;
    const worker = this.worker;
    // Publish both lifetimes before abort callbacks can synchronously request stop.
    let resolve!: () => void, reject!: (error: unknown) => void;
    this.resourceJoin = new Promise<void>((yes, no) => {
      resolve = yes;
      reject = no;
    });
    this.stopCleanup = observe_shutdown(
      this.resourceJoin,
      options,
      'Usage resources did not stop before the shutdown deadline.',
    );
    const failures: unknown[] = [];
    try {
      this.fail(new UsageServiceStopped());
    } catch (error) {
      failures.push(error);
    }
    // Request cancellation is not worker exit or asynchronous callback cleanup.
    Promise.allSettled([
      Promise.resolve().then(() => worker?.terminate()),
      this.workerExit,
      ...this.callbackOperations,
      ...this.operations,
    ]).then((results) => {
      const termination = results[0];
      if (termination?.status === 'rejected') failures.push(termination.reason);
      if (failures.length)
        reject(new AggregateError(failures, 'Usage resources did not close cleanly'));
      else resolve();
    });
    return this.stopCleanup;
  }
}

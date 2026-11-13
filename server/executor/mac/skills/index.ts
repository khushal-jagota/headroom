/** The runner owns this publisher until actual worker exit, including late cleanup. */
import { Worker } from 'node:worker_threads';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import { BackendSpawnFailed } from '../../../conversation/backends/contracts.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import type { CanonicalSkillSnapshot } from '../../wire.generated.ts';
import type { SkillInstallation, SkillReply, SkillRequest } from './contracts.ts';
export class MacSkillsPublisher {
  private readonly on_failure: (error: Error) => void;
  private worker: Worker | undefined;
  private closed = false;
  private stop_sent = false;
  private failure: Error | undefined;
  private sequence = 0;
  private tail: Promise<void> = Promise.resolve();
  private pending:
    { id: number; resolve: (value: string) => void; reject: (error: Error) => void } | undefined;
  private resolve_actual!: () => void;
  private reject_actual!: (error: Error) => void;
  private readonly actual = new Promise<void>((resolve, reject) => {
    this.resolve_actual = resolve;
    this.reject_actual = reject;
  });
  constructor(on_failure: (error: Error) => void) {
    this.on_failure = on_failure;
    void this.actual.catch(() => {});
  }
  install_native_skill_snapshot(
    snapshot: CanonicalSkillSnapshot,
    options: { root: string; native_home: string },
  ): Promise<string> {
    if (this.closed)
      return Promise.reject(
        this.failure ?? new ServerLifecycleError('Skill publication is stopped.'),
      );
    // Snapshot belongs to this admitted operation, even if the caller later mutates its object.
    const input: SkillInstallation = { snapshot: structuredClone(snapshot), ...options };
    const task = this.tail.then(async () => {
      if (this.closed)
        throw this.failure ?? new ServerLifecycleError('Skill publication is stopped.');
      let worker: Worker;
      try {
        worker = this.ensure_worker();
      } catch (error) {
        this.fault(error);
        throw error;
      }
      return new Promise<string>((resolve, reject) => {
        const id = ++this.sequence;
        this.pending = { id, resolve, reject };
        try {
          worker.postMessage({ id, install: input } satisfies SkillRequest);
        } catch (error) {
          this.fault(error);
        }
      });
    });
    this.tail = task.then(
      () => {},
      () => {},
    );
    return task;
  }
  private ensure_worker(): Worker {
    if (this.worker) return this.worker;
    const worker = new Worker(new URL('./worker.ts', import.meta.url), { execArgv: [] });
    this.worker = worker;
    worker.on('message', (reply: SkillReply) => {
      if ('cleanup_error' in reply) {
        this.stop_sent = false;
        this.record_fault(new Error(reply.cleanup_error.message));
        return;
      }
      const pending = this.pending;
      if (!pending || reply.id !== pending.id) {
        this.fault(new Error('Skill worker returned an unknown installation.'));
        return;
      }
      this.pending = undefined;
      if ('generation' in reply) pending.resolve(reply.generation);
      else {
        const error =
          reply.error.name === 'BackendSpawnFailed'
            ? new BackendSpawnFailed(reply.error.message)
            : Object.assign(new Error(reply.error.message), {
                name: reply.error.name,
                code: reply.error.code,
              });
        pending.reject(error);
        if (reply.fatal) this.fault(error);
      }
    });
    worker.on('error', (error) => this.fault(error));
    worker.on('exit', (code) => {
      if (!this.stop_sent || code !== 0 || this.pending)
        this.fault(
          new ServerLifecycleError('Skill worker exited before confirmed publication cleanup.'),
        );
      this.worker = undefined;
      void this.tail.then(() => {
        if (this.failure) this.reject_actual(this.failure);
        else this.resolve_actual();
      });
    });
    return worker;
  }
  private fault(error: unknown): void {
    const value = error instanceof Error ? error : new Error(String(error));
    this.closed = true;
    const pending = this.pending;
    this.pending = undefined;
    pending?.reject(value);
    this.record_fault(value);
    // The worker retains any lock until its own cleanup. Never terminate its thread.
    this.request_stop();
  }
  private record_fault(value: Error): void {
    this.closed = true;
    if (this.failure) return;
    this.failure = value;
    try {
      this.on_failure(value);
    } catch (error) {
      this.failure = new AggregateError([value, error], 'Skill publisher fault callback failed.');
    }
  }
  private request_stop(): void {
    if (this.stop_sent) return;
    if (!this.worker) {
      void this.tail.then(() => {
        if (this.failure) this.reject_actual(this.failure);
        else this.resolve_actual();
      });
      return;
    }
    try {
      this.worker.postMessage({ stop: true } satisfies SkillRequest);
      this.stop_sent = true;
    } catch (error) {
      // Keep the worker and failed close capability so a later stop can retry.
      if (!this.failure) this.fault(error);
    }
  }
  stop(options: StopOptions): Promise<void> {
    this.closed = true;
    this.request_stop();
    return observe_shutdown(
      this.actual,
      options,
      'Skill publication remains unfinished at the shutdown deadline.',
    );
  }
  settled(): Promise<void> {
    return this.actual;
  }
}

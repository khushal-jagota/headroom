/** Shared task custody for these mailbox loops, without a second shutdown clock. */
import { setTimeout as pause } from 'node:timers/promises';
import {
  observe_shutdown,
  is_shutdown_business_cancellation,
} from '../../../conversation/shutdown.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { mailbox_failure as failure_name } from './diagnostics.ts';
export class MailboxLifetime {
  readonly controller = new AbortController();
  private readonly tasks = new Set<Promise<void>>();
  private readonly on_failure: (error: Error) => void;
  private sealed = false;
  private failure: Error | undefined;
  private readonly failures: unknown[] = [];
  private resolve_actual!: () => void;
  private reject_actual!: (error: unknown) => void;
  private readonly actual = new Promise<void>((resolve, reject) => {
    this.resolve_actual = resolve;
    this.reject_actual = reject;
  });
  constructor(on_failure: (error: Error) => void) {
    this.on_failure = on_failure;
    void this.actual.catch(() => {});
  }
  get alive(): boolean {
    return !this.sealed;
  }
  get signal(): AbortSignal {
    return this.controller.signal;
  }
  assert_admission(): void {
    if (this.sealed) throw new ServerLifecycleError('Mailbox admission is stopped.');
  }
  own(work: () => Promise<void>): Promise<void> {
    this.assert_admission();
    const task = Promise.resolve().then(() => (this.alive ? work() : undefined));
    this.tasks.add(task);
    void task.then(
      () => this.release(task),
      (error) => {
        console.warn('Mac mailbox task failed:', failure_name(error));
        this.record_cleanup_failure(error);
        this.release(task);
      },
    );
    return task;
  }
  private release(task: Promise<void>): void {
    this.tasks.delete(task);
    this.complete();
  }
  record_cleanup_failure(error: unknown): void {
    this.failures.push(error);
    if (!this.failure) {
      this.failure = error instanceof Error ? error : new Error(String(error));
      this.seal();
      try {
        this.on_failure(this.failure);
      } catch (error) {
        this.failures.push(error);
        console.warn('Mac mailbox failure callback failed:', failure_name(error));
      }
    }
  }
  private complete(): void {
    if (!this.sealed || this.tasks.size) return;
    if (this.failures.length)
      this.reject_actual(new AggregateError(this.failures, 'Mailbox tasks or cleanup failed.'));
    else this.resolve_actual();
  }
  seal(): void {
    this.sealed = true;
    this.controller.abort();
    this.complete();
  }
  pause(seconds: number): Promise<void> {
    return pause(seconds * 1000, undefined, { signal: this.signal }).catch((error) => {
      const cancellation =
        this.signal.aborted && error instanceof Error && error.name === 'AbortError'
          ? this.signal.reason
          : error;
      if (this.signal.aborted && is_shutdown_business_cancellation(cancellation)) return;
      throw error;
    });
  }
  stop(options: StopOptions): Promise<void> {
    this.seal();
    return observe_shutdown(
      this.actual,
      options,
      'Mailbox tasks remain unfinished at the shutdown deadline.',
    );
  }
  settled(): Promise<void> {
    return this.actual;
  }
}
export class MailboxWake {
  private pending = true;
  private wake_waiter: (() => void) | undefined;
  wake(): void {
    this.pending = true;
    this.wake_waiter?.();
  }
  async wait(signal: AbortSignal): Promise<void> {
    try {
      signal.throwIfAborted();
      if (!this.pending)
        await new Promise<void>((resolve, reject) => {
          const cleanup = () => {
            signal.removeEventListener('abort', abort);
            this.wake_waiter = undefined;
          };
          const abort = () => {
            cleanup();
            reject(signal.reason);
          };
          this.wake_waiter = () => {
            cleanup();
            resolve();
          };
          signal.addEventListener('abort', abort, { once: true });
        });
      this.pending = false;
    } catch (error) {
      if (!(signal.aborted && is_shutdown_business_cancellation(error))) throw error;
    }
  }
}
export class MailboxCleanupFailure extends AggregateError {}

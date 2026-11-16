/** The host must join this aggregate before it stops journal/outbox ownership. */
import { setTimeout as pause } from 'node:timers/promises';
import {
  observe_shutdown,
  is_shutdown_business_cancellation,
} from '../../../conversation/shutdown.ts';
import { ServerLifecycleError, ShutdownDeadlineExceeded } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { LEASE_INTERVAL_SECONDS } from '../configuration.ts';
import type { MailboxDependencies } from './contracts.ts';
import { MacCommandQueues } from './queues.ts';
import { MacResultReporter } from './reporter.ts';
import { MacPollLoop } from './poll.ts';
export { MacCommandQueues, MacResultReporter, MacPollLoop };
export type * from './contracts.ts';
export class MacMailbox {
  readonly queues: MacCommandQueues;
  readonly reporter: MacResultReporter;
  readonly poll: MacPollLoop;
  private readonly dependencies: MailboxDependencies;
  private closed = false;
  private failure: Error | undefined;
  private startup: Promise<void> | undefined;
  private startup_window: AbortController | undefined;
  private cleanup: Promise<void> | undefined;
  private finish!: () => void;
  private reject_actual!: (error: unknown) => void;
  private readonly actual = new Promise<void>((resolve, reject) => {
    this.finish = resolve;
    this.reject_actual = reject;
  });
  constructor(dependencies: MailboxDependencies) {
    this.dependencies = dependencies;
    void this.actual.catch(() => {});
    const on_failure = (error: Error) => this.failed(error);
    this.reporter = new MacResultReporter({ ...dependencies, on_failure });
    const result_finished = () => this.reporter.wake();
    this.queues = new MacCommandQueues({ ...dependencies, result_finished, on_failure });
    this.poll = new MacPollLoop({ ...dependencies, result_finished, on_failure }, this.queues);
  }
  private failed(error: Error): void {
    if (this.failure) return;
    this.failure = error;
    this.closed = true;
    this.startup_window?.abort();
    this.dependencies.on_failure(error);
  }
  start(on_replayed?: () => void): Promise<void> {
    if (this.closed) return Promise.reject(new ServerLifecycleError('Mailbox is stopped.'));
    this.startup ??= (async () => {
      const window = new AbortController();
      this.startup_window = window;
      try {
        await Promise.race([
          this.queues.republish(),
          pause(LEASE_INTERVAL_SECONDS * 1000, undefined, { signal: window.signal }).catch(
            (error) => {
              if (window.signal.aborted && error instanceof Error && error.name === 'AbortError')
                throw window.signal.reason;
              throw error;
            },
          ),
        ]);
        if (this.closed) return;
        on_replayed?.();
        if (this.closed) return;
        this.reporter.start();
        this.poll.start();
      } catch (error) {
        if (this.closed && is_shutdown_business_cancellation(error)) return;
        this.failed(error instanceof Error ? error : new Error(String(error)));
        throw error;
      } finally {
        window.abort();
        this.startup_window = undefined;
      }
    })();
    void this.startup.catch(() => {});
    return this.startup;
  }
  stop(options: StopOptions): Promise<void> {
    this.closed = true;
    if (!this.cleanup) {
      let resolve_cleanup!: () => void, reject_cleanup!: (error: unknown) => void;
      this.cleanup = new Promise<void>((resolve, reject) => {
        resolve_cleanup = resolve;
        reject_cleanup = reject;
      });
      void this.cleanup.then(
        () => this.finish(),
        (error) => this.reject_actual(error),
      );
      // Publish ownership before an abort handler can reenter stop.
      this.startup_window?.abort();
      // Deadline observations do not cancel this actual ownership chain.
      const observe = (stop: () => Promise<void>): Promise<PromiseSettledResult<void>> => {
        let bounded: Promise<void>;
        try {
          bounded = stop();
        } catch (error) {
          bounded = Promise.reject(error);
        }
        return Promise.allSettled([bounded]).then((results) => results[0]!);
      };
      const observations = [
        observe(() => this.queues.stop(options)),
        observe(() => this.poll.stop(options)),
        observe(() => this.reporter.stop(options)),
      ];
      void (async () => {
        const first = await Promise.allSettled([this.startup, this.poll.settled()]);
        const last = await Promise.allSettled([this.queues.settled(), this.reporter.settled()]);
        const errors = new Set(
          [...first, ...last].flatMap((result) =>
            result.status === 'rejected' ? [result.reason] : [],
          ),
        );
        for (const result of await Promise.all(observations))
          if (result.status === 'rejected' && !(result.reason instanceof ShutdownDeadlineExceeded))
            errors.add(result.reason);
        if (errors.size)
          throw new AggregateError(
            errors,
            'Mailbox shutdown failed after every actual task settled.',
          );
      })().then(resolve_cleanup, reject_cleanup);
    }
    return observe_shutdown(
      this.cleanup,
      options,
      'Mailbox ownership remains unfinished at the shutdown deadline.',
    );
  }
  settled(): Promise<void> {
    return this.actual;
  }
}

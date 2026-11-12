/** The database owner retains its lock until every sidecar and worker actually settles. */
import type { DatabaseCloseHandle } from '../../../core/database/contracts.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import { ShutdownDeadlineExceeded } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import type { FilesService } from '../files.ts';
export interface OutboxCleanup {
  stop(options: StopOptions): Promise<void>;
  /** Actual closure of every owned resource, then any retained fatal/cleanup failure. */
  settled(): Promise<void>;
}
export interface OutboxSidecar extends OutboxCleanup {
  assert_healthy(): void;
  on_failure(listener: (error: Error) => void): () => void;
}
export class OutboxResources implements OutboxCleanup {
  database: DatabaseCloseHandle | undefined;
  lock: { close(): void } | undefined;
  private readonly files: FilesService;
  private readonly sidecars = new Set<OutboxSidecar>();
  private file_phase = false;
  private sequence: Promise<void> | undefined;
  private done = false;
  private readonly failures = new Set<unknown>();
  private readonly observers = new Set<Promise<void>>();
  private finish!: () => void;
  private reject!: (error: unknown) => void;
  private readonly actual = new Promise<void>((resolve, reject) => {
    this.finish = resolve;
    this.reject = reject;
  });
  constructor(files: FilesService) {
    this.files = files;
    void this.actual.catch(() => {});
  }
  register(service: OutboxSidecar): void {
    this.sidecars.add(service);
  }
  record_cleanup_failure(error: unknown): void {
    this.failures.add(error);
  }
  assert_healthy(): void {
    for (const service of this.sidecars) service.assert_healthy();
  }
  get is_settled(): boolean {
    return this.done;
  }
  stop(options: StopOptions): Promise<void> {
    if (this.done)
      return observe_shutdown(
        this.actual,
        options,
        'Outbox resources remain unfinished at the shutdown deadline.',
      );
    if (this.sequence === undefined && !this.done) {
      // Publish the chain before a child stop callback can reenter this owner.
      const sequence = Promise.resolve().then(async () => {
        const children = await Promise.allSettled(
          [...this.sidecars].map((service) => service.settled()),
        );
        for (const child of children)
          if (child.status === 'rejected') this.failures.add(child.reason);
        await this.join_observers();
        try {
          this.database?.close();
        } catch (error) {
          this.failures.add(error);
          throw error;
        }
        this.database = undefined;
        this.file_phase = true;
        // A bounded stop can fail separately from a clean actual worker exit.
        const files = await Promise.allSettled([
          this.observe_stop(() => this.files.stop(options)),
          this.files.settled(),
        ]);
        const file_result = files[1]!;
        if (file_result.status === 'rejected') this.failures.add(file_result.reason);
        await this.join_observers();
        try {
          this.lock?.close();
        } catch (error) {
          this.failures.add(error);
          throw error;
        }
        this.lock = undefined;
        await this.join_observers();
        this.done = true;
        if (this.failures.size) this.reject(this.failure());
        else this.finish();
        await this.actual;
      });
      this.sequence = sequence;
      void sequence.catch(() => {
        if (this.sequence === sequence) this.sequence = undefined;
      });
    }
    const observations = [...this.sidecars].map((service) =>
      this.observe_stop(() => service.stop(options)),
    );
    if (this.file_phase) observations.push(this.observe_stop(() => this.files.stop(options)));
    return observe_shutdown(
      Promise.all([...observations, this.sequence ?? this.actual]).then(() => undefined),
      options,
      'Outbox resources remain unfinished at the shutdown deadline.',
    );
  }
  settled(): Promise<void> {
    return this.actual;
  }
  private observe_stop(stop: () => Promise<void>): Promise<void> {
    let bounded: Promise<void>;
    try {
      bounded = stop();
    } catch (error) {
      bounded = Promise.reject(error);
    }
    const recorded = bounded.then(
      () => {},
      (error) => {
        if (!(error instanceof ShutdownDeadlineExceeded)) this.failures.add(error);
      },
    );
    this.observers.add(recorded);
    void recorded.then(() => this.observers.delete(recorded));
    return bounded;
  }
  private async join_observers(): Promise<void> {
    while (this.observers.size) await Promise.all([...this.observers]);
  }
  private failure(): unknown {
    return this.failures.size === 1
      ? [...this.failures][0]
      : new AggregateError(this.failures, 'Outbox resource cleanup failed.');
  }
}

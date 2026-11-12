/** The database owner retains its lock until every sidecar and worker actually settles. */
import type { DatabaseCloseHandle } from '../../../core/database/contracts.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import type { FilesService } from '../files.ts';
export interface OutboxCleanup {
  stop(options: StopOptions): Promise<void>;
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
  private finish!: () => void;
  private readonly actual = new Promise<void>((resolve) => {
    this.finish = resolve;
  });
  constructor(files: FilesService) {
    this.files = files;
  }
  register(service: OutboxSidecar): void {
    this.sidecars.add(service);
  }
  assert_healthy(): void {
    for (const service of this.sidecars) service.assert_healthy();
  }
  get is_settled(): boolean {
    return this.done;
  }
  stop(options: StopOptions): Promise<void> {
    const observations = [...this.sidecars].map((service) => service.stop(options));
    if (this.file_phase) observations.push(this.files.stop(options));
    if (this.sequence === undefined) {
      const sequence = (async () => {
        for (const service of this.sidecars) await service.settled();
        this.database?.close();
        this.database = undefined;
        this.file_phase = true;
        // A deadline observes this chain. It does not cancel its later cleanup stages.
        void this.files.stop(options).catch(() => {});
        await this.files.settled();
        this.lock?.close();
        this.lock = undefined;
        this.done = true;
        this.finish();
      })();
      this.sequence = sequence;
      void sequence.catch(() => {
        if (this.sequence === sequence) this.sequence = undefined;
      });
    }
    return observe_shutdown(
      Promise.all([...observations, this.sequence]).then(() => undefined),
      options,
      'Outbox resources remain unfinished at the shutdown deadline.',
    );
  }
  settled(): Promise<void> {
    return this.actual;
  }
}

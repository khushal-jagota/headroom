/** The database owner retains its lock independently of the optional file worker. */
import type { Database } from '../../../core/database/contracts.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import type { FilesService } from '../files.ts';
export interface OutboxCleanup {
  stop(options: StopOptions): Promise<void>;
  settled(): Promise<void>;
}
export class OutboxResources implements OutboxCleanup {
  database: Database | undefined;
  lock: { close(): void } | undefined;
  private readonly files: FilesService;
  private done = false;
  private finish!: () => void;
  private readonly actual = new Promise<void>((resolve) => {
    this.finish = resolve;
  });
  constructor(files: FilesService) {
    this.files = files;
  }
  get is_settled(): boolean {
    return this.done;
  }
  stop(options: StopOptions): Promise<void> {
    try {
      this.database?.close();
      this.database = undefined;
    } catch (error) {
      // Keep the lock and worker owned. A later stop retries the database close.
      return Promise.reject(error);
    }
    const attempt = this.files.stop(options);
    const finish = this.files.settled().then(() => {
      this.lock?.close();
      this.lock = undefined;
      this.done = true;
      this.finish();
    });
    return observe_shutdown(
      Promise.all([attempt, finish]).then(() => undefined),
      options,
      'Outbox resources remain unfinished at the shutdown deadline.',
    );
  }
  settled(): Promise<void> {
    return this.actual;
  }
}

/** One current-minute clock for source scheduling and Goal reconciliation. */
import type { Database } from '../core/database/contracts.ts';
import type { Clock } from '../core/clock.ts';
import type { WorkspaceAdmission } from '../conversation/workspace_admission.ts';
import type { SlotParticipant } from './participants.ts';
export interface SourceScheduleLoopOptions {
  database: Database;
  clock: Clock;
  participants: readonly SlotParticipant[];
  assert_database_owned(): void;
  workspace_admission?: WorkspaceAdmission;
}
export class SourceScheduleLoop {
  private readonly options: SourceScheduleLoopOptions;
  private stopped = false;
  private started = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private interval = 0;
  constructor(options: SourceScheduleLoopOptions) {
    this.options = options;
  }
  poll_once(): void {
    this.options.assert_database_owned();
    if (this.stopped) return;
    const admission = this.options.workspace_admission?.operation();
    try {
      if (admission && !admission.admitted) return;
      for (const participant of this.options.participants) {
        if (this.stopped) break;
        participant.poll(this.options.database, this.options.clock);
      }
    } finally {
      admission?.release();
    }
  }
  start(interval: number): void {
    this.options.assert_database_owned();
    if (this.started || this.stopped)
      throw new Error('measure source loop already started or stopped');
    this.started = true;
    this.interval = interval * 1000;
    for (const participant of this.options.participants) participant.start();
    this.timer = setTimeout(() => this.tick(), 0);
    this.timer.unref();
  }
  private tick(): void {
    this.timer = undefined;
    if (this.stopped) return;
    try {
      this.poll_once();
    } catch {
      console.error('measure source poll failed');
    } finally {
      if (!this.stopped) {
        this.timer = setTimeout(() => this.tick(), this.interval);
        this.timer.unref();
      }
    }
  }
  stop(options: { deadline?: number } = {}): Promise<void> {
    this.stopped = true;
    if (this.timer !== undefined) clearTimeout(this.timer);
    // Each participant fences its producer before any drain is awaited.
    const drains = this.options.participants.map((participant) => {
      try {
        return Promise.resolve(participant.stop(options));
      } catch (error) {
        return Promise.reject(error);
      }
    });
    return Promise.allSettled(drains).then((results) => {
      const failures = results.filter((result) => result.status === 'rejected');
      if (failures.length)
        throw new AggregateError(
          failures.map((result) => result.reason),
          'Source clock shutdown failed',
        );
    });
  }
}

/** One current-minute clock for source scheduling and Goal reconciliation. */
import type { Database } from '../core/database/contracts.ts';
import type { Clock } from '../core/clock.ts';
import type { WorkspaceAdmission } from '../conversation/workspace_admission.ts';
import type { SlotParticipant } from './participants.ts';
import { observe_shutdown } from '../lifecycle/deadline.ts';
import { SHUTDOWN_GRACE_SECONDS } from '../core/config.ts';
import { ShutdownDeadlineExceeded } from '../lifecycle/contracts.ts';
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
  private closing: Promise<void> | undefined;
  private shutdown_settlement: Promise<void> | undefined;
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
  settled(): Promise<void> {
    return (
      this.shutdown_settlement ??
      Promise.all(this.options.participants.map((participant) => participant.settled())).then(
        () => {},
      )
    );
  }
  stop(options: { deadline?: number } = {}): Promise<void> {
    if (this.closing) return this.closing;
    this.stopped = true;
    if (this.timer !== undefined) clearTimeout(this.timer);
    const failures: unknown[] = [],
      observers: Promise<unknown>[] = [];
    const stop = {
      deadline: options.deadline ?? performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS,
    };
    this.shutdown_settlement = Promise.resolve().then(async () => {
      const actuals = this.options.participants.map((participant) => {
        try {
          return participant.settled();
        } catch (error) {
          return Promise.reject(error);
        }
      });
      const results = await Promise.allSettled([...observers, ...actuals]);
      for (const result of results) if (result.status === 'rejected') failures.push(result.reason);
      if (failures.length) throw new AggregateError(failures, 'Source clock shutdown failed');
    });
    this.closing = observe_shutdown(this.shutdown_settlement, stop, 'source clock');
    // Each participant fences its producer before any drain is awaited.
    for (const participant of this.options.participants) {
      try {
        observers.push(
          Promise.resolve(participant.stop(stop)).catch((error: unknown) => {
            if (!(error instanceof ShutdownDeadlineExceeded)) throw error;
          }),
        );
      } catch (error) {
        failures.push(error);
      }
    }
    return this.closing;
  }
}

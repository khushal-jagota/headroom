/** Report worker-selected canonical bytes. Fairness and inventories stay off main. */
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { setTimeout as pause } from 'node:timers/promises';
import { is_shutdown_business_cancellation } from '../../../conversation/shutdown.ts';
import { LEASE_INTERVAL_SECONDS, OUTBOX_RETRY_INITIAL_SECONDS } from '../configuration.ts';
import { mailbox_failure as failure_name } from './diagnostics.ts';
import type { ReporterDependencies } from './contracts.ts';
import { MailboxLifetime, MailboxWake } from './lifetime.ts';
export class MacResultReporter {
  private readonly dependencies: ReporterDependencies;
  private readonly lifetime: MailboxLifetime;
  private readonly wake_signal = new MailboxWake();
  private started = false;
  constructor(dependencies: ReporterDependencies) {
    this.dependencies = dependencies;
    this.lifetime = new MailboxLifetime(dependencies.on_failure);
  }
  start(): void {
    this.lifetime.assert_admission();
    if (this.started) return;
    this.started = true;
    void this.lifetime.own(() => this.run());
  }
  wake(): void {
    if (this.lifetime.alive) this.wake_signal.wake();
  }
  private async complete<T>(work: () => Promise<T>): Promise<T> {
    while (true) {
      try {
        return await work();
      } catch (error) {
        const refusal = error as { code?: unknown; unadmitted?: unknown } | null;
        if (refusal?.code !== 'EBUSY' || refusal.unadmitted !== true) throw error;
        this.dependencies.journal.assert_healthy();
        // This is already-admitted selection cleanup, not another HTTP attempt.
        await pause(OUTBOX_RETRY_INITIAL_SECONDS * 1000);
      }
    }
  }
  private async report_once(): Promise<boolean> {
    const { journal, client } = this.dependencies;
    const selected = await journal.select_report();
    if (selected === null) return false;
    try {
      if (!this.lifetime.alive) {
        await this.complete(() => journal.report_feedback(selected.selection, null));
        return false;
      }
      let reply;
      try {
        reply = await client.report_outcomes(selected.body, this.lifetime.signal);
      } catch (error) {
        const cancelled = !this.lifetime.alive && is_shutdown_business_cancellation(error);
        await this.complete(() => journal.report_feedback(selected.selection, null));
        if (cancelled) return false;
        throw error;
      }
      const settled = new Set(reply.recorded);
      for (const rejected of reply.rejected ?? []) {
        let reason = '',
          count = 0;
        for (const point of rejected.reason) {
          if (count++ === 200) break;
          reason += point;
        }
        console.warn(`VPS rejected command result command=${rejected.command_id} reason=${reason}`);
        settled.add(rejected.command_id);
      }
      const progressed = await this.complete(() =>
        journal.report_feedback(
          selected.selection,
          selected.command_ids.filter((id) => settled.has(id)),
        ),
      );
      if (!progressed) throw new Error('The VPS recorded none of the reported results.');
      return true;
    } finally {
      try {
        await this.complete(() => selected.body.close());
      } catch (error) {
        console.warn('Mac result report cleanup failed:', failure_name(error));
        this.lifetime.record_cleanup_failure(error);
        journal.delivery_failure('Result report reader cleanup failed.', error);
      }
    }
  }
  private async run(): Promise<void> {
    let delay = OUTBOX_RETRY_INITIAL_SECONDS;
    while (this.lifetime.alive) {
      await this.wake_signal.wait(this.lifetime.signal);
      try {
        while (this.lifetime.alive && (await this.report_once()))
          delay = OUTBOX_RETRY_INITIAL_SECONDS;
        if (this.lifetime.alive) await this.dependencies.journal.shrink_to_tombstones();
      } catch (error) {
        this.dependencies.journal.assert_healthy();
        if (!this.lifetime.alive) throw error;
        console.warn('Mac command outcome report failed:', failure_name(error));
        await this.lifetime.pause(delay);
        delay = Math.min(delay * 2, LEASE_INTERVAL_SECONDS);
        this.wake_signal.wake();
      }
    }
  }
  stop(options: StopOptions): Promise<void> {
    return this.lifetime.stop(options);
  }
  settled(): Promise<void> {
    return this.lifetime.settled();
  }
}

/** Report worker-selected canonical bytes. Fairness and inventories stay off main. */
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { LEASE_INTERVAL_SECONDS, OUTBOX_RETRY_INITIAL_SECONDS } from '../configuration.ts';
import { mailbox_failure as failure_name } from './diagnostics.ts';
import type { ReporterDependencies } from './contracts.ts';
import type { JournalReportFeedback } from '../storage/journal_contracts.ts';
import { MailboxLifetime, MailboxWake, MailboxCleanupFailure } from './lifetime.ts';
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
  private async report_once(): Promise<boolean> {
    const { journal, client } = this.dependencies;
    const selected = await journal.select_report();
    if (selected === null) return false;
    let feedback_finished = false,
      withdrawn = false,
      primary: unknown,
      failed = false;
    const feedback = async (ids: JournalReportFeedback) => {
      try {
        return await journal.report_feedback(selected.selection, ids);
      } finally {
        // Owned cleanup is admitted behind the ordinary chain. An admitted worker
        // failure releases its selection too; a broken worker retains shutdown custody.
        feedback_finished = true;
      }
    };
    try {
      if (!this.lifetime.alive) {
        withdrawn = true;
        return false;
      }
      let reply;
      try {
        reply = await client.report_outcomes(selected.body, this.lifetime.signal);
      } catch (error) {
        if (!this.lifetime.alive) {
          withdrawn = true;
          return false;
        }
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
      const progressed = await feedback(selected.command_ids.filter((id) => settled.has(id)));
      if (!progressed) throw new Error('The VPS recorded none of the reported results.');
      return true;
    } catch (error) {
      primary = error;
      failed = true;
      throw error;
    } finally {
      const errors: unknown[] = [];
      if (!feedback_finished) {
        try {
          await feedback(withdrawn ? 'cancelled' : null);
        } catch (error) {
          errors.push(error);
        }
      }
      try {
        await selected.body.close();
      } catch (error) {
        errors.push(error);
      }
      if (errors.length) {
        const failure = new MailboxCleanupFailure(
          failed ? [primary, ...errors] : errors,
          'Result report selection or reader cleanup failed.',
        );
        this.lifetime.record_cleanup_failure(failure);
        journal.delivery_failure('Result report cleanup failed.', failure);
        throw failure;
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

/** Admit only the latest real daily occurrence inside the catch-up window. */
import type { OwnedAreaContext } from '../core/authority/register.ts';
import type { Database } from '../core/database/contracts.ts';
import type { Clock } from '../core/clock.ts';
import { PlannerError } from '../core/contracts.ts';
import { GoalCompletionParticipant } from '../goals/scheduling.ts';
import type { SourceRuntime } from './contracts.ts';
import type { SlotParticipant } from './participants.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import { SourceDispatcher } from './dispatcher.ts';
import { admit_scheduled_run } from './run_actions.ts';
import { latest_due_slot } from './logic/local_time.ts';
export class SourceScheduleParticipant implements SlotParticipant {
  private stopped = false;
  private readonly dispatcher: SourceDispatcher;
  constructor(context: OwnedAreaContext, runtime: SourceRuntime) {
    this.dispatcher = new SourceDispatcher({ context, runtime });
  }
  start(): void {
    this.stopped = false;
    this.dispatcher.start();
  }
  poll(database: Database, clock: Clock): void {
    if (this.stopped) return;
    const rows = database.read((sql) =>
      sql.all<{ id: string; local_time: string; created_at: number }>(
        'SELECT id,local_time,created_at FROM measure_sources WHERE enabled=1 ORDER BY id',
      ),
    );
    for (const row of rows) {
      if (this.stopped) break;
      const slot = latest_due_slot(row.local_time, clock.now());
      if (slot === null || slot.getTime() / 1000 < Math.floor(row.created_at / 60) * 60) continue;
      try {
        database.write((sql) =>
          admit_scheduled_run(sql, row.id, {
            local_now: slot,
            now: clock.nowUnix(),
          }),
        );
      } catch (error) {
        if (!(error instanceof PlannerError)) throw error;
        console.error('source schedule admission refused');
      }
    }
  }
  stop(options: StopOptions): Promise<void> {
    this.stopped = true;
    return this.dispatcher.stop(options);
  }
  settled(): Promise<void> {
    return this.dispatcher.settled();
  }
}
export function build_participants(
  context: OwnedAreaContext,
  runtime: SourceRuntime,
): SlotParticipant[] {
  const completion = new GoalCompletionParticipant();
  return [
    new SourceScheduleParticipant(context, runtime),
    {
      start: () => completion.start(),
      poll: (database, clock) => completion.poll(database, clock.nowUnix()),
      stop: (options) => completion.stop(options),
      // Goal reconciliation is synchronous. Its stop fence leaves no async flight.
      settled: () => Promise.resolve(),
    },
  ];
}

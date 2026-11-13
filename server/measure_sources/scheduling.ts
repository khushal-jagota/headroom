/** Current local-minute admission. Missed minutes never create catch-up runs. */
import type { OwnedAreaContext } from '../core/authority/register.ts';
import type { Database } from '../core/database/contracts.ts';
import type { Clock } from '../core/clock.ts';
import { PlannerError } from '../core/contracts.ts';
import { GoalCompletionParticipant } from '../goals/scheduling.ts';
import type { SourceRuntime } from './contracts.ts';
import type { SlotParticipant } from './participants.ts';
import { SourceDispatcher } from './dispatcher.ts';
import { admit_scheduled_run } from './run_actions.ts';
export function current_source_slot(clock: Clock): [string, string] {
  // Date normalizes a fixed-offset test clock to UTC. Retain its local label here.
  const local = clock.isoformat();
  return [local.slice(0, 10), local.slice(11, 16)];
}
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
    const slot = current_source_slot(clock);
    const rows = database.read((sql) =>
      sql.all<{ id: string }>(
        `
      SELECT id FROM measure_sources
      WHERE enabled=1 AND local_time=? ORDER BY id
    `,
        [slot[1]],
      ),
    );
    for (const row of rows) {
      if (this.stopped) break;
      try {
        database.write((sql) =>
          admit_scheduled_run(sql, row.id, {
            local_now: clock.now(),
            now: clock.nowUnix(),
            slot,
          }),
        );
      } catch (error) {
        if (!(error instanceof PlannerError)) throw error;
        console.error('source schedule admission refused');
      }
    }
  }
  stop(options: { deadline?: number } = {}): Promise<void> {
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

/** Goal completion participant for the shared background clock; owns no timer or writer. */
import type { Database, ReadSql } from '../core/database/contracts.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import { server_date } from './calendar.ts';
import { reconcile_completions } from './outcomes.ts';
export class GoalCompletionParticipant {
  private stopped = false;
  private previous: string | null = null;
  start(): void {
    this.stopped = false;
    this.previous = null;
  }
  poll(database: Database, now: number): void {
    if (this.stopped) return;
    const today = server_date(now),
      revision = (sql: ReadSql) => {
        const source = (kind: string) =>
          String(
            sql.get<{ revision: number }>(
              'SELECT revision FROM resource_source_versions WHERE source_kind=? AND source_id=?',
              [kind, 'all'],
            )?.revision ?? 0,
          );
        return JSON.stringify([source('goals'), source('automatic-measures'), today]);
      };
    if (database.read(revision) === this.previous) return;
    this.previous = database.write((sql) => {
      reconcile_completions(sql, now);
      return revision(sql);
    });
  }
  stop(_options: StopOptions): void {
    this.stopped = true;
  }
}

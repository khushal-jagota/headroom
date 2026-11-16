import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import { create_wake } from '../manager_wakes/data.ts';
import type { FailureCode, MeasureSource, RunFailure, SourceRun } from './contracts.ts';
import * as data from './data.ts';
export { failure_message } from './responsibility.ts';
import { notify_episode as notify_stored_episode } from './responsibility.ts';
export function notify_episode(
  sql: TransactionSql,
  source: MeasureSource,
  { now }: { now: number },
): void {
  const row = sql.get<Record<string, unknown>>('SELECT * FROM measure_sources WHERE id=?', [
    source.id,
  ]);
  if (row) notify_stored_episode(sql, row, now);
}
export function record_failure(
  sql: TransactionSql,
  run: SourceRun,
  { now }: { now: number },
): void {
  const episode = data.read_failure_episode(sql, run.source_id);
  if (!episode) {
    const n = sql.get<{ n: number }>(
      'SELECT COALESCE(MAX(episode),0)+1 AS n FROM measure_source_failure_episodes WHERE source_id=?',
      [run.source_id],
    )!.n;
    sql.run(
      'INSERT INTO measure_source_failure_episodes(source_id,episode,opened_at,latest_run_id) VALUES (?,?,?,?)',
      [run.source_id, n, now, run.id],
    );
  } else
    sql.run(
      'UPDATE measure_source_failure_episodes SET latest_run_id=? WHERE source_id=? AND episode=?',
      [run.id, run.source_id, episode.episode],
    );
  notify_episode(sql, data.read_source(sql, run.source_id), { now });
}
export function notice_is_current(
  sql: ReadSql,
  row: {
    measure_source_id: string;
    source_responsibility_revision: number;
    source_episode: number;
  },
): boolean {
  const s = sql.get<{ responsibility_revision: number }>(
    'SELECT responsibility_revision FROM measure_sources WHERE id=?',
    [row.measure_source_id],
  );
  return (
    s?.responsibility_revision === row.source_responsibility_revision &&
    Boolean(
      sql.get(
        'SELECT 1 FROM measure_source_failure_episodes WHERE source_id=? AND episode=? AND closed_at IS NULL',
        [row.measure_source_id, row.source_episode],
      ),
    )
  );
}

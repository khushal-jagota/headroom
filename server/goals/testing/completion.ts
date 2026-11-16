/** Compare canonical completion writers and their revision-sensitive clock participant. */
import { createDatabase } from '../../core/database/index.ts';
import type { ReadSql } from '../../core/database/contracts.ts';
import { parse_write } from '../../measures/logic/manual.ts';
import { write_value } from '../../measures/manual.ts';
import type { GoalCompletionRule } from '../contracts.ts';
import { update_goal } from '../actions.ts';
import { reconcile_completions } from '../outcomes.ts';
import { GoalCompletionParticipant } from '../scheduling.ts';
interface Spec {
  db_path: string;
  now: number;
  measure_id: string;
  goal_id: string;
  rule: GoalCompletionRule;
}
export function run(spec: Spec) {
  const database = createDatabase(spec.db_path),
    participant = new GoalCompletionParticipant(),
    result: unknown[] = [];
  const snapshot = (phase: string, outcome: unknown = null) =>
    result.push(database.read((sql) => ({ phase, outcome, tables: rows(sql) })));
  const poll = () => participant.poll(database, spec.now);
  const replace = (id: string, date: string, value: number, revision: number) =>
    database.write((sql) =>
      write_value(
        sql,
        spec.measure_id,
        parse_write({
          request_id: id,
          date,
          expected_definition_revision: 1,
          basis: 'date',
          operation: 'replace',
          value,
          expected_write_revision: revision,
        }),
        { kind: 'owner', id: 'owner' },
        spec.now,
      ),
    );
  try {
    participant.start();
    poll();
    snapshot('initial');
    replace('completion-first', '2026-10-25', 12, 0);
    poll();
    snapshot('achieved');
    poll();
    snapshot('unchanged-poll');
    replace('completion-correction', '2026-10-25', 3, 1);
    poll();
    snapshot('correction-reopens');
    const replay = replace('completion-correction', '2026-10-25', 3, 1);
    poll();
    snapshot('replayed-write', replay.replayed);
    replace('completion-later', '2026-10-26', 15, 2);
    snapshot(
      'direct-reconcile',
      database.write((sql) => reconcile_completions(sql, spec.now)),
    );
    snapshot(
      'unchanged-reconcile',
      database.write((sql) => reconcile_completions(sql, spec.now)),
    );
    database.write((sql) =>
      update_goal(
        sql,
        spec.goal_id,
        {
          completion_rule: {
            ...spec.rule,
            direction: 'atmost',
            target: { op: 'constant', value: 0 },
          },
        },
        spec.now,
      ),
    );
    snapshot('changed-direction');
    participant.stop({ deadline: performance.now() / 1000 + 1 });
    replace('completion-negative', '2026-10-27', -2.75, 3);
    poll();
    snapshot('stopped-participant');
    participant.start();
    poll();
    snapshot('restarted-participant');
    return result;
  } finally {
    participant.stop({ deadline: performance.now() / 1000 + 1 });
    database.close();
  }
}
function rows(sql: ReadSql): Record<string, unknown[]> {
  const result: Record<string, unknown[]> = {};
  for (const [table, order] of [
    ['goals', 'id'],
    ['measures', 'id'],
    ['measure_observations', 'measure_id,date'],
    ['manual_measure_writes', 'request_id'],
    ['resource_source_versions', 'source_kind,source_id'],
  ] as const)
    result[table] = sql.all(`SELECT * FROM ${table} ORDER BY ${order}`);
  return result;
}

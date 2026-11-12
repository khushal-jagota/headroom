/** Direct paired proof of canonical source completion, without a child or HTTP host. */
import { createDatabase } from '../../core/database/index.ts';
import type { ReadSql } from '../../core/database/contracts.ts';
import { parse_numeric_json } from '../../core/http/numeric_json.ts';
import type { RunResult } from '../contracts.ts';
import * as actions from '../actions.ts';
import * as data from '../data.ts';
import * as runs from '../run_actions.ts';
import { notice_is_current } from '../notices.ts';
const tables: Record<string, string> = {
  measure_sources: 'id',
  measure_source_runs: 'id',
  measure_observations: 'measure_id,date',
  measure_history_gaps: 'measure_id,date',
  dataset_results: 'dataset_id,date',
  dataset_rows: 'dataset_id,date,dimension_key',
  dataset_category_labels: 'dataset_id,dimension,code',
  measure_source_failure_episodes: 'source_id,episode',
  manager_wakes: 'id',
  resource_source_versions: 'source_kind,source_id',
};
interface Spec {
  db_path: string;
  now: number;
  source_id: string;
  manager_id: string;
  phases: {
    name: string;
    run_id?: string;
    result_json?: string;
    operation?: 'transfer' | 'script' | 'abandon';
  }[];
}
export function run(spec: Spec) {
  const database = createDatabase(spec.db_path),
    out: unknown[] = [];
  try {
    for (const phase of spec.phases) {
      let outcome: unknown = null;
      if (phase.operation === 'transfer')
        database.write((sql) =>
          actions.set_responsible(
            sql,
            spec.source_id,
            { kind: 'manager', id: spec.manager_id },
            {
              principal: { kind: 'owner', id: 'owner' },
              now: spec.now,
              expected_responsibility_revision: data.read_source(sql, spec.source_id)
                .responsibility_revision,
            },
          ),
        );
      else if (phase.operation === 'script')
        database.write((sql) =>
          actions.submit_script(
            sql,
            spec.source_id,
            {
              expected_revision: data.read_source(sql, spec.source_id).source_revision,
              text: 'changed script',
            },
            { principal: { kind: 'owner', id: 'owner' }, now: spec.now },
          ),
        );
      else if (phase.operation === 'abandon')
        outcome = database.write((sql) => runs.interrupt_abandoned_runs(sql, { now: spec.now }));
      else {
        const claimed = database.write((sql) =>
          runs.claim_run(sql, phase.run_id!, { now: spec.now }),
        );
        if (claimed && phase.result_json) {
          const parsed = JSON.parse(phase.result_json) as RunResult,
            numeric = parse_numeric_json(phase.result_json) as RunResult;
          parsed.observations = numeric.observations;
          parsed.dataset_results = numeric.dataset_results;
          outcome = database.write((sql) =>
            runs.complete_run(sql, phase.run_id!, parsed, { now: spec.now }),
          );
        }
      }
      out.push(
        database.read((sql) => ({
          phase: phase.name,
          outcome,
          tables: rows(sql),
          current_notices: sql
            .all<{
              measure_source_id: string;
              source_responsibility_revision: number;
              source_episode: number;
            }>('SELECT * FROM manager_wakes ORDER BY id')
            .map((row) => notice_is_current(sql, row)),
        })),
      );
    }
    return out;
  } finally {
    database.close();
  }
}
function rows(sql: ReadSql) {
  return Object.fromEntries(
    Object.entries(tables).map(([table, order]) => [
      table,
      sql.all(`SELECT * FROM ${table} ORDER BY ${order}`),
    ]),
  );
}

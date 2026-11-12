/** Source completion validates both batches before the first retained write. */
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import type { NumericJSON } from '../core/http/numeric_json.ts';
import { stringify_python_json } from '../core/http/numeric_json.ts';
import { PlannerError } from '../core/contracts.ts';
import type { CandidateObservation } from './contracts.ts';
import type { OutputMeasure } from '../measures/contracts.ts';
import type { CandidateDatasetResult, OutputDataset } from '../datasets/contracts.ts';
import { calendar_date, local_date } from '../measures/logic/dates.ts';
import { validate_coverage } from '../measures/logic/coverage.ts';
import { number } from '../measures/logic/formulas.ts';
import { hydrate_measure } from '../measures/data.ts';
import { validate_batch as validate_results, dimension_key } from '../datasets/logic/validation.ts';
const invalid = (message: string): never => {
  throw new PlannerError('validation', message);
};
export function prepare_measure_outputs(
  sql: ReadSql,
  source_id: string,
  frozen_at: number,
): OutputMeasure[] {
  return sql
    .all<Record<string, unknown>>('SELECT * FROM measures WHERE source_id=? ORDER BY id', [
      source_id,
    ])
    .map(hydrate_measure)
    .map((m) => ({
      measure_id: m.id,
      definition_revision: m.definition_revision,
      kind: m.kind,
      timezone: m.timezone,
      period: m.period,
      required_date: local_date(frozen_at, m.timezone),
      period_interval: null,
    }));
}
export function validate_measures(
  sql: ReadSql,
  source_id: string,
  outputs: OutputMeasure[],
  candidates: NumericJSON<CandidateObservation[]>,
): void {
  const ids = new Set(
      sql
        .all<{ id: string }>('SELECT id FROM measures WHERE source_id=?', [source_id])
        .map((x) => x.id),
    ),
    captured = new Set(outputs.map((x) => x.measure_id)),
    seen = new Set<string>();
  for (const c of candidates) {
    const pair = c.measure_id + '\0' + c.date;
    if (seen.has(pair) || !ids.has(c.measure_id) || !captured.has(c.measure_id))
      invalid('duplicate or foreign source output');
    seen.add(pair);
    calendar_date(c.date);
    validate_coverage(c.query_start_at, c.query_end_at, c.limitation);
    if (c.value === null) {
      if (
        !['provider_has_no_history', 'provider_has_no_value'].includes(c.unavailable_reason ?? '')
      )
        invalid('missing output requires a reason');
    } else {
      if (c.unavailable_reason !== null) invalid('value cannot carry an unavailable reason');
      number(c.value);
    }
  }
}
export function validate_datasets(
  sql: ReadSql,
  source_id: string,
  outputs: OutputDataset[],
  candidates: NumericJSON<CandidateDatasetResult[]>,
): void {
  const ids = new Set(
    sql
      .all<{ id: string }>('SELECT id FROM datasets WHERE source_id=?', [source_id])
      .map((x) => x.id),
  );
  if (outputs.some((o) => !ids.has(o.dataset_id))) invalid('foreign source dataset output');
  validate_results(outputs, candidates as CandidateDatasetResult[]);
}
export function commit_observations(
  sql: TransactionSql,
  run_id: string,
  source_id: string,
  frozen_at: number,
  outputs: OutputMeasure[],
  candidates: NumericJSON<CandidateObservation[]>,
): number {
  validate_measures(sql, source_id, outputs, candidates);
  const by_id = new Map(outputs.map((x) => [x.measure_id, x]));
  let count = 0;
  for (const c of candidates) {
    if (c.value === null) {
      sql.run('DELETE FROM measure_observations WHERE measure_id=? AND date=?', [
        c.measure_id,
        c.date,
      ]);
      sql.run(
        `INSERT INTO measure_history_gaps(measure_id,date,run_id,reason) VALUES(?,?,?,?)
 ON CONFLICT(measure_id,date) DO UPDATE SET run_id=excluded.run_id,reason=excluded.reason`,
        [c.measure_id, c.date, run_id, c.unavailable_reason],
      );
      continue;
    }
    const output = by_id.get(c.measure_id)!;
    sql.run(
      `INSERT INTO measure_observations(measure_id,date,value,run_id,observed_at,definition_revision,
 period_start_date,period_end_date_exclusive,query_start_at,query_end_at,limitation)
 VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(measure_id,date) DO UPDATE SET value=excluded.value,
 run_id=excluded.run_id,manual_write_id=NULL,observed_at=excluded.observed_at,
 definition_revision=excluded.definition_revision,period_start_date=excluded.period_start_date,
 period_end_date_exclusive=excluded.period_end_date_exclusive,query_start_at=excluded.query_start_at,
 query_end_at=excluded.query_end_at,limitation=excluded.limitation`,
      [
        c.measure_id,
        c.date,
        number(c.value),
        run_id,
        frozen_at,
        output.definition_revision,
        null,
        null,
        c.query_start_at === null ? null : number(c.query_start_at),
        c.query_end_at === null ? null : number(c.query_end_at),
        c.limitation,
      ],
    );
    sql.run('DELETE FROM measure_history_gaps WHERE measure_id=? AND date=?', [
      c.measure_id,
      c.date,
    ]);
    count++;
  }
  return count;
}
export function commit_results(
  sql: TransactionSql,
  run_id: string,
  source_id: string,
  outputs: OutputDataset[],
  candidates: NumericJSON<CandidateDatasetResult[]>,
  now: number,
): number {
  validate_datasets(sql, source_id, outputs, candidates);
  if (
    sql.get<{ source_id: string }>('SELECT source_id FROM measure_source_runs WHERE id=?', [run_id])
      ?.source_id !== source_id
  )
    invalid('foreign source dataset run');
  const by_id = new Map(outputs.map((o) => [o.dataset_id, o]));
  let count = 0;
  for (const r of candidates) {
    sql.run('DELETE FROM dataset_rows WHERE dataset_id=? AND date=?', [r.dataset_id, r.date]);
    sql.run(
      `INSERT INTO dataset_results(dataset_id,date,run_id,definition_revision,output,collected_at,
 query_end_at,query_start_at,unavailable_reason,limitation) VALUES(?,?,?,?,?,?,?,?,?,?)
 ON CONFLICT(dataset_id,date) DO UPDATE SET run_id=excluded.run_id,
 definition_revision=excluded.definition_revision,output=excluded.output,collected_at=excluded.collected_at,
 query_end_at=excluded.query_end_at,query_start_at=excluded.query_start_at,
 unavailable_reason=excluded.unavailable_reason,limitation=excluded.limitation`,
      [
        r.dataset_id,
        r.date,
        run_id,
        by_id.get(r.dataset_id)!.definition_revision,
        stringify_python_json(by_id.get(r.dataset_id), { compact: true, ensure_ascii: true }),
        now,
        number(r.query_end_at),
        r.query_start_at === null ? null : number(r.query_start_at),
        r.unavailable_reason,
        r.limitation,
      ],
    );
    for (const row of r.rows)
      sql.run(
        'INSERT INTO dataset_rows(dataset_id,date,dimension_key,dimensions,field_values) VALUES(?,?,?,?,?)',
        [
          r.dataset_id,
          r.date,
          dimension_key(row.dimensions),
          stringify_python_json(row.dimensions, { ensure_ascii: true }),
          stringify_python_json(row.values, { ensure_ascii: true }),
        ],
      );
    for (const [dimension, items] of Object.entries(r.labels))
      for (const [code, label] of Object.entries(items))
        sql.run(
          `INSERT INTO dataset_category_labels(dataset_id,dimension,code,label) VALUES(?,?,?,?)
 ON CONFLICT(dataset_id,dimension,code) DO UPDATE SET label=excluded.label`,
          [r.dataset_id, dimension, code, label],
        );
    count += r.rows.length;
  }
  return count;
}

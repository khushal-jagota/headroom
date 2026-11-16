import { PlannerError } from '../core/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import type { ReadSql, TransactionSql, SqlValue } from '../core/database/contracts.ts';
import { parse_numeric_json } from '../core/http/numeric_json.ts';
import { pageFacts } from '../list_reads/logic/index.ts';
import type {
  Definitions,
  Measure,
  MeasureDate,
  Observation,
  Observations,
  OutputMeasure,
} from './contracts.ts';
import { pair_key } from './contracts.ts';
import { measure_json } from './serialization.ts';
import { calendar_date, invalid } from './logic/dates.ts';
import { dependencies, parse_formula } from './logic/formulas.ts';
import { observation_coverage } from './logic/coverage.ts';
export function hydrate_measure(row: Record<string, unknown>): Measure {
  return measure_json({
    ...row,
    formula: row.formula === null ? null : parse_formula(parse_numeric_json(String(row.formula))),
  } as unknown as Measure);
}
export function read_measure(sql: ReadSql, id: string): Measure {
  const row = sql.get<Record<string, unknown>>('SELECT * FROM measures WHERE id=?', [id]);
  if (!row) throw new PlannerError('not_found', 'measure not found');
  return hydrate_measure(row);
}
export function validate_page(limit: number, offset: number): void {
  if (
    !Number.isInteger(limit) ||
    limit < 1 ||
    limit > 100 ||
    !Number.isInteger(offset) ||
    offset < 0
  )
    throw invalid('invalid bounded list request');
}
export function list_measures(
  sql: ReadSql,
  project_id: string | null = null,
  source_id: string | null = null,
  limit = 25,
  offset = 0,
) {
  validate_page(limit, offset);
  const where: string[] = [],
    args: SqlValue[] = [];
  for (const [key, value] of [
    ['project_id', project_id],
    ['source_id', source_id],
  ] as const)
    if (value !== null) {
      where.push(`${key}=?`);
      args.push(value);
    }
  const clause = where.length ? ' WHERE ' + where.join(' AND ') : '';
  const count = sql.get<{ n: number }>('SELECT COUNT(*) AS n FROM measures' + clause, args)!.n;
  const measures = sql
    .all<Record<string, unknown>>(
      'SELECT * FROM measures' + clause + ' ORDER BY id LIMIT ? OFFSET ?',
      [...args, limit, offset],
    )
    .map(hydrate_measure);
  return { measures, page: pageFacts(count, measures.length, { limit, offset }) };
}
export function read_definitions(
  sql: ReadSql,
  roots: string[],
  proposed: Measure | null = null,
  missing_ok = false,
): Definitions {
  const result: Definitions = new Map(),
    visited = new Set<string>(),
    pending = [...roots];
  while (pending.length) {
    const id = pending.pop()!;
    if (visited.has(id)) continue;
    visited.add(id);
    let current: Measure;
    try {
      current = proposed?.id === id ? proposed : read_measure(sql, id);
    } catch (e) {
      if (missing_ok && e instanceof PlannerError && e.code === 'not_found') continue;
      throw e;
    }
    result.set(id, current);
    if (current.formula) pending.push(...dependencies(current.formula));
  }
  return result;
}
export function observation(
  row: Record<string, unknown>,
  measure: Measure,
  captured: OutputMeasure | null = null,
): Observation {
  const [coverage_status, coverage_reasons] = observation_coverage(
    measure,
    captured,
    String(row.date),
    row.query_start_at as number | null,
    row.query_end_at as number | null,
    row.limitation as string | null,
  );
  return {
    measure_id: String(row.measure_id),
    date: String(row.date),
    value: Number(row.value),
    run_id: row.run_id as string | null,
    observed_at: Number(row.observed_at),
    definition_revision: Number(row.definition_revision),
    period_interval:
      row.period_start_date === null
        ? null
        : {
            start_date: String(row.period_start_date),
            end_date_exclusive: String(row.period_end_date_exclusive),
          },
    manual_write_id: row.manual_write_id as string | null,
    query_start_at: row.query_start_at as number | null,
    query_end_at: row.query_end_at as number | null,
    limitation: row.limitation as string | null,
    coverage_status,
    coverage_reasons,
  };
}
export function hydrate_observations(
  sql: ReadSql,
  rows: Record<string, unknown>[],
  definitions: Definitions = new Map(),
): Observation[] {
  const captures = new Map<string, OutputMeasure>();
  for (const run of new Set(rows.map((r) => r.run_id).filter((v) => v !== null))) {
    const stored = sql.get<{ required_outputs: string }>(
      'SELECT required_outputs FROM measure_source_runs WHERE id=?',
      [String(run)],
    );
    if (stored)
      for (const output of JSON.parse(stored.required_outputs) as OutputMeasure[])
        captures.set(`${run}\0${output.measure_id}`, output);
  }
  return rows.map((row) =>
    observation(
      row,
      definitions.get(String(row.measure_id)) ?? read_measure(sql, String(row.measure_id)),
      captures.get(`${row.run_id}\0${row.measure_id}`) ?? null,
    ),
  );
}
export function read_demanded(
  sql: ReadSql,
  pairs: Map<string, MeasureDate>,
  definitions: Definitions,
): Observations {
  const rows: Record<string, unknown>[] = [];
  for (const [id, m] of definitions) {
    if (m.origin === 'assembled') continue;
    const days = [...pairs.values()]
      .filter((p) => p.measure_id === id)
      .map((p) => p.date)
      .sort();
    for (let start = 0; start < days.length; start += 400) {
      const chunk = days.slice(start, start + 400);
      rows.push(
        ...sql.all<Record<string, unknown>>(
          `SELECT * FROM measure_observations WHERE measure_id=? AND date IN (${chunk.map(() => '?').join(',')})`,
          [id, ...chunk],
        ),
      );
    }
  }
  return new Map(hydrate_observations(sql, rows, definitions).map((o) => [pair_key(o), o]));
}
export function read_observations(
  sql: ReadSql,
  id: string,
  first: string,
  last: string,
): Observation[] {
  if (calendar_date(first) > calendar_date(last)) throw invalid('measure date range is reversed');
  return hydrate_observations(
    sql,
    sql.all<Record<string, unknown>>(
      'SELECT * FROM measure_observations WHERE measure_id=? AND date>=? AND date<=? ORDER BY date',
      [id, first, last],
    ),
  );
}
export function latest_observations(sql: ReadSql, ids: string[]): Observation[] {
  const rows: Record<string, unknown>[] = [];
  for (const id of ids) {
    const row = sql.get<Record<string, unknown>>(
      'SELECT * FROM measure_observations WHERE measure_id=? ORDER BY date DESC LIMIT 1',
      [id],
    );
    if (row) rows.push(row);
  }
  return hydrate_observations(sql, rows);
}
export function earliest_retained_date(sql: ReadSql, id: string): string | null {
  return sql.get<{ day: string | null }>(
    `WITH RECURSIVE inputs(id) AS (SELECT ? UNION
 SELECT input_measure_id FROM measure_dependencies JOIN inputs ON measure_id=id)
 SELECT MIN(date) AS day FROM (SELECT date FROM measure_observations WHERE measure_id IN inputs
 UNION ALL SELECT date FROM measure_history_gaps WHERE measure_id IN inputs)`,
    [id],
  )!.day;
}
export function read_recorded_dates(
  sql: ReadSql,
  id: string,
  first: string,
  last: string,
): Set<string> {
  return new Set(
    sql
      .all<{ date: string }>(
        `SELECT date FROM measure_observations WHERE measure_id=? AND date>=? AND date<=?
 UNION SELECT date FROM measure_history_gaps WHERE measure_id=? AND date>=? AND date<=?`,
        [id, first, last, id, first, last],
      )
      .map((r) => r.date),
  );
}
export interface Source {
  id: string;
  name: string;
  scope: { project_id: string; account: string };
  responsible: Principal;
  enabled: boolean;
}
export function read_source(sql: ReadSql, id: string): Source {
  const r = sql.get<Record<string, unknown>>('SELECT * FROM measure_sources WHERE id=?', [id]);
  if (!r) throw new PlannerError('not_found', 'measure source does not exist');
  const kind = r.responsible_kind as Principal['kind'];
  return {
    id: String(r.id),
    name: String(r.name),
    scope: { project_id: String(r.project_id), account: String(r.account) },
    responsible: {
      kind,
      id:
        kind === 'chief'
          ? 'chief'
          : String(kind === 'goal' ? r.responsible_goal_id : r.responsible_manager_id),
    } as Principal,
    enabled: Boolean(r.enabled),
  };
}
export function bump_source(sql: TransactionSql, id: string, now: number): void {
  if (
    sql.run(
      'UPDATE measure_sources SET source_revision=source_revision+1,updated_at=? WHERE id=?',
      [now, id],
    ).changes !== 1
  )
    throw new PlannerError('not_found', 'measure source does not exist');
}
export function source_responsible(sql: ReadSql, id: string): Principal {
  return read_source(sql, id).responsible;
}
export function measure_source(
  sql: ReadSql,
  kind: 'measures' | 'datasets',
  id: string,
): string | null {
  if (kind === 'measures') return read_measure(sql, id).source_id;
  const row = sql.get<{ source_id: string }>('SELECT source_id FROM datasets WHERE id=?', [id]);
  if (!row) throw new PlannerError('not_found', 'dataset not found');
  return row.source_id;
}
export function read_health(sql: ReadSql, ids: string[], _now: number) {
  return [...new Set(ids)].map((id) => {
    const source = read_source(sql, id),
      success = sql.get<{ frozen_at: number; finished_at: number }>(
        `SELECT frozen_at,finished_at FROM measure_source_runs WHERE source_id=?
 AND mode='collection' AND status='succeeded' ORDER BY frozen_at DESC,rowid DESC LIMIT 1`,
        [id],
      ),
      episode = sql.get<{ latest_run_id: string }>(
        'SELECT latest_run_id FROM measure_source_failure_episodes WHERE source_id=? AND closed_at IS NULL',
        [id],
      );
    return {
      source_id: id,
      source_name: source.name,
      health: episode
        ? 'failed'
        : success
          ? 'healthy'
          : source.enabled
            ? 'never_collected'
            : 'disabled',
      latest_success_at: success?.finished_at ?? null,
      latest_failure_run_id: episode?.latest_run_id ?? null,
    };
  });
}

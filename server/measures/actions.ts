import { PlannerError } from '../core/contracts.ts';
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { newId } from '../core/ids.ts';
import { numeric_kind, numeric_value, stringify_numeric_json } from '../core/http/numeric_json.ts';
import type { Measure, MeasureDate } from './contracts.ts';
import { measure_json } from './serialization.ts';
import {
  bump_source,
  read_definitions,
  read_demanded,
  read_health,
  read_measure,
  read_source,
} from './data.ts';
import { date_range, invalid } from './logic/dates.ts';
import { ArithmeticDepthLimit, demanded_pairs, evaluate, Evaluation } from './logic/evaluation.ts';
import { dependencies, parse_formula, validate_graph } from './logic/formulas.ts';
export function validate_text(m: Measure): void {
  if (typeof m.name !== 'string' || !m.name.trim() || Array.from(m.name).length > 200)
    throw invalid('invalid measure name');
  if (typeof m.unit !== 'string' || Array.from(m.unit).length > 80)
    throw invalid('invalid measure unit');
}
function edges(sql: TransactionSql, m: Measure): void {
  sql.run('DELETE FROM measure_dependencies WHERE measure_id=?', [m.id]);
  if (m.formula)
    for (const child of [...dependencies(m.formula)].sort())
      sql.run('INSERT INTO measure_dependencies(measure_id,input_measure_id) VALUES(?,?)', [
        m.id,
        child,
      ]);
}
export function create_measure(
  sql: TransactionSql,
  body: Omit<
    Measure,
    'id' | 'definition_revision' | 'created_at' | 'updated_at' | 'write_revision'
  >,
  now: number,
): Measure {
  const m: Measure = {
    ...body,
    id: newId('measure'),
    formula: body.formula === null ? null : parse_formula(body.formula),
    definition_revision: 1,
    created_at: now,
    updated_at: now,
    write_revision: 0,
  };
  validate_text(m);
  if (!sql.get('SELECT 1 FROM projects WHERE id=?', [m.project_id]))
    throw invalid('measure Project does not exist');
  validate_graph(m.id, read_definitions(sql, [m.id], m));
  if (m.source_id !== null && read_source(sql, m.source_id).scope.project_id !== m.project_id)
    throw invalid('measure and source Projects differ');
  sql.run(
    `INSERT INTO measures(id,project_id,name,unit,origin,kind,timezone,period,
 source_id,formula,definition_revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    [
      m.id,
      m.project_id,
      m.name,
      m.unit,
      m.origin,
      m.kind,
      m.timezone,
      m.period,
      m.source_id,
      m.formula === null ? null : stringify_numeric_json(m.formula),
      1,
      now,
      now,
    ],
  );
  edges(sql, m);
  if (m.source_id) bump_source(sql, m.source_id, now);
  return measure_json(m);
}
export function update_measure(
  sql: TransactionSql,
  id: string,
  body: Record<string, unknown>,
  now: number,
): Measure {
  const current = read_measure(sql, id);
  if (
    numeric_kind(body.expected_revision) !== 'integer' ||
    numeric_value(body.expected_revision) !== current.definition_revision
  )
    throw new PlannerError('stale_claim', 'measure definition changed');
  const edits = { ...body };
  delete edits.expected_revision;
  const m = {
    ...current,
    ...edits,
    formula: 'formula' in body ? parse_formula(body.formula) : current.formula,
    definition_revision: current.definition_revision + 1,
    updated_at: now,
  } as Measure;
  validate_text(m);
  validate_graph(id, read_definitions(sql, [id], m));
  sql.run(
    `UPDATE measures SET name=?,unit=?,kind=?,timezone=?,period=?,formula=?,
 definition_revision=?,updated_at=? WHERE id=?`,
    [
      m.name,
      m.unit,
      m.kind,
      m.timezone,
      m.period,
      m.formula === null ? null : stringify_numeric_json(m.formula),
      m.definition_revision,
      now,
      id,
    ],
  );
  edges(sql, m);
  if (m.source_id) bump_source(sql, m.source_id, now);
  return measure_json(m);
}
export function delete_measure(sql: TransactionSql, id: string, now: number): void {
  const m = read_measure(sql, id);
  if (
    sql.get(
      `SELECT 1 FROM measure_dependencies WHERE input_measure_id=? UNION ALL SELECT
 1 FROM measure_observations WHERE measure_id=? UNION ALL SELECT 1 FROM
 measure_history_gaps WHERE measure_id=? LIMIT 1`,
      [id, id, id],
    )
  )
    throw invalid('referenced or retained measure cannot be deleted');
  sql.run('DELETE FROM measures WHERE id=?', [id]);
  if (m.source_id) bump_source(sql, m.source_id, now);
}
export function read_values(
  sql: ReadSql,
  body: { measure_ids: string[]; first_date: string; last_date: string; limit?: unknown },
  now: number,
) {
  const ids = body.measure_ids;
  if (!ids.length || new Set(ids).size !== ids.length)
    throw invalid('measure read requires distinct measure IDs');
  let max: number | undefined;
  if (body.limit !== undefined) {
    if (numeric_kind(body.limit) !== 'integer' || numeric_value(body.limit)! < 1)
      throw invalid('invalid measure read limit');
    max = numeric_value(body.limit);
  }
  const days = date_range(body.first_date, body.last_date),
    roots: MeasureDate[] = [];
  outer: for (const measure_id of ids)
    for (const date of days) {
      if (max !== undefined && roots.length >= max) break outer;
      roots.push({ measure_id, date });
    }
  const definitions = read_definitions(sql, ids);
  for (const id of ids) validate_graph(id, definitions);
  let values;
  try {
    const pairs = demanded_pairs(roots, definitions, now);
    values = evaluate(roots, definitions, read_demanded(sql, pairs, definitions), now);
  } catch (e) {
    if (!(e instanceof ArithmeticDepthLimit)) throw e;
    const c = new Evaluation(definitions, new Map(), now);
    values = roots.map((p) => ({
      ...c._unavailable(p, 'arithmetic_limit'),
      coverage_status: 'not_applicable' as const,
    }));
  }
  const source_ids = [
    ...new Set(
      [...definitions.values()].map((m) => m.source_id).filter((v): v is string => v !== null),
    ),
  ].sort();
  return { values, sources: read_health(sql, source_ids, now) };
}

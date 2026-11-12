import type { ReadSql } from '../core/database/contracts.ts';
import type { SqlValue } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { pageFacts } from '../list_reads/logic/index.ts';
import * as measures from '../measures/data.ts';
import * as data from './data.ts';
import type { MeasureSource, SourceRun } from './contracts.ts';
import { LATEST_VALUES_PREVIEW, MAX_PAGE_LIMIT } from './configuration.ts';
export function source_summary(s: MeasureSource) {
  return {
    id: s.id,
    name: s.name,
    scope: s.scope,
    responsible: s.responsible,
    source_revision: s.source_revision,
    enabled: s.enabled,
    local_time: s.local_time,
    active_script_revision: s.active_script?.revision ?? null,
  };
}
function run_summary(r: SourceRun) {
  return {
    id: r.id,
    mode: r.mode,
    status: r.status,
    created_at: r.created_at,
    finished_at: r.finished_at,
    failure: r.failure,
    observation_count: r.observation_count,
    dataset_result_count: r.dataset_result_count,
    dataset_row_count: r.dataset_row_count,
  };
}
export function status(sql: ReadSql, id: string, { now }: { now: number }) {
  const source = data.read_source(sql, id),
    health = data.read_health(sql, [id], now)[0]!,
    latest = sql.get<{ id: string }>(
      'SELECT id FROM measure_source_runs WHERE source_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',
      [id],
    ),
    running = sql.get<{ id: string }>(
      "SELECT id FROM measure_source_runs WHERE source_id=? AND status='running'",
      [id],
    ),
    outputs = measures.list_measures(sql, null, id, LATEST_VALUES_PREVIEW, 0),
    observations = new Map(
      measures
        .latest_observations(
          sql,
          outputs.measures.map((x) => x.id),
        )
        .map((x) => [x.measure_id, x]),
    ),
    episode = data.read_failure_episode(sql, id);
  return {
    source: source_summary(source),
    health: health.health,
    latest_run: latest ? run_summary(data.read_run(sql, latest.id)) : null,
    latest_success_at: health.latest_success_at,
    latest_values: outputs.measures.map((m) => ({
      measure_id: m.id,
      name: m.name,
      unit: m.unit,
      kind: m.kind,
      observation: observations.get(m.id) ?? null,
    })),
    output_count: outputs.page.match_count,
    failure_episode: episode,
    collection_failure: episode ? data.read_run(sql, episode.latest_run_id).failure : null,
    running_run_id: running?.id ?? null,
    dataset_output_count: sql.get<{ n: number }>(
      'SELECT COUNT(*) AS n FROM datasets WHERE source_id=?',
      [id],
    )!.n,
  };
}
export function validate_page(limit: number, offset: number): void {
  if (
    !Number.isInteger(limit) ||
    limit < 1 ||
    limit > MAX_PAGE_LIMIT ||
    !Number.isInteger(offset) ||
    offset < 0
  )
    throw new PlannerError('validation', 'invalid bounded source list');
}
export function source_ids(
  sql: ReadSql,
  { project_id, limit, offset }: { project_id: string | null; limit: number; offset: number },
) {
  validate_page(limit, offset);
  const where = project_id !== null ? ' WHERE project_id=?' : '',
    args: SqlValue[] = project_id === null ? [] : [project_id],
    count = sql.get<{ n: number }>('SELECT COUNT(*) AS n FROM measure_sources' + where, args)!.n,
    rows = sql
      .all<{ id: string }>(
        'SELECT id FROM measure_sources' + where + ' ORDER BY id LIMIT ? OFFSET ?',
        [...args, limit, offset],
      )
      .map((r) => r.id);
  return { rows, page: pageFacts(count, rows.length, { limit, offset }) };
}
export function list_json(
  sql: ReadSql,
  options: { project_id: string | null; limit: number; offset: number; now: number },
) {
  const p = source_ids(sql, options);
  return { sources: p.rows.map((id) => status(sql, id, { now: options.now })), page: p.page };
}
export function runs_json(
  sql: ReadSql,
  id: string,
  { limit, offset }: { limit: number; offset: number },
) {
  validate_page(limit, offset);
  data.read_source(sql, id);
  const count = sql.get<{ n: number }>(
      'SELECT COUNT(*) AS n FROM measure_source_runs WHERE source_id=?',
      [id],
    )!.n,
    rows = sql
      .all<{ id: string }>(
        'SELECT id FROM measure_source_runs WHERE source_id=? ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?',
        [id, limit, offset],
      )
      .map((r) => run_summary(data.read_run(sql, r.id)));
  return { runs: rows, page: pageFacts(count, rows.length, { limit, offset }) };
}

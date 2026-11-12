import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import type {
  CandidateObservation,
  FailureEpisode,
  MeasureSource,
  RunFailure,
  RunMetrics,
  SourceRun,
  SourceRunDetail,
} from './contracts.ts';
import type { OutputMeasure } from '../measures/contracts.ts';
import type { CandidateDatasetResult, OutputDataset } from '../datasets/contracts.ts';
import { decode_record } from './records.ts';
export { read_health } from '../measures/data.ts';
type Row = Record<string, any>;
export function read_source(sql: ReadSql, id: string): MeasureSource {
  const r = sql.get<Row>('SELECT * FROM measure_sources WHERE id=?', [id]);
  if (!r) throw new PlannerError('not_found', 'measure source does not exist');
  return {
    id: r.id,
    name: r.name,
    scope: { project_id: r.project_id, account: r.account },
    responsible: {
      kind: r.responsible_kind,
      id:
        r.responsible_kind === 'chief'
          ? 'chief'
          : r.responsible_kind === 'goal'
            ? r.responsible_goal_id
            : r.responsible_manager_id,
    },
    responsibility_revision: r.responsibility_revision,
    enabled: Boolean(r.enabled),
    local_time: r.local_time,
    source_revision: r.source_revision,
    active_script:
      r.active_script === null
        ? null
        : {
            revision: r.active_script_revision,
            text: r.active_script,
            sha256: r.active_script_sha256,
            authored_by: { kind: r.active_script_author_kind, id: r.active_script_author_id },
            created_at: r.active_script_created_at,
          },
    created_at: r.created_at,
    updated_at: r.updated_at,
  };
}
export function bump_source_revision(
  sql: TransactionSql,
  id: string,
  { now }: { now: number },
): void {
  if (
    sql.run(
      'UPDATE measure_sources SET source_revision=source_revision+1,updated_at=? WHERE id=?',
      [now, id],
    ).changes !== 1
  )
    throw new PlannerError('not_found', 'measure source does not exist');
}
export function read_run(sql: ReadSql, id: string): SourceRun {
  const r = sql.get<Row>('SELECT * FROM measure_source_runs WHERE id=?', [id]);
  if (!r) throw new PlannerError('not_found', 'source run does not exist');
  return {
    id: r.id,
    source_id: r.source_id,
    mode: r.mode,
    trigger: r.trigger,
    status: r.status,
    source_revision: r.source_revision,
    script_revision: r.script_revision,
    script_sha256: r.script_sha256,
    frozen_at: r.frozen_at,
    required_outputs: JSON.parse(r.required_outputs) as OutputMeasure[],
    history:
      r.history_first_date === null
        ? null
        : { first_date: r.history_first_date, last_date: r.history_last_date },
    schedule_date: r.schedule_date,
    schedule_time: r.schedule_time,
    created_at: r.created_at,
    started_at: r.started_at,
    finished_at: r.finished_at,
    failure: r.failure === null ? null : (JSON.parse(r.failure) as RunFailure),
    metrics: r.metrics === null ? null : (JSON.parse(r.metrics) as RunMetrics),
    observation_count: r.observation_count,
    dataset_outputs: JSON.parse(r.dataset_outputs) as OutputDataset[],
    dataset_result_count: r.dataset_result_count,
    dataset_row_count: r.dataset_row_count,
  };
}
export function read_run_detail(sql: ReadSql, id: string): SourceRunDetail {
  const run = read_run(sql, id),
    r = sql.get<{ test_candidates: string | null; test_dataset_results: string | null }>(
      'SELECT test_candidates,test_dataset_results FROM measure_source_runs WHERE id=?',
      [id],
    )!;
  return {
    run,
    test_candidates:
      r.test_candidates === null ? null : decode_record<CandidateObservation[]>(r.test_candidates),
    test_dataset_results:
      r.test_dataset_results === null
        ? null
        : decode_record<CandidateDatasetResult[]>(r.test_dataset_results),
  };
}
export function read_failure_episode(sql: ReadSql, id: string): FailureEpisode | null {
  return (
    sql.get<FailureEpisode>(
      'SELECT source_id,episode,opened_at,latest_run_id,closed_at FROM measure_source_failure_episodes WHERE source_id=? AND closed_at IS NULL',
      [id],
    ) ?? null
  );
}

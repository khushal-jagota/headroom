import { PlannerError } from '../core/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { newId } from '../core/ids.ts';
import { calendar_date } from '../measures/logic/dates.ts';
import { prepare_outputs as dataset_outputs } from '../datasets/data/index.ts';
import { failure } from './contracts.ts';
import type {
  HistoryWindow,
  MeasureSource,
  RunFailure,
  RunMode,
  RunResult,
  RunStatus,
  RunTrigger,
  SourceRun,
  SourceRunDetail,
} from './contracts.ts';
import { encode_record } from './records.ts';
import * as data from './data.ts';
import { record_failure } from './notices.ts';
import * as retention from './retention.ts';
function selected_script(source: MeasureSource) {
  if (!source.active_script)
    throw new PlannerError('validation', 'the selected source script is absent');
  return source.active_script;
}
function admit(
  sql: TransactionSql,
  source: MeasureSource,
  {
    mode,
    trigger,
    history,
    now,
    slot = null,
  }: {
    mode: RunMode;
    trigger: RunTrigger;
    history: HistoryWindow | null;
    now: number;
    slot?: [string, string] | null;
  },
): SourceRun {
  if (history !== null && calendar_date(history.first_date) > calendar_date(history.last_date))
    throw new PlannerError('validation', 'history starts after its end date');
  const script = selected_script(source),
    outputs = retention.prepare_measure_outputs(sql, source.id, now),
    datasets = dataset_outputs(sql, source.id, now),
    id = newId('source_run');
  sql.run(
    `INSERT INTO measure_source_runs(id,source_id,mode,trigger,status,source_revision,
 script_revision,script_sha256,frozen_at,required_outputs,history_first_date,history_last_date,
 schedule_date,schedule_time,created_at,dataset_outputs) VALUES (?,?,?,?,'pending',?,?,?,?,?,?,?,?,?,?,?)`,
    [
      id,
      source.id,
      mode,
      trigger,
      source.source_revision,
      script.revision,
      script.sha256,
      now,
      encode_record(outputs),
      history?.first_date ?? null,
      history?.last_date ?? null,
      slot?.[0] ?? null,
      slot?.[1] ?? null,
      now,
      encode_record(datasets),
    ],
  );
  return data.read_run(sql, id);
}
export function request_run(
  sql: TransactionSql,
  id: string,
  {
    mode,
    history,
    expected_script_revision,
    now,
  }: {
    mode: RunMode;
    history: HistoryWindow | null;
    expected_script_revision: number;
    principal: Principal;
    now: number;
  },
): SourceRun {
  const source = data.read_source(sql, id);
  if (selected_script(source).revision !== expected_script_revision)
    throw new PlannerError('stale_claim', 'selected source script changed');
  return admit(sql, source, { mode, trigger: mode === 'test' ? 'test' : 'manual', history, now });
}
export function admit_scheduled_run(
  sql: TransactionSql,
  id: string,
  { local_now, now, slot }: { local_now: Date; now: number; slot?: [string, string] },
): SourceRun {
  const source = data.read_source(sql, id),
    date =
      slot?.[0] ??
      `${String(local_now.getFullYear()).padStart(4, '0')}-${String(local_now.getMonth() + 1).padStart(2, '0')}-${String(local_now.getDate()).padStart(2, '0')}`,
    time =
      slot?.[1] ??
      `${String(local_now.getHours()).padStart(2, '0')}:${String(local_now.getMinutes()).padStart(2, '0')}`;
  if (!source.enabled || source.local_time !== time)
    throw new PlannerError('validation', 'source is not due in this slot');
  const existing = sql.get<{ id: string }>(
    "SELECT id FROM measure_source_runs WHERE source_id=? AND trigger='scheduled' AND schedule_date=? AND schedule_time=?",
    [id, date, time],
  );
  if (existing) return data.read_run(sql, existing.id);
  return admit(sql, source, {
    mode: 'collection',
    trigger: 'scheduled',
    history: null,
    now,
    slot: [date, time],
  });
}
export function current_definition(sql: ReadSql, run: SourceRun): boolean {
  try {
    const script = selected_script(data.read_source(sql, run.source_id));
    return script.revision === run.script_revision && script.sha256 === run.script_sha256;
  } catch (e) {
    if (e instanceof PlannerError) return false;
    throw e;
  }
}
function fail(
  sql: TransactionSql,
  run: SourceRun,
  f: RunFailure,
  { now, status = 'failed' }: { now: number; status?: RunStatus },
): void {
  sql.run('UPDATE measure_source_runs SET status=?,failure=?,finished_at=? WHERE id=?', [
    status,
    encode_record(f),
    now,
    run.id,
  ]);
  if (run.mode === 'collection') record_failure(sql, data.read_run(sql, run.id), { now });
}
export function claim_run(
  sql: TransactionSql,
  id: string,
  { now }: { now: number },
): SourceRun | null {
  const run = data.read_run(sql, id);
  if (run.status !== 'pending') return null;
  if (
    sql.get("SELECT 1 FROM measure_source_runs WHERE source_id=? AND status='running'", [
      run.source_id,
    ])
  )
    return null;
  if (!current_definition(sql, run)) {
    fail(sql, run, failure('stale_definition'), { now });
    return null;
  }
  sql.run("UPDATE measure_source_runs SET status='running',started_at=? WHERE id=?", [now, id]);
  return data.read_run(sql, id);
}
export function complete_run(
  sql: TransactionSql,
  id: string,
  result: RunResult,
  { now }: { now: number },
): SourceRunDetail {
  const run = data.read_run(sql, id);
  if (run.status !== 'running')
    throw new PlannerError('stale_claim', 'only a running source run can complete');
  let f = result.failure,
    encoded: string | null = null,
    encoded_datasets: string | null = null,
    count = 0,
    dataset_count = 0,
    row_count = 0;
  if (f === null && !current_definition(sql, run)) f = failure('stale_definition');
  if (f === null) {
    try {
      encoded = encode_record(result.observations);
      encoded_datasets = encode_record(result.dataset_results);
      retention.validate_measures(sql, run.source_id, run.required_outputs, result.observations);
      retention.validate_datasets(sql, run.source_id, run.dataset_outputs, result.dataset_results);
    } catch (e) {
      if (e instanceof PlannerError)
        f = failure(
          e.message === 'stale measure definition' ? 'stale_definition' : 'invalid_output',
        );
      else if (e instanceof TypeError || e instanceof RangeError) f = failure('invalid_output');
      else throw e;
    }
  }
  if (f !== null)
    fail(sql, run, f, { now, status: f.code === 'interrupted' ? 'interrupted' : 'failed' });
  else {
    if (run.mode === 'collection') {
      count = retention.commit_observations(
        sql,
        run.id,
        run.source_id,
        run.frozen_at,
        run.required_outputs,
        result.observations,
      );
      row_count = retention.commit_results(
        sql,
        run.id,
        run.source_id,
        run.dataset_outputs,
        result.dataset_results,
        now,
      );
      dataset_count = result.dataset_results.length;
      sql.run(
        'UPDATE measure_source_failure_episodes SET closed_at=? WHERE source_id=? AND closed_at IS NULL',
        [now, run.source_id],
      );
    }
    sql.run(
      `UPDATE measure_source_runs SET status='succeeded',finished_at=?,observation_count=?,
 dataset_result_count=?,dataset_row_count=?,test_candidates=?,test_dataset_results=? WHERE id=?`,
      [
        now,
        count,
        dataset_count,
        row_count,
        run.mode === 'test' ? encoded : null,
        run.mode === 'test' ? encoded_datasets : null,
        run.id,
      ],
    );
  }
  sql.run('UPDATE measure_source_runs SET metrics=? WHERE id=?', [
    encode_record(result.metrics),
    run.id,
  ]);
  return data.read_run_detail(sql, id);
}
export function interrupt_abandoned_runs(sql: TransactionSql, { now }: { now: number }): number {
  const rows = sql.all<{ id: string }>("SELECT id FROM measure_source_runs WHERE status='running'");
  for (const r of rows)
    fail(sql, data.read_run(sql, r.id), failure('interrupted'), { now, status: 'interrupted' });
  return rows.length;
}

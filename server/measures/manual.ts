import { PlannerError } from '../core/contracts.ts';
import { numeric_value } from '../core/http/numeric_json.ts';
import type { Principal } from '../core/contracts.ts';
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import type {
  ManualMeasureState,
  ManualWriteBody,
  ManualWriteReceipt,
  Measure,
} from './contracts.ts';
import { observation, read_measure } from './data.ts';
import { calendar_date, invalid, local_date } from './logic/dates.ts';
import { accepted_value, fingerprint, parse_write } from './logic/manual.ts';
export function snapshot(sql: ReadSql, m: Measure, day: string, now: number): ManualMeasureState {
  if (m.origin !== 'manual') throw invalid('manual controls require a manual measure');
  calendar_date(day);
  const row = sql.get<Record<string, unknown>>(
      'SELECT * FROM measure_observations WHERE measure_id=? AND date<=? ORDER BY date DESC LIMIT 1',
      [m.id, day],
    ),
    latest = row ? observation(row, m) : null;
  return {
    measure_id: m.id,
    definition_revision: m.definition_revision,
    write_revision: m.write_revision,
    timezone: m.timezone,
    current_date: local_date(now, m.timezone),
    date: day,
    observation: latest?.date === day ? latest : null,
    latest_observation: latest,
  };
}
export function read_state(
  sql: ReadSql,
  id: string,
  day: string | null,
  now: number,
): ManualMeasureState {
  const m = read_measure(sql, id);
  return snapshot(sql, m, day ?? local_date(now, m.timezone), now);
}
function chart_measure_ids(primary: unknown, page: unknown): Set<string> {
  const found = new Set<string>();
  const chart = (raw: unknown) => {
    if (!raw || typeof raw !== 'object') return;
    const c = raw as Record<string, unknown>;
    if (typeof c.measure_id === 'string') found.add(c.measure_id);
    const b = c.binding as { kind?: string; inputs?: { measure_id: string }[] } | undefined;
    if (b?.kind === 'scalar_inputs')
      for (const input of b.inputs ?? []) found.add(input.measure_id);
  };
  chart(primary);
  const walk = (raw: unknown) => {
    if (Array.isArray(raw)) {
      for (const v of raw) walk(v);
      return;
    }
    if (!raw || typeof raw !== 'object') return;
    const o = raw as Record<string, unknown>;
    if (o.type === 'chart') chart(o);
    for (const v of Object.values(o)) if (v && typeof v === 'object') walk(v);
  };
  walk(page);
  return found;
}
export function manager_manual_measure(sql: ReadSql, id: string, measure_id: string): boolean {
  if (read_measure(sql, measure_id).origin !== 'manual') return false;
  const row = sql.get<{ page: string | null }>('SELECT page FROM managers WHERE id=?', [id]);
  if (!row) throw new PlannerError('not_found', 'manager not found', { id });
  if (row.page !== null && chart_measure_ids(null, JSON.parse(row.page)).has(measure_id))
    return true;
  return sql
    .all<{ primary_chart: string | null; page: string }>(
      "SELECT primary_chart,page FROM goals WHERE holder_kind='manager' AND holder_id=? ORDER BY id",
      [id],
    )
    .some((goal) =>
      chart_measure_ids(
        goal.primary_chart === null ? null : JSON.parse(goal.primary_chart),
        JSON.parse(goal.page),
      ).has(measure_id),
    );
}
function require_manual_author(sql: ReadSql, actor: Principal, id: string): void {
  if (actor.kind !== 'goal') return;
  const row = sql.get<{ primary_chart: string | null; page: string }>(
    'SELECT primary_chart,page FROM goals WHERE id=?',
    [actor.id],
  );
  if (!row) throw new PlannerError('not_found', 'goal not found');
  if (
    read_measure(sql, id).origin !== 'manual' ||
    !chart_measure_ids(
      row.primary_chart ? JSON.parse(row.primary_chart) : null,
      JSON.parse(row.page),
    ).has(id)
  )
    throw new PlannerError('agent_forbidden', 'goal can write only its bound manual measures');
}
function receipt(r: Record<string, unknown>): ManualWriteReceipt {
  return {
    request_id: String(r.request_id),
    measure_id: String(r.measure_id),
    actor: { kind: r.actor_kind, id: r.actor_id } as Principal,
    payload_fingerprint: String(r.payload_fingerprint),
    date: String(r.date),
    value: Number(r.value),
    write_revision: Number(r.write_revision),
    accepted_at: Number(r.accepted_at),
  };
}
export function write_value(
  sql: TransactionSql,
  id: string,
  body: ManualWriteBody,
  actor: Principal,
  now: number,
) {
  const request = parse_write(body),
    payload = fingerprint(id, request);
  require_manual_author(sql, actor, id);
  const accepted = sql.get<Record<string, unknown>>(
    'SELECT * FROM manual_measure_writes WHERE request_id=?',
    [request.request_id],
  );
  if (accepted) {
    const r = receipt(accepted);
    if (r.actor.kind !== actor.kind || r.actor.id !== actor.id || r.payload_fingerprint !== payload)
      throw new PlannerError('stale_claim', 'manual request ID was already used');
    return {
      receipt: r,
      state: snapshot(sql, read_measure(sql, id), request.date, now),
      replayed: true,
    };
  }
  const m = read_measure(sql, id),
    state = snapshot(sql, m, request.date, now);
  if (numeric_value(request.expected_definition_revision) !== m.definition_revision)
    throw new PlannerError('stale_claim', 'measure definition changed', { state });
  if (
    request.operation === 'replace' &&
    numeric_value(request.expected_write_revision) !== m.write_revision
  )
    throw new PlannerError('stale_claim', 'measure value changed', { state });
  const value = accepted_value(
    request,
    request.basis === 'date' ? state.observation : state.latest_observation,
  );
  const r: ManualWriteReceipt = {
    request_id: request.request_id,
    measure_id: id,
    actor,
    payload_fingerprint: payload,
    date: request.date,
    value,
    write_revision: m.write_revision + 1,
    accepted_at: now,
  };
  sql.run(
    `INSERT INTO manual_measure_writes(request_id,measure_id,actor_kind,actor_id,
 payload_fingerprint,date,value,write_revision,accepted_at) VALUES(?,?,?,?,?,?,?,?,?)`,
    [r.request_id, id, actor.kind, actor.id, payload, r.date, value, r.write_revision, now],
  );
  sql.run(
    `INSERT INTO measure_observations(measure_id,date,value,run_id,observed_at,
 definition_revision,manual_write_id) VALUES(?,?,?,NULL,?,?,?)
 ON CONFLICT(measure_id,date) DO UPDATE SET value=excluded.value,run_id=NULL,
 observed_at=excluded.observed_at,definition_revision=excluded.definition_revision,
 manual_write_id=excluded.manual_write_id,period_start_date=NULL,
 period_end_date_exclusive=NULL,query_start_at=NULL,query_end_at=NULL,limitation=NULL`,
    [id, r.date, value, now, m.definition_revision, r.request_id],
  );
  sql.run('UPDATE measures SET write_revision=?,updated_at=? WHERE id=?', [
    r.write_revision,
    now,
    id,
  ]);
  return {
    receipt: r,
    state: snapshot(sql, read_measure(sql, id), request.date, now),
    replayed: false,
  };
}

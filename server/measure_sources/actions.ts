import { createHash } from 'node:crypto';
import { PlannerError } from '../core/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import type { TransactionSql } from '../core/database/contracts.ts';
import { newId } from '../core/ids.ts';
import * as data from './data.ts';
import type {
  MeasureSource,
  SourceCreate,
  SourceScheduleBody,
  SourceScriptBody,
} from './contracts.ts';
import { set_responsible as transfer_responsibility } from './responsibility.ts';
type ActorTime = { principal: Principal; now: number };
function revision(source: MeasureSource, expected: number): void {
  if (!Number.isInteger(expected) || expected !== source.source_revision)
    throw new PlannerError('stale_claim', 'source revision changed');
}
function responsibility(sql: TransactionSql, p: Principal): void {
  if (!['chief', 'manager', 'goal'].includes(p.kind) || (p.kind === 'chief' && p.id !== 'chief'))
    throw new PlannerError('validation', 'a source needs Chief, Manager, or goal responsibility');
  if (p.kind === 'manager' && !sql.get('SELECT 1 FROM managers WHERE id=?', [p.id]))
    throw new PlannerError('validation', 'responsible Manager does not exist');
  if (p.kind === 'goal' && !sql.get('SELECT 1 FROM goals WHERE id=?', [p.id]))
    throw new PlannerError('validation', 'responsible goal does not exist');
}
export function create_source(
  sql: TransactionSql,
  request: SourceCreate,
  { now }: ActorTime,
): MeasureSource {
  if (
    !request.name.trim() ||
    [...request.name].length > 200 ||
    !request.scope.account.trim() ||
    [...request.scope.account].length > 200
  )
    throw new PlannerError('validation', 'source needs a bounded name and account');
  responsibility(sql, request.responsible);
  if (!sql.get('SELECT 1 FROM projects WHERE id=?', [request.scope.project_id]))
    throw new PlannerError('validation', 'source Project does not exist');
  const id = newId('source'),
    p = request.responsible;
  sql.run(
    `INSERT INTO measure_sources(id,name,project_id,account,responsible_kind,
 responsible_manager_id,responsible_goal_id,responsibility_revision,source_revision,
 created_at,updated_at) VALUES (?,?,?,?,?,?,?,1,1,?,?)`,
    [
      id,
      request.name.trim(),
      request.scope.project_id,
      request.scope.account,
      p.kind,
      p.kind === 'manager' ? p.id : null,
      p.kind === 'goal' ? p.id : null,
      now,
      now,
    ],
  );
  return data.read_source(sql, id);
}
export function set_schedule(
  sql: TransactionSql,
  id: string,
  body: SourceScheduleBody,
  { now }: ActorTime,
): MeasureSource {
  if (typeof body.enabled !== 'boolean')
    throw new PlannerError('validation', 'enabled must be a boolean');
  const time = body.local_time;
  if (time !== null && !/^(?:[01]\p{Nd}|2[0-3]):[0-5]\p{Nd}$/u.test(time))
    throw new PlannerError('validation', 'local_time must be an exact 24-hour HH:MM value', {
      local_time: time,
    });
  const source = data.read_source(sql, id);
  revision(source, body.expected_revision);
  if (body.enabled && (!source.active_script || time === null))
    throw new PlannerError('validation', 'daily collection needs a script and schedule');
  sql.run('UPDATE measure_sources SET enabled=?,local_time=? WHERE id=?', [
    Number(body.enabled),
    time,
    id,
  ]);
  data.bump_source_revision(sql, id, { now });
  return data.read_source(sql, id);
}
export function set_responsible(
  sql: TransactionSql,
  id: string,
  p: Principal,
  {
    expected_responsibility_revision,
    now,
  }: ActorTime & { expected_responsibility_revision: number },
): MeasureSource {
  transfer_responsibility(sql, id, p, expected_responsibility_revision, now);
  return data.read_source(sql, id);
}

export function submit_script(
  sql: TransactionSql,
  id: string,
  body: SourceScriptBody,
  { principal, now }: ActorTime,
): MeasureSource {
  if (!body.text.trim()) throw new PlannerError('validation', 'source script is empty');
  revision(data.read_source(sql, id), body.expected_revision);
  sql.run(
    `UPDATE measure_sources SET active_script=?,active_script_revision=script_revision_counter+1,
 script_revision_counter=script_revision_counter+1,active_script_sha256=?,active_script_author_kind=?,
 active_script_author_id=?,active_script_created_at=?,updated_at=? WHERE id=?`,
    [
      body.text,
      createHash('sha256').update(body.text).digest('hex'),
      principal.kind,
      principal.id,
      now,
      now,
      id,
    ],
  );
  data.bump_source_revision(sql, id, { now });
  return data.read_source(sql, id);
}
export function delete_source(
  sql: TransactionSql,
  id: string,
  _actor: { principal: Principal },
): void {
  data.read_source(sql, id);
  if (
    sql.get('SELECT 1 FROM measures WHERE source_id=? LIMIT 1', [id]) ||
    sql.get('SELECT 1 FROM datasets WHERE source_id=? LIMIT 1', [id]) ||
    sql.get("SELECT 1 FROM measure_source_runs WHERE source_id=? AND mode!='test' LIMIT 1", [id])
  )
    throw new PlannerError(
      'validation',
      'source has retained measures, datasets, or runs; disable it instead',
    );
  sql.run("DELETE FROM measure_source_runs WHERE source_id=? AND mode='test'", [id]);
  sql.run('DELETE FROM measure_sources WHERE id=?', [id]);
}

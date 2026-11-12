import { PlannerError } from '../core/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import type { Clock } from '../core/clock.ts';
import type { Database, SqlValue, TransactionSql } from '../core/database/contracts.ts';
import { requireAbove } from '../core/authority/service.ts';
import { newId } from '../core/ids.ts';
import {
  clone_numeric_json,
  JsonNumber,
  stringify_python_json,
} from '../core/http/numeric_json.ts';
import { read_dataset } from '../datasets/data/index.ts';
import { read_definitions, read_measure } from '../measures/data.ts';
import { validate_graph } from '../measures/logic/formulas.ts';
import { read_chief_settings } from '../worker_settings/service.ts';
import { create_manager } from '../managers/data.ts';
import {
  purge_quarantined_files,
  quarantine_goal_files,
  restore_quarantined_files,
} from '../files/lifecycle.ts';
import type { QuarantinedFiles } from '../files/lifecycle.ts';
import { set_responsible } from '../measure_sources/responsibility.ts';
import type { ChartBlock, Goal, PageDocument } from './contracts.ts';
import { read_goal } from './data.ts';
import { server_date, validate_iso_calendar_date } from './calendar.ts';
import {
  blocks,
  charts,
  dataset_ids,
  expectation_measure_ids,
  measure_ids,
  prepare_document,
  validate_chart,
} from './logic/documents.ts';
import { validate_category_references } from './logic/categories.ts';
import { completion_date_after_edit, prepare_completion } from './logic/completion.ts';
import { evaluate_completion_date, parse_completion_rule } from './rule_evaluation.ts';
const EDITABLE = [
  'holder_kind',
  'holder_id',
  'project_id',
  'title',
  'why',
  'active',
  'completed',
  'deadline',
  'page',
  'completion_rule',
  'kind',
  'primary_checklist_block_id',
  'primary_chart',
];
/** Python dict equality compares numeric values, including signed zero, before structure. */
function rule_equal(left: unknown, right: unknown): boolean {
  const numeric = (value: unknown): number | bigint | null =>
    value instanceof JsonNumber
      ? (value.integer ?? value.value)
      : typeof value === 'number'
        ? value
        : null;
  const a = numeric(left),
    b = numeric(right);
  if (a !== null || b !== null) {
    if (a === null || b === null) return false;
    if (typeof a === typeof b) return a === b;
    const integer = typeof a === 'bigint' ? a : (b as bigint);
    const floating = typeof a === 'number' ? a : (b as number);
    return Number.isFinite(floating) && Number.isInteger(floating) && BigInt(floating) === integer;
  }
  if (left === right) return true;
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
  if (Array.isArray(left) !== Array.isArray(right)) return false;
  const l = left as Record<string, unknown>,
    r = right as Record<string, unknown>;
  const keys = Object.keys(l);
  return (
    keys.length === Object.keys(r).length &&
    keys.every((key) => key in r && rule_equal(l[key], r[key]))
  );
}
function accepted(
  sql: TransactionSql,
  current: Goal,
  body: Record<string, unknown>,
  now: number,
  creating = false,
): Goal {
  if (Object.keys(body).some((k) => !EDITABLE.includes(k))) throw new Error('unknown goal field');
  const changes = { ...body };
  delete changes.completed;
  if ('completed' in body && typeof body.completed !== 'boolean')
    throw new Error('active and completed must be booleans');
  if ('page' in changes) changes.page = prepare_document(changes.page, () => newId('block'));
  if (changes.primary_chart != null) {
    const raw = clone_numeric_json(changes.primary_chart);
    if (!raw || typeof raw !== 'object' || Array.isArray(raw) || raw instanceof JsonNumber)
      throw new Error('primary chart must be an object');
    const draft = raw as Record<string, unknown>;
    if (!('id' in draft)) draft.id = newId('chart');
    if (!('placement' in draft)) draft.placement = 'primary';
    const chart = draft as unknown as ChartBlock;
    validate_chart(chart, '$.primary_chart');
    if (chart.placement !== 'primary') throw new Error('primary chart must have primary placement');
    changes.primary_chart = chart;
  }
  if (changes.completion_rule != null)
    changes.completion_rule = parse_completion_rule(sql, changes.completion_rule);
  let result = { ...current, ...changes } as Goal;
  if (current.kind !== result.kind && result.kind === 'binary')
    result = { ...result, completion_rule: null };
  if (result.holder_kind !== 'chief' && result.holder_kind !== 'manager')
    throw new Error('holder_kind must be chief or manager');
  if (result.holder_kind === 'chief' && result.holder_id !== null)
    throw new Error('Chief holder_id must be null');
  if (
    result.holder_kind === 'manager' &&
    (typeof result.holder_id !== 'string' ||
      !result.holder_id ||
      !sql.get('SELECT 1 FROM managers WHERE id=?', [result.holder_id]))
  )
    throw new Error('goal holder Manager does not exist');
  for (const key of ['title', 'project_id'] as const)
    if (typeof result[key] !== 'string' || !result[key].trim())
      throw new Error(`${key} must be nonempty`);
  if (typeof result.why !== 'string') throw new Error('why must be text');
  if (creating && !('kind' in body))
    throw new Error(
      'kind is required: choose binary or quantitative and its main display; inspect panels goal schema create --json',
    );
  if (result.kind !== 'binary' && result.kind !== 'quantitative')
    throw new Error(
      `unknown goal kind: choose binary or quantitative; inspect panels goal schema ${creating ? 'create' : 'edit'} --json`,
    );
  if (typeof result.active !== 'boolean') throw new Error('active and completed must be booleans');
  result = prepare_completion(current, result, {
    creating,
    selection_explicit: 'primary_checklist_block_id' in body,
    chart_explicit: 'primary_chart' in body,
    completed:
      result.completion_rule !== null ? null : ((body.completed as boolean | undefined) ?? null),
  });
  if (result.kind === 'binary' && result.completion_rule !== null)
    throw new Error('completion_rule applies only to quantitative goals');
  if (
    result.kind === 'quantitative' &&
    result.completion_rule === null &&
    (creating || current.kind !== 'quantitative' || current.completion_rule !== null)
  )
    throw new Error(
      `completion_rule is required for quantitative goals; inspect panels goal schema ${creating ? 'create' : 'edit'} --json`,
    );
  if (result.deadline !== null)
    validate_iso_calendar_date(result.deadline, 'deadline must be an ISO calendar date');
  if (!sql.get('SELECT 1 FROM projects WHERE id=?', [result.project_id]))
    throw new Error('goal Project does not exist');
  if (result.primary_chart && blocks(result.page).some((b) => b.id === result.primary_chart!.id))
    throw new Error('primary chart ID must be unique');
  for (const id of measure_ids(result.primary_chart, result.page)) read_measure(sql, id);
  const graph_roots = [
      ...new Set([
        ...expectation_measure_ids(result.primary_chart, result.page),
        ...measure_ids(result.primary_chart, result.page),
      ]),
    ],
    definitions = read_definitions(sql, graph_roots),
    datasets = new Map(
      dataset_ids(result.primary_chart, result.page).map((id) => [id, read_dataset(sql, id)]),
    );
  for (const id of graph_roots) validate_graph(id, definitions);
  for (const chart of charts(result.primary_chart, result.page)) {
    if (chart.binding) validate_category_references(chart, datasets, definitions);
    if (chart.manual && read_measure(sql, chart.measure_id!).origin !== 'manual')
      throw new Error('manual controls require a manual-origin measure');
  }
  if (result.completion_rule !== null) {
    if (
      creating ||
      !rule_equal(result.completion_rule, current.completion_rule) ||
      'completed' in body
    )
      result = {
        ...result,
        completion_date: evaluate_completion_date(sql, result.completion_rule, now),
      };
    if ('completed' in body && body.completed !== (result.completion_date !== null))
      throw new Error("completion follows the goal's completion_rule");
  } else
    result = {
      ...result,
      completion_date: completion_date_after_edit(current, result, server_date(now), creating),
    };
  return result;
}
function values(goal: Goal): Record<string, SqlValue> {
  const { binary_checked, ...rest } = goal;
  return {
    ...rest,
    active: Number(goal.active),
    completed: Number(binary_checked),
    page: stringify_python_json(goal.page, { compact: true }),
    primary_chart: goal.primary_chart
      ? stringify_python_json(goal.primary_chart, { compact: true, ensure_ascii: true })
      : null,
    completion_rule: goal.completion_rule
      ? stringify_python_json(goal.completion_rule, { compact: true, ensure_ascii: true })
      : null,
  };
}
function prepare(
  sql: TransactionSql,
  current: Goal,
  body: Record<string, unknown>,
  now: number,
  creating: boolean,
): Goal {
  try {
    return accepted(sql, current, body, now, creating);
  } catch (e) {
    if (e instanceof PlannerError) throw e;
    if (e instanceof Error) throw new PlannerError('validation', e.message);
    throw e;
  }
}
export function create_goal(sql: TransactionSql, body: Record<string, unknown>, now: number): Goal {
  const defaults = read_chief_settings(sql).launch_defaults,
    title = body.title ?? '';
  const current: Goal = {
    id: newId('goal'),
    project_id: (body.project_id as string) ?? '',
    title: title as string,
    why: '',
    kind: 'binary',
    active: true,
    binary_checked: false,
    primary_checklist_block_id: null,
    deadline: null,
    primary_chart: null,
    page: { kind: 'headroom.page', version: 1, title: title as string, summary: '', blocks: [] },
    revision: 1,
    created_at: now,
    updated_at: now,
    ...defaults,
    completion_rule: null,
    completion_date: null,
    holder_kind: 'chief',
    holder_id: null,
  };
  const goal = prepare(sql, current, body, now, true),
    v = values(goal),
    fields = Object.keys(v);
  sql.run(
    `INSERT INTO goals(${fields.join(',')}) VALUES(${fields.map(() => '?').join(',')})`,
    Object.values(v),
  );
  return goal;
}
export function update_goal(
  sql: TransactionSql,
  id: string,
  body: Record<string, unknown>,
  now: number,
): Goal {
  const current = read_goal(sql, id),
    goal = {
      ...prepare(sql, current, body, now, false),
      revision: current.revision + 1,
      updated_at: now,
    },
    v = values(goal);
  delete v.id;
  sql.run(
    `UPDATE goals SET ${Object.keys(v)
      .map((k) => k + '=?')
      .join(',')} WHERE id=?`,
    [...Object.values(v), id],
  );
  return goal;
}
export function make_manager(
  sql: TransactionSql,
  id: string,
  clock: Clock,
): { manager_id: string; goal: Goal } {
  const current = read_goal(sql, id);
  if (current.holder_kind !== 'chief')
    throw new PlannerError('validation', 'only a Chief goal can become a Manager');
  const manager = create_manager(sql, {
    title: current.title,
    project_id: current.project_id,
    clock,
  });
  return {
    manager_id: manager.id,
    goal: update_goal(sql, id, { holder_kind: 'manager', holder_id: manager.id }, clock.nowUnix()),
  };
}
export function delete_goal(
  database: Database,
  db_path: string,
  id: string,
  principal: Principal,
  now: number,
): { goal_id: string; title: string } {
  let quarantine: QuarantinedFiles | null = null;
  let result: { goal_id: string; title: string };
  try {
    result = database.write((sql) => {
      const current = read_goal(sql, id);
      requireAbove(sql, principal, { kind: 'goal', id });
      for (const source of sql.all<{ id: string; responsibility_revision: number }>(
        'SELECT id,responsibility_revision FROM measure_sources WHERE responsible_goal_id=?',
        [id],
      ))
        set_responsible(
          sql,
          source.id,
          { kind: 'chief', id: 'chief' },
          source.responsibility_revision,
          now,
        );
      quarantine = quarantine_goal_files(db_path, id);
      for (const table of ['manager_wake_batches', 'manager_wakes', 'manager_wake_suppressions'])
        sql.run(`DELETE FROM ${table} WHERE target_kind='goal' AND target_id=?`, [id]);
      for (const table of [
        'notification_deliveries',
        'notification_attention_edges',
        'notification_attention_state',
      ])
        sql.run(`DELETE FROM ${table} WHERE subject_kind='goal' AND subject_id=?`, [id]);
      sql.run('DELETE FROM goals WHERE id=?', [id]);
      return { goal_id: current.id, title: current.title };
    });
  } catch (e) {
    restore_quarantined_files(quarantine);
    throw e;
  }
  purge_quarantined_files(quarantine);
  return result;
}

/** Manager readers and launch writer used by Ticket message delivery. */
import { parse_numeric_json } from '../core/http/numeric_json.ts';
import { newId } from '../core/ids.ts';
import { validate_launch_configuration } from './launch_settings.ts';
import { isDeepStrictEqual } from 'node:util';
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import type { Clock } from '../core/clock.ts';
import { PlannerError } from '../core/contracts.ts';
import { require_conversation_backend_key } from '../conversation/contracts.ts';
import type {
  Manager,
  ManagerLaunchConfiguration,
  ManagerRead,
  ManagerPageDocument,
} from './contracts.ts';
interface ManagerRow {
  id: string;
  title: string;
  goal: string;
  recap: string;
  project_id: string;
  project_name: string;
  manager_backend: string;
  manager_model: string;
  manager_reasoning_effort: string | null;
  settled: number;
  created_at: number;
  updated_at: number;
  launch_configuration_is_explicit: number;
  page: string | null;
  page_revision: number;
}
export function manager_agent_key(id: string): string {
  return `manager_${id}`;
}
export function read_manager(sql: ReadSql, id: string): ManagerRead {
  const row = sql.get<ManagerRow>(
    'SELECT managers.*, projects.name AS project_name FROM managers ' +
      'JOIN projects ON projects.id=managers.project_id WHERE managers.id=?',
    [id],
  );
  if (!row) throw new PlannerError('not_found', 'manager not found', { id });
  const item: Manager = {
    id: row.id,
    title: row.title,
    goal: row.goal,
    recap: row.recap,
    project_id: row.project_id,
    project_name: row.project_name,
    settled: Boolean(row.settled),
    created_at: row.created_at,
    updated_at: row.updated_at,
    page: row.page ? (parse_numeric_json(row.page) as ManagerPageDocument) : null,
    page_revision: row.page_revision,
    launch_configuration_is_explicit: Boolean(row.launch_configuration_is_explicit),
    manager_launch_configuration: {
      employee_backend: require_conversation_backend_key(row.manager_backend),
      employee_launch_model: row.manager_model,
      employee_launch_reasoning_effort: row.manager_reasoning_effort,
    },
    manager_agent_key: manager_agent_key(id),
  };
  return { item };
}
export function update_manager_launch_configuration(
  tx: TransactionSql,
  id: string,
  configuration: ManagerLaunchConfiguration,
  options: {
    principal: Principal;
    clock: Clock;
    expected_configuration?: ManagerLaunchConfiguration | null;
    explicit_choice?: boolean;
  },
): Manager {
  const current = read_manager(tx, id).item;
  if (
    options.expected_configuration != null &&
    !isDeepStrictEqual(current.manager_launch_configuration, options.expected_configuration)
  ) {
    return current;
  }
  tx.run(
    'UPDATE managers SET manager_backend=?, manager_model=?, manager_reasoning_effort=?, ' +
      'updated_at=?, launch_configuration_is_explicit=CASE WHEN ? THEN 1 ' +
      'ELSE launch_configuration_is_explicit END WHERE id=?',
    [
      configuration.employee_backend,
      configuration.employee_launch_model,
      configuration.employee_launch_reasoning_effort,
      options.clock.nowUnix(),
      Number(options.explicit_choice ?? false),
      id,
    ],
  );
  return read_manager(tx, id).item;
}

export function read_launch_defaults(sql: ReadSql): ManagerLaunchConfiguration {
  const row = sql.get<ManagerLaunchConfiguration>(
    'SELECT employee_backend, employee_launch_model, employee_launch_reasoning_effort ' +
      'FROM manager_launch_defaults WHERE singleton=1',
  );
  if (!row) throw new PlannerError('validation', 'Manager launch defaults are absent');
  return { ...row, employee_backend: require_conversation_backend_key(row.employee_backend) };
}
export function update_launch_defaults(
  tx: TransactionSql,
  configuration: ManagerLaunchConfiguration,
  principal: Principal,
): ManagerLaunchConfiguration {
  if (principal.kind !== 'owner' && principal.kind !== 'chief') {
    throw new PlannerError(
      'agent_forbidden',
      'Only the owner or Chief can change Manager defaults',
    );
  }
  validate_launch_configuration(tx, configuration);
  tx.run(
    'UPDATE manager_launch_defaults SET employee_backend=?, employee_launch_model=?, employee_launch_reasoning_effort=? WHERE singleton=1',
    [
      configuration.employee_backend,
      configuration.employee_launch_model,
      configuration.employee_launch_reasoning_effort,
    ],
  );
  return read_launch_defaults(tx);
}
export function create_manager(
  tx: TransactionSql,
  options: { title: string; project_id: string; goal?: string; clock: Clock },
): Manager {
  if (!options.title) throw new PlannerError('validation', 'item title is required', {});
  const id = newId('si');
  const now = options.clock.nowUnix();
  if (!tx.get('SELECT 1 FROM projects WHERE id=?', [options.project_id])) {
    throw new PlannerError('validation', 'invalid project_id', { project_id: options.project_id });
  }
  tx.run('INSERT INTO agents(agent_key, conversation_id) VALUES (?, NULL)', [
    manager_agent_key(id),
  ]);
  const defaults = read_launch_defaults(tx);
  tx.run(
    'INSERT INTO managers (id, title, goal, project_id, manager_backend, manager_model, manager_reasoning_effort, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
    [
      id,
      options.title,
      options.goal ?? '',
      options.project_id,
      defaults.employee_backend,
      defaults.employee_launch_model,
      defaults.employee_launch_reasoning_effort,
      now,
      now,
    ],
  );
  return read_manager(tx, id).item;
}
export function update_manager(
  tx: TransactionSql,
  id: string,
  options: { edits: Record<string, string | null>; clock: Clock },
): Manager {
  const allowed = ['title', 'goal', 'recap', 'project_id'];
  const invalid = Object.keys(options.edits)
    .filter((field) => !allowed.includes(field))
    .sort();
  if (invalid.length)
    throw new PlannerError('validation', 'field is not an editable item field', {
      field: invalid[0],
    });
  read_manager(tx, id);
  if (
    'project_id' in options.edits &&
    (options.edits.project_id === null ||
      !tx.get('SELECT 1 FROM projects WHERE id=?', [options.edits.project_id!]))
  ) {
    throw new PlannerError('validation', 'invalid project_id', {
      project_id: options.edits.project_id,
    });
  }
  const fields = Object.keys(options.edits);
  const now = options.clock.nowUnix();
  if (fields.length) {
    tx.run(
      `UPDATE managers SET ${fields.map((field) => `${field}=?`).join(', ')}, updated_at=? WHERE id=?`,
      [...fields.map((field) => options.edits[field]!), now, id],
    );
    if ('project_id' in options.edits)
      tx.run('UPDATE tickets SET updated_at=? WHERE manager_id=?', [now, id]);
  }
  return read_manager(tx, id).item;
}
export function require_manager_can_delete(sql: ReadSql, id: string): Manager {
  const item = read_manager(sql, id).item;
  if (
    sql.get(
      "SELECT 1 FROM measure_sources WHERE responsible_kind='manager' AND responsible_manager_id=? LIMIT 1",
      [id],
    )
  )
    throw new PlannerError(
      'validation',
      'Manager is responsible for measure sources; reassign them first',
    );
  if (sql.get("SELECT 1 FROM goals WHERE holder_kind='manager' AND holder_id=? LIMIT 1", [id]))
    throw new PlannerError('validation', 'Manager holds goals; reassign them first');
  const tickets = sql.all<{ id: string }>('SELECT id FROM tickets WHERE manager_id=? ORDER BY id', [
    id,
  ]);
  if (tickets.length)
    throw new PlannerError('validation', 'manager has child tickets', {
      manager_id: id,
      ticket_ids: tickets.map((row) => row.id),
    });
  return item;
}
export function delete_manager_rows(
  tx: TransactionSql,
  id: string,
): { manager_id: string; title: string } {
  const item = require_manager_can_delete(tx, id);
  const held = tx.get<{ id: string }>(
    "SELECT id FROM tickets WHERE json_extract(ceiling_holder, '$.kind')='manager' AND json_extract(ceiling_holder, '$.id')=? ORDER BY id LIMIT 1",
    [id],
  );
  if (held)
    throw new PlannerError(
      'validation',
      'Manager cannot be deleted while it holds a Ticket ceiling',
      { manager_id: id, held_ticket_id: held.id },
    );
  tx.run('DELETE FROM managers WHERE id=?', [id]);
  tx.run('DELETE FROM agents WHERE agent_key=?', [item.manager_agent_key]);
  return { manager_id: id, title: item.title };
}
export function set_manager_settled(
  tx: TransactionSql,
  id: string,
  options: { settled: boolean; principal: Principal; clock: Clock },
): Manager {
  if (options.principal.kind !== 'owner')
    throw new PlannerError('agent_forbidden', 'only the owner can settle a Manager');
  if (id === 'chief') throw new PlannerError('validation', 'Chief can never be settled');
  read_manager(tx, id);
  tx.run('UPDATE managers SET settled=?, updated_at=? WHERE id=?', [
    Number(options.settled),
    options.clock.nowUnix(),
    id,
  ]);
  return read_manager(tx, id).item;
}

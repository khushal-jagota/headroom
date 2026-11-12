/** Manager readers and launch writer used by Ticket message delivery. */
import { isDeepStrictEqual } from 'node:util';
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import type { Clock } from '../core/clock.ts';
import { PlannerError } from '../core/contracts.ts';
import { require_conversation_backend_key } from '../conversation/contracts.ts';
import type { Manager, ManagerLaunchConfiguration, ManagerRead } from './contracts.ts';
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
    page: row.page ? JSON.parse(row.page) : null,
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

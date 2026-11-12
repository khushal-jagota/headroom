import { PlannerError } from '../core/contracts.ts';
import type { ReadSql, SqlValue } from '../core/database/contracts.ts';
import { parse_numeric_json } from '../core/http/numeric_json.ts';
import type { Goal } from './contracts.ts';
export function hydrate_goal(row: Record<string, unknown>): Goal {
  const value: Record<string, unknown> = {
    ...row,
    active: Boolean(row.active),
    binary_checked: Boolean(row.completed),
    primary_chart: row.primary_chart ? parse_numeric_json(String(row.primary_chart)) : null,
    page: parse_numeric_json(String(row.page)),
    completion_rule: row.completion_rule ? parse_numeric_json(String(row.completion_rule)) : null,
    completion_date: row.completion_date ?? null,
  };
  delete value.completed;
  return value as unknown as Goal;
}
export function read_goal(sql: ReadSql, id: string): Goal {
  const row = sql.get<Record<string, unknown>>('SELECT * FROM goals WHERE id=?', [id]);
  if (!row) throw new PlannerError('not_found', 'goal not found');
  return hydrate_goal(row);
}
export function list_goals(
  sql: ReadSql,
  options: { active?: boolean | null; project_id?: string | null; holder_id?: string | null } = {},
): Goal[] {
  const { active = null, project_id = null, holder_id = null } = options;
  const args: SqlValue[] = [
    active === null ? null : Number(active),
    active === null ? null : Number(active),
    project_id,
    project_id,
  ];
  let query = 'SELECT * FROM goals WHERE (? IS NULL OR active=?) AND (? IS NULL OR project_id=?)';
  if (holder_id !== null) {
    query += " AND holder_kind='manager' AND holder_id=?";
    args.push(holder_id);
  }
  return sql
    .all<Record<string, unknown>>(query + ' ORDER BY created_at,id', args)
    .map(hydrate_goal);
}

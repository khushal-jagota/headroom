/** Canonical holder page storage. Rich dependencies enter through the goal-unit seam. */
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { parse_numeric_json, stringify_python_json } from '../core/http/numeric_json.ts';
import { newId } from '../core/ids.ts';
import type { ManagerPageDocument as PageDocument } from './contracts.ts';
import { manager_document_dependencies } from './document_dependencies.ts';
import { prepare_plain_document, has_chart } from './logic/documents.ts';
export function read_page(
  sql: ReadSql,
  manager_id: string | null,
): { page: PageDocument | null; page_revision: number } {
  const row =
    manager_id === null
      ? sql.get<{ page: string | null; page_revision: number }>(
          'SELECT page,page_revision FROM chief_page WHERE singleton=1',
        )
      : sql.get<{ page: string | null; page_revision: number }>(
          'SELECT page,page_revision FROM managers WHERE id=?',
          [manager_id],
        );
  if (!row) throw new PlannerError('not_found', 'holder not found');
  return {
    page: row.page ? (parse_numeric_json(row.page) as PageDocument) : null,
    page_revision: row.page_revision,
  };
}
export function write_page(
  tx: TransactionSql,
  manager_id: string | null,
  body: Record<string, unknown>,
) {
  let page: PageDocument | null = null;
  if (body.page != null) {
    try {
      page = has_chart(body.page)
        ? manager_document_dependencies().prepare_rich_document(body.page)
        : prepare_plain_document(body.page, () => newId('block'));
    } catch (error) {
      if (error instanceof TypeError) throw new PlannerError('validation', error.message);
      throw error;
    }
    if (has_chart(page)) manager_document_dependencies().validate_page_bindings(tx, page);
  }
  const saved = page === null ? null : python_json(page);
  if (manager_id === null)
    tx.run('UPDATE chief_page SET page=?,page_revision=page_revision+1 WHERE singleton=1', [saved]);
  else
    tx.run('UPDATE managers SET page=?,page_revision=page_revision+1 WHERE id=?', [
      saved,
      manager_id,
    ]);
  return read_page(tx, manager_id);
}
/** Shared numeric-kind encoder also supplies Manager page character counts. */
export const python_json = stringify_python_json;
export function page_readings(
  sql: ReadSql,
  manager_id: string | null,
  now: Date,
  calendar_iso?: string,
): Record<string, unknown> {
  const saved = read_page(sql, manager_id);
  const page = saved.page ?? {
    kind: 'headroom.page',
    version: 1,
    title: '',
    summary: '',
    blocks: [],
  };
  if (has_chart(page))
    return manager_document_dependencies().document_readings(
      sql,
      { id: manager_id ?? 'chief', page, revision: saved.page_revision },
      now,
    );
  let today: string;
  let midnight: Date;
  if (calendar_iso !== undefined) {
    // TestClock retains its explicit fixed offset, as Python datetime.fromisoformat does.
    today = calendar_iso.slice(0, 10);
    const offset = /([+-]\d{2}:\d{2}|Z)$/.exec(calendar_iso)?.[1] ?? '';
    const tomorrow = new Date(today + 'T00:00:00Z');
    tomorrow.setUTCDate(tomorrow.getUTCDate() + 1);
    midnight = new Date(tomorrow.toISOString().slice(0, 10) + 'T00:00:00' + offset);
  } else {
    // The real server calendar resolves tomorrow's offset, including DST transitions.
    today = `${String(now.getFullYear()).padStart(4, '0')}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    midnight = new Date(now.getTime());
    midnight.setDate(midnight.getDate() + 1);
    midnight.setHours(0, 0, 0, 0);
  }
  return {
    goal_id: manager_id ?? 'chief',
    revision: saved.page_revision,
    status: 'neutral',
    status_date: today,
    next_refresh_at: Math.floor(midnight.getTime() / 1000),
    values: [],
    sources: [],
    charts: {},
  };
}

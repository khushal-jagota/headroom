import { registerReadFunction } from '../../core/database/index.ts';
import type { ReadSql } from '../../core/database/contracts.ts';
import { PlannerError } from '../../core/contracts.ts';
import { workflow_from_row } from '../../worker_types/store.ts';
import type { CatalogEntry } from '../contracts.ts';
import { canonical_origin } from './links.ts';
export function linked(
  sql: ReadSql,
  ticket: string,
  origin: string,
  seen: Set<string>,
  offset: number,
  limit: number,
): { total: number; entries: CatalogEntry[] } {
  const ticketRow = sql.get<{ workflow_json: string }>(
    'SELECT workflow_json FROM tickets WHERE id=?',
    [ticket],
  );
  if (!ticketRow) throw new PlannerError('not_found', 'ticket not found', {});
  const fields = [
    '@proposal',
    ...workflow_from_row(ticketRow)
      .fields.map((f) => f.id)
      .reverse(),
  ];
  registerReadFunction(sql, 'artifact_field_rank', (field) =>
    fields.includes(String(field)) ? fields.indexOf(String(field)) : fields.length,
  );
  registerReadFunction(sql, 'artifact_visible', (target) => Number(!seen.has(String(target))));
  registerReadFunction(sql, 'artifact_known_field', (field) =>
    Number(fields.includes(String(field))),
  );
  registerReadFunction(sql, 'artifact_stem_key', (stem) => String(stem).toLowerCase());
  registerReadFunction(sql, 'artifact_same_origin', (value) =>
    Number(
      canonical_origin(String(value), new URL(origin).protocol.slice(0, -1)) ===
        canonical_origin(origin),
    ),
  );
  const cte = `WITH ranked AS (
    SELECT *,row_number() OVER(
      PARTITION BY target ORDER BY artifact_field_rank(field_id),ordinal) duplicate
    FROM ticket_artifact_links WHERE ticket_id=? AND artifact_known_field(field_id)
    AND (origin IS NULL OR artifact_same_origin(origin))
  ), unique_links AS (
    SELECT *,count(*) OVER(PARTITION BY artifact_stem_key(stem)) collisions
    FROM ranked WHERE duplicate=1
  ), visible AS (SELECT * FROM unique_links WHERE artifact_visible(target)) `;
  const total = sql.get<{ count: number }>(cte + 'SELECT count(*) AS count FROM visible', [
    ticket,
  ])!.count;
  const rows = sql.all<{
    target: string;
    parent: string;
    stem: string;
    kind: string;
    collisions: number;
  }>(
    cte +
      `SELECT * FROM visible
    ORDER BY artifact_field_rank(field_id),ordinal LIMIT ? OFFSET ?`,
    [ticket, limit, offset],
  );
  return {
    total,
    entries: rows.map((row) => ({
      key: 'linked:' + row.target,
      path: decodeURIComponent(row.target).split('/').slice(4).join('/'),
      label: row.collisions > 1 && row.parent ? row.parent + ' / ' + row.stem : row.stem,
      kind: row.kind,
      href: row.target,
      directory: null,
      child_count: 0,
    })),
  };
}

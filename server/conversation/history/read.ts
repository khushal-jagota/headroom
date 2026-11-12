/** Bounded prior-conversation metadata in one read-only database snapshot. */
import type { ReadSql } from '../../core/database/contracts.ts';
import { PlannerError } from '../../core/contracts.ts';
import { casefold } from '../../core/casefold.ts';
import { stringify_python_json } from '../../core/http/numeric_json.ts';
import { PAGE_MAX_BYTES, PAGE_SIZE } from '../../files/catalog/config.ts';
import type {
  ConversationHistoryEntry,
  ConversationHistoryPage,
  HistoryPageInput,
} from './contracts.ts';
import { history_formatter } from './format.ts';
interface HistoryRow {
  conversation_id: string;
  created_at: number;
}
export function read_history_page(sql: ReadSql, input: HistoryPageInput): ConversationHistoryPage {
  const { kind, owner_id, limit, selected, prefix, after, locale, timezone } = input;
  let { offset } = input;
  if (
    offset < 0 ||
    limit < 1 ||
    limit > PAGE_SIZE ||
    Array.from(locale).length > 80 ||
    Array.from(timezone).length > 100 ||
    (prefix && Array.from(prefix).length > 200)
  )
    throw new PlannerError('validation', 'invalid conversation history page');
  if (kind === 'ticket' && sql.get('SELECT 1 FROM tickets WHERE id=?', [owner_id]) === undefined)
    throw new PlannerError('not_found', 'ticket not found');
  const current =
    kind === 'ticket'
      ? sql.get<{ conversation_id: string | null }>(
          'SELECT conversation_id FROM tickets WHERE id=?',
          [owner_id],
        )
      : sql.get<{ conversation_id: string | null }>(
          'SELECT conversation_id FROM agents WHERE agent_key=?',
          ['manager_' + owner_id],
        );
  const current_id = current?.conversation_id ?? null;
  const args = [kind, owner_id, current_id] as const;
  const base =
    ' FROM conversation_history_catalog WHERE owner_kind=? AND owner_id=? AND conversation_id IS NOT ?';
  const order = ' ORDER BY created_at,conversation_id';
  const total = sql.get<{ count: number }>('SELECT count(*) AS count' + base, args)!.count;
  const format = history_formatter(locale, timezone);
  try {
    const entry = (row: HistoryRow): ConversationHistoryEntry => ({
      conversation_id: row.conversation_id,
      created_at: row.created_at,
      label: format.format(row.created_at) + ' · ' + row.conversation_id,
    });
    let active: number | null = null;
    if (selected) {
      const row = sql.get<HistoryRow>(
        'SELECT created_at,conversation_id' + base + ' AND conversation_id=?',
        [...args, selected],
      );
      if (row !== undefined)
        active = sql.get<{ count: number }>(
          'SELECT count(*) AS count' + base + ' AND (created_at,conversation_id)<(?,?)',
          [...args, row.created_at, row.conversation_id],
        )!.count;
    }
    if (prefix) {
      active = null;
      const wanted = casefold(prefix);
      const start = Math.min(Math.max(after + 1, 0), total);
      seek: for (const [first, end] of [
        [start, total],
        [0, start],
      ]) {
        let continuation: HistoryRow | null = null;
        for (let begin = first; begin < end;) {
          const batch_size = Math.min(PAGE_SIZE, end - begin);
          // Pay the initial rank offset once per range. Later batches seek from
          // the last indexed tuple instead of revisiting every earlier row.
          const rows: HistoryRow[] =
            continuation === null
              ? sql.all<HistoryRow>(
                  'SELECT conversation_id,created_at' + base + order + ' LIMIT ? OFFSET ?',
                  [...args, batch_size, begin],
                )
              : sql.all<HistoryRow>(
                  'SELECT conversation_id,created_at' +
                    base +
                    ' AND (created_at,conversation_id)>(?,?)' +
                    order +
                    ' LIMIT ?',
                  [...args, continuation.created_at, continuation.conversation_id, batch_size],
                );
          if (rows.length === 0) break;
          for (let index = 0; index < rows.length; index++) {
            if (casefold(entry(rows[index]).label).startsWith(wanted)) {
              active = begin + index;
              break seek;
            }
          }
          continuation = rows[rows.length - 1];
          begin += rows.length;
        }
      }
    }
    if (active !== null) offset = Math.floor(active / limit) * limit;
    const rows = sql.all<HistoryRow>(
      'SELECT conversation_id,created_at' + base + order + ' LIMIT ? OFFSET ?',
      [...args, limit, offset],
    );
    const entries: ConversationHistoryEntry[] = [];
    let bytes_used = 0;
    for (const row of rows) {
      const value = entry(row);
      const bytes = Buffer.byteLength(stringify_python_json(value), 'utf8');
      if (bytes_used + bytes > PAGE_MAX_BYTES) {
        if (entries.length === 0)
          throw new PlannerError('validation', 'conversation metadata exceeds page budget');
        break;
      }
      entries.push(value);
      bytes_used += bytes;
    }
    const revision =
      sql.get<{ revision: number }>(
        'SELECT revision FROM resource_source_versions WHERE source_kind=? AND source_id=?',
        ['conversation-history', kind + ':' + owner_id],
      )?.revision ?? 0;
    return {
      revision: String(revision) + ':' + (current_id === null ? 'None' : current_id),
      offset,
      total,
      active_index: active,
      entries,
    };
  } finally {
    format.close();
  }
}

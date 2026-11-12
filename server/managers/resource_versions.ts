/** Manager documents and the Chief page share the selected-resource protocol. */
import type { FastifyRequest } from 'fastify';
import type { Clock } from '../core/clock.ts';
import type { ReadSql } from '../core/database/contracts.ts';
import type { ResourceProvider, ResourceSnapshot } from '../resource_versions/contracts.ts';
import { manager_workspace_document } from './views.ts';
import { read_page } from './pages.ts';
let clock: Clock;
export function configure_manager_resources(value: Clock): void {
  clock = value;
}
function revision(sql: ReadSql, kind: string, id: string): string {
  return String(
    sql.get<{ revision: number }>(
      'SELECT revision FROM resource_source_versions WHERE source_kind=? AND source_id=?',
      [kind, id],
    )?.revision ?? 0,
  );
}
export class ManagerDocumentResources implements ResourceProvider {
  capture(sql: ReadSql, _request: FastifyRequest, key: string): ResourceSnapshot | null {
    if (key === '/api/chief/page') {
      const row = sql.get<{ page_revision: number }>(
        'SELECT page_revision FROM chief_page WHERE singleton=1',
      );
      if (!row) throw new Error('Chief page singleton is missing');
      return { revision: String(row.page_revision), read: () => read_page(sql, null) };
    }
    const match = /^\/api\/managers\/([^/?]+)\/workspace-content$/.exec(key);
    if (!match) return null;
    const id = decodeURIComponent(match[1]);
    const now = clock.now();
    const date = clock.isoformat().slice(0, 10);
    return {
      revision: [revision(sql, 'workspace-manager', id), revision(sql, 'goals', 'all'), date].join(
        ':',
      ),
      read: () => manager_workspace_document(sql, id, now),
    };
  }
}

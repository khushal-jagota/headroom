import { randomUUID } from 'node:crypto';
import type { TransactionSql, ReadSql } from '../../core/database/contracts.ts';
import type { NavigationKey, NavigationSource, NavigationRegistry, NavigationRecord } from '../contracts.ts';
import { compact, ORDERING_GENERATION } from '../logic/json.ts';
const key = ([kind, id]: NavigationKey) => compact([kind, id]);
function replace(tx: TransactionSql, source: NavigationSource, id: string): { upserts: NavigationRecord[]; deletes: { kind: string; id: string }[] } {
  const previous = tx.all<{ kind: string; id: string; value: string }>('SELECT o.kind,o.id,r.value FROM navigation_root_records o LEFT JOIN navigation_records r ON r.kind=o.kind AND r.id=o.id WHERE o.root_kind=? AND o.root_id=? ORDER BY o.kind,o.id', [source.kind, id]);
  const derived = source.derive(tx, id);
  const current = new Set(derived.records.map(record => key([record.kind, record.id])));
  const deletes = previous.filter(record => !current.has(key([record.kind, record.id]))).map(({ kind, id }) => ({ kind, id }));
  for (const record of deletes) tx.run('DELETE FROM navigation_records WHERE kind=? AND id=?', [record.kind, record.id]);
  const upserts: NavigationRecord[] = [];
  for (const record of derived.records) {
    const body = compact(record.value);
    if (previous.find(old => old.kind === record.kind && old.id === record.id)?.value !== body) {
      tx.run('INSERT INTO navigation_records(kind,id,value) VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET value=excluded.value', [record.kind, record.id, body]);
      upserts.push(record);
    }
  }
  tx.run('DELETE FROM navigation_root_records WHERE root_kind=? AND root_id=?', [source.kind, id]);
  for (const record of derived.records) tx.run('INSERT INTO navigation_root_records(root_kind,root_id,kind,id) VALUES(?,?,?,?)', [source.kind, id, record.kind, record.id]);
  tx.run('DELETE FROM navigation_dependencies WHERE kind=? AND id=?', [source.kind, id]);
  for (const [kind, identity] of derived.dependencies) tx.run('INSERT OR IGNORE INTO navigation_dependencies(source_kind,source_id,kind,id) VALUES(?,?,?,?)', [kind, identity, source.kind, id]);
  return { upserts, deletes };
}
export function coverage(sql: ReadSql) {
  const row = sql.get<{ scope: string; schema_version: number; epoch: string; revision: number }>('SELECT scope,schema_version,epoch,revision FROM navigation_meta WHERE singleton=1');
  if (!row) throw new Error('navigation metadata is absent');
  return { ...row, semantics: 'attention-knowledge-v10-ticket-workflows' as const, revision: String(row.revision) };
}
function metadata(tx: TransactionSql): void {
  const value = coverage(tx);
  tx.run('INSERT INTO navigation_view_meta(singleton,projection_version,epoch,revision,ordering_generation) VALUES(1,1,?,?,?) ON CONFLICT(singleton) DO UPDATE SET projection_version=1,epoch=excluded.epoch,revision=excluded.revision,ordering_generation=excluded.ordering_generation', [value.epoch, Number(value.revision), ORDERING_GENERATION]);
}
export function createNavigationRegistry(sources: readonly NavigationSource[]): NavigationRegistry {
  if (new Set(sources.map(source => source.kind)).size !== sources.length) throw new Error('duplicate navigation source');
  const registered = new Map(sources.map(source => [source.kind, source]));
  return {
    sources,
    finalize(tx) {
      const dirty = tx.all<{ kind: string; id: string }>('SELECT kind,id FROM navigation_dirty ORDER BY kind,id');
      if (!dirty.length) return;
      const roots = new Map<string, NavigationKey>();
      for (const changed of dirty) {
        for (const root of tx.all<{ kind: string; id: string }>('SELECT kind,id FROM navigation_dependencies WHERE source_kind=? AND source_id=? ORDER BY kind,id', [changed.kind, changed.id])) roots.set(key([root.kind, root.id]), [root.kind, root.id]);
        for (const source of sources) for (const id of source.affected(tx, [changed.kind, changed.id])) roots.set(key([source.kind, id]), [source.kind, id]);
      }
      const upserts: NavigationRecord[] = [], deletes: { kind: string; id: string }[] = [];
      for (const [, [kind, id]] of [...roots].sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)) {
        const source = registered.get(kind);
        if (!source) throw new Error(`navigation source ${kind} is not registered`);
        const changed = replace(tx, source, id);
        upserts.push(...changed.upserts); deletes.push(...changed.deletes);
      }
      const previous = coverage(tx), revision = Number(previous.revision) + 1;
      tx.run('INSERT INTO navigation_journal(revision,body) VALUES(?,?)', [revision, compact({ ...previous, base_revision: previous.revision, revision: String(revision), upserts, deletes })]);
      tx.run('UPDATE navigation_meta SET revision=? WHERE singleton=1', [revision]);
      tx.run('DELETE FROM navigation_dirty');
      metadata(tx);
    },
    rebuild(tx) {
      for (const table of ['navigation_records', 'navigation_dependencies', 'navigation_root_records', 'navigation_dirty', 'navigation_journal', 'navigation_view_index', 'navigation_view_memberships', 'navigation_view_counts', 'navigation_view_current_conversations', 'navigation_view_unread_conversations', 'navigation_view_globals', 'navigation_view_changes']) tx.run(`DELETE FROM ${table}`);
      tx.run('UPDATE navigation_meta SET schema_version=10,epoch=?,revision=0 WHERE singleton=1', [randomUUID()]);
      for (const source of sources) for (const id of source.roots(tx)) replace(tx, source, id);
      for (const name of ['running_conversations', 'out_claims', 'review', 'unread', 'settled']) tx.run('INSERT INTO navigation_view_globals(name,value) VALUES(?,0)', [name]);
      metadata(tx);
    },
  };
}

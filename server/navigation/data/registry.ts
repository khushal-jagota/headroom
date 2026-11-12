/** Transaction-owned finalization. Existing SQLite triggers supply every dirty source. */
import { randomUUID } from 'node:crypto';
import type { TransactionSql, ReadSql } from '../../core/database/contracts.ts';
import type {
  NavigationKey,
  NavigationRegistry,
  NavigationRecord,
  RecordKey,
} from '../contracts.ts';
import { compact } from '../logic/json.ts';
import { derive, newRoots, ticketOwners } from './derivation.ts';
import type { DerivedRoot } from './derivation.ts';
import { newDerivationContext } from './derivation_helpers.ts';
import type { DerivationContext } from './derivation_helpers.ts';
import { updateProjection, rebuildProjection } from '../views/projection.ts';
const key = ([kind, id]: NavigationKey) => compact([kind, id]);
const compareKeys = (a: NavigationKey, b: NavigationKey): number =>
  a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0;
function replace(
  tx: TransactionSql,
  root: NavigationKey,
  context: DerivationContext,
  prepared?: DerivedRoot,
): { upserts: NavigationRecord[]; deletes: RecordKey[] } {
  const previous = tx.all<{ kind: RecordKey['kind']; id: string; value: string | null }>(
    'SELECT o.kind,o.id,r.value FROM navigation_root_records o LEFT JOIN navigation_records r ON r.kind=o.kind AND r.id=o.id WHERE o.root_kind=? AND o.root_id=? ORDER BY o.kind,o.id',
    root,
  );
  const previousDependencies = tx.all<{ source_kind: string; source_id: string }>(
    'SELECT source_kind,source_id FROM navigation_dependencies WHERE kind=? AND id=? ORDER BY source_kind,source_id',
    root,
  );
  const derived = prepared ?? derive(tx, root, context);
  const oldRecords = new Map(previous.map((record) => [key([record.kind, record.id]), record]));
  const current = new Map(derived.records.map((record) => [key([record.kind, record.id]), record]));
  const oldDependencies = new Map(
    previousDependencies.map((dep) => [
      key([dep.source_kind, dep.source_id]),
      [dep.source_kind, dep.source_id] as NavigationKey,
    ]),
  );
  const dependencies = new Map(derived.dependencies.map((dep) => [key(dep), dep]));
  const deletes: RecordKey[] = [];
  for (const old of previous)
    if (!current.has(key([old.kind, old.id]))) {
      tx.run('DELETE FROM navigation_records WHERE kind=? AND id=?', [old.kind, old.id]);
      tx.run(
        'DELETE FROM navigation_root_records WHERE root_kind=? AND root_id=? AND kind=? AND id=?',
        [...root, old.kind, old.id],
      );
      deletes.push({ kind: old.kind, id: old.id });
    }
  const upserts: NavigationRecord[] = [];
  for (const record of derived.records) {
    const body = compact(record.value),
      identity = key([record.kind, record.id]);
    if (oldRecords.get(identity)?.value !== body) {
      tx.run(
        'INSERT INTO navigation_records(kind,id,value) VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET value=excluded.value',
        [record.kind, record.id, body],
      );
      upserts.push(record);
    }
    if (!oldRecords.has(identity))
      tx.run('INSERT INTO navigation_root_records(root_kind,root_id,kind,id) VALUES(?,?,?,?)', [
        ...root,
        record.kind,
        record.id,
      ]);
  }
  for (const [identity, dep] of oldDependencies)
    if (!dependencies.has(identity))
      tx.run(
        'DELETE FROM navigation_dependencies WHERE source_kind=? AND source_id=? AND kind=? AND id=?',
        [...dep, ...root],
      );
  for (const [identity, dep] of dependencies)
    if (!oldDependencies.has(identity))
      tx.run('INSERT INTO navigation_dependencies(source_kind,source_id,kind,id) VALUES(?,?,?,?)', [
        ...dep,
        ...root,
      ]);
  return { upserts, deletes };
}
function rawCoverage(sql: ReadSql) {
  const row = sql.get<{ scope: string; schema_version: number; epoch: string; revision: number }>(
    'SELECT scope,schema_version,epoch,revision FROM navigation_meta WHERE singleton=1',
  );
  if (!row) throw new Error('navigation metadata is absent');
  return {
    ...row,
    semantics:
      row.schema_version === 10
        ? ('attention-knowledge-v10-ticket-workflows' as const)
        : row.schema_version === 4
          ? 'attention-knowledge-v4'
          : `generic-source-facts-v${row.schema_version}`,
    revision: String(row.revision),
  };
}
export function coverage(sql: ReadSql) {
  const value = rawCoverage(sql);
  if (value.schema_version !== 10)
    throw new Error('navigation public reads require the current schema');
  return { ...value, semantics: 'attention-knowledge-v10-ticket-workflows' as const };
}
function prioritySourceShape(sql: ReadSql): boolean {
  const shapes = ['projects', 'managers', 'tickets'].map((table) => {
    const columns = sql.all<{ name: string }>('SELECT name FROM pragma_table_info(?)', [table]);
    if (!columns.length) throw new Error(`navigation source table is missing: ${table}`);
    return columns.some((column) => column.name === 'priority');
  });
  if (shapes.some(Boolean) !== shapes.every(Boolean))
    throw new Error('navigation source has inconsistent priority columns');
  return shapes.every(Boolean);
}
export function createNavigationRegistry(): NavigationRegistry {
  return {
    finalize(tx) {
      if (!tx.get("SELECT 1 FROM sqlite_master WHERE name='navigation_meta'")) return;
      const dirty = tx.all<{ kind: string; id: string }>(
        'SELECT kind,id FROM navigation_dirty ORDER BY kind,id',
      );
      if (!dirty.length) return;
      const roots = new Map<string, NavigationKey>();
      const add = (root: NavigationKey) => roots.set(key(root), root);
      for (const changed of dirty) {
        for (const root of tx.all<{ kind: string; id: string }>(
          'SELECT kind,id FROM navigation_dependencies WHERE source_kind=? AND source_id=? ORDER BY kind,id',
          [changed.kind, changed.id],
        ))
          add([root.kind, root.id]);
        for (const root of newRoots(tx, [changed.kind, changed.id])) add(root);
      }
      const context = newDerivationContext(),
        prepared = new Map<string, DerivedRoot>();
      // Capture old and new address parents before replacement, so reparenting fixes
      // both counts even when the old canonical association already disappeared.
      const addAddressTotals = (value: Record<string, unknown>) => {
        const parent = value.parent_kind
          ? [value.parent_kind, value.parent_id]
          : [value.owner_kind, value.owner_id];
        if (parent[0] === 'chief' || parent[0] === 'manager')
          add(['totals', parent.map(String).join(':')]);
      };
      for (const root of [...roots.values()])
        if (root[0] === 'conversation') {
          for (const row of tx.all<{ value: string }>(
            "SELECT value FROM navigation_records WHERE kind='address' AND json_extract(value,'$.conversation_id')=? ORDER BY id",
            [root[1]],
          ))
            addAddressTotals(JSON.parse(row.value));
          const derived = derive(tx, root, context);
          prepared.set(key(root), derived);
          for (const record of derived.records)
            if (record.kind === 'address')
              addAddressTotals(record.value as unknown as Record<string, unknown>);
          add(['totals', 'workspace']);
        }
      const upserts: NavigationRecord[] = [],
        deletes: RecordKey[] = [];
      for (const root of [...roots.values()].sort(
        (a, b) => Number(a[0] === 'totals') - Number(b[0] === 'totals') || compareKeys(a, b),
      )) {
        const changed = replace(tx, root, context, prepared.get(key(root)));
        upserts.push(...changed.upserts);
        deletes.push(...changed.deletes);
      }
      const previous = rawCoverage(tx),
        revision = Number(previous.revision) + 1;
      tx.run('INSERT INTO navigation_journal(revision,body) VALUES(?,?)', [
        revision,
        compact({
          ...previous,
          base_revision: previous.revision,
          revision: String(revision),
          upserts,
          deletes,
        }),
      ]);
      tx.run('UPDATE navigation_meta SET revision=? WHERE singleton=1', [revision]);
      tx.run('DELETE FROM navigation_dirty');
      updateProjection(tx, upserts, deletes);
    },
    rebuild(tx) {
      const legacy = prioritySourceShape(tx);
      const version = tx.get<{ schema_version: number }>(
        'SELECT schema_version FROM navigation_meta WHERE singleton=1',
      )!.schema_version;
      if (version >= 7 && legacy) throw new Error('current navigation requires priority removal');
      if (version < 7 && !legacy)
        tx.run('UPDATE navigation_meta SET schema_version=7 WHERE singleton=1');
      for (const table of [
        'navigation_records',
        'navigation_dependencies',
        'navigation_root_records',
        'navigation_dirty',
      ])
        tx.run(`DELETE FROM ${table}`);
      const roots: NavigationKey[] = [
        ['chief', 'chief'],
        ['totals', 'chief:chief'],
        ['totals', 'workspace'],
      ];
      for (const [table, kind, column] of [
        ['tickets', 'ticket', 'id'],
        ['managers', 'manager', 'id'],
        ['conversations', 'conversation', 'conversation_id'],
        ['projects', 'project', 'id'],
      ]) {
        for (const row of tx.all<Record<string, string>>(
          `SELECT ${column} FROM ${table} ORDER BY ${column}`,
        )) {
          roots.push([kind, row[column]]);
          if (kind === 'manager') roots.push(['totals', 'manager:' + row[column]]);
        }
      }
      if (tx.get("SELECT 1 FROM sqlite_master WHERE name='goals'"))
        for (const row of tx.all<{ id: string }>('SELECT id FROM goals ORDER BY id'))
          roots.push(['goal', row.id]);
      const context = newDerivationContext();
      context.ticketOwners = ticketOwners(tx);
      for (const root of roots.sort(
        (a, b) => Number(a[0] === 'totals') - Number(b[0] === 'totals') || compareKeys(a, b),
      ))
        replace(tx, root, context);
      tx.run('DELETE FROM navigation_journal');
      tx.run('UPDATE navigation_meta SET epoch=?,revision=0 WHERE singleton=1', [randomUUID()]);
      rebuildProjection(tx);
    },
  };
}

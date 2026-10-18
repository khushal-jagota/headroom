import { createCollection } from '@tanstack/db';
import type { SyncConfig } from '@tanstack/db';
import type { Continuity, NavigationRecord, Snapshot, RevisionBatch } from './contracts';
import { assertCursorMatches, parseRevisionBatch, parseSnapshotResponse, recordKey } from './validation';

export interface NavigationPublication { readonly publicationId: number; readonly continuity: Continuity; readonly cursor: string; readonly records: readonly NavigationRecord[] }
export type NavigationRow = { key: string; type: 'record'; record: NavigationRecord } | { key: string; type: 'publication'; publicationId: number; continuity: Continuity; cursor: string };
const PUBLICATION_KEY = 'publication';
export function publicationFromRows(rows: readonly NavigationRow[]): NavigationPublication | null {
  const metadata = rows.find(row => row.type === 'publication');
  if (!metadata || metadata.type !== 'publication') return null;
  return Object.freeze({ publicationId: metadata.publicationId, continuity: metadata.continuity, cursor: metadata.cursor, records: Object.freeze(rows.flatMap(row => row.type === 'record' ? [row.record] : [])) });
}
export function createNavigationCollection() {
  let sync: Parameters<SyncConfig<NavigationRow, string>['sync']>[0] | undefined;
  let publication: NavigationPublication | null = null;
  let destroyed = false;
  let publicationId = 0;
  const pendingCursors = new Set<string>();
  const listeners = new Set<(publication: NavigationPublication) => void>();
  const collection = createCollection<NavigationRow, string>({
    getKey: row => row.key, startSync:true, gcTime:Infinity,
    sync: { rowUpdateMode:'full', sync(params) { sync = params; return () => { sync = undefined; }; } },
  });
  const subscription = collection.subscribeChanges(() => {
    const next = publicationFromRows([...collection.values()]);
    if (next?.continuity === publication?.continuity) return;
    publication = next;
    if (next) pendingCursors.delete(next.cursor);
    if (publication) for (const listener of listeners) listener(publication);
  });
  function publish(records: NavigationRecord[], deletes: string[], value: Continuity, cursor: string, replace: boolean) {
    if (destroyed || !sync) throw new Error('Navigation collection is closed');
    sync.begin();
    if (replace) sync.truncate();
    else for (const key of deletes) if (collection.has(key)) sync.write({type:'delete', key});
    for (const record of records) {
      const key = recordKey(record);
      sync.write({type: replace || !collection.has(key) ? 'insert' : 'update', value: {key,type:'record',record}});
    }
    sync.write({ type: replace || !collection.has(PUBLICATION_KEY) ? 'insert' : 'update', value:{key:PUBLICATION_KEY,type:'publication',continuity:value,cursor,publicationId:++publicationId} });
    pendingCursors.add(cursor);
    try { sync.commit(); sync.markReady(); }
    catch (error) { pendingCursors.delete(cursor); throw error; }
  }
  return {
    collection,
    publishSnapshot(snapshot: Snapshot, cursor: string, signal?: AbortSignal): void {
      signal?.throwIfAborted();
      const validated = parseSnapshotResponse({wire_version:1,cursor,snapshot,proof:null}).snapshot;
      const {records,...value} = validated; publish(records,[],value,cursor,true);
    },
    publishBatch(batch: RevisionBatch, cursor: string, signal?: AbortSignal): void {
      signal?.throwIfAborted();
      const validated = parseRevisionBatch(batch); assertCursorMatches(cursor,validated);
      const current = publication?.continuity;
      if (!current || current.scope !== validated.scope || current.epoch !== validated.epoch || current.schema_version !== validated.schema_version || current.semantics !== validated.semantics || current.revision !== validated.base_revision) throw new Error('Batch continuity mismatch');
      const {upserts,deletes,base_revision: _,...value} = validated;
      publish(upserts,deletes.map(recordKey),value,cursor,false);
    },
    getPublication: () => publication,
    isPublicationPending: (cursor: string) => pendingCursors.has(cursor),
    subscribe(listener: (publication: NavigationPublication) => void): () => void { listeners.add(listener); return () => { listeners.delete(listener); }; },
    destroy(): void { destroyed = true; listeners.clear(); subscription.unsubscribe(); void collection.cleanup(); publication = null; },
  };
}
export type NavigationCollectionAdapter = ReturnType<typeof createNavigationCollection>;

import { createCollection } from '@tanstack/db';
import type { SyncConfig } from '@tanstack/db';
import type { Continuity, NavigationRecord, SnapshotResponse, BatchEnvelope, ViewRecord, ViewWindow, ViewCounts } from './contracts';
import { assertCursorMatches, parseBatchEnvelope, parseSnapshotResponse, recordKey, sameAuthority } from './validation';

export interface NavigationPublication {
  readonly publicationId: number;
  readonly admissionToken: string;
  readonly continuity: Continuity;
  readonly cursor: string | null;
  readonly mode: SnapshotResponse['mode'];
  readonly authority: SnapshotResponse['authority'];
  readonly records: readonly (NavigationRecord | ViewRecord)[];
  readonly view?: { readonly id: string; readonly scopeGeneration: number; readonly contentVersion: string; readonly orderingGeneration: string; readonly windows: readonly ViewWindow[]; readonly counts: ViewCounts; readonly reviewContext?: readonly import('./contracts').ReviewConversationContext[] };
}
type PublicationMetadata = Omit<NavigationPublication, 'records'>;
export type NavigationRow = { key: string; type: 'record'; record: NavigationRecord }
  | ({ key: string; type: 'publication' } & PublicationMetadata);
const PUBLICATION_KEY = 'publication';
export function publicationFromRows(rows: readonly NavigationRow[]): NavigationPublication | null {
  const metadata = rows.find(row => row.type === 'publication');
  if (!metadata || metadata.type !== 'publication') return null;
  const {key: _, type: __, ...publication} = metadata;
  return Object.freeze({ ...publication, records: Object.freeze(rows.flatMap(row => row.type === 'record' ? [row.record] : [])) });
}
export function createNavigationCollection() {
  let sync: Parameters<SyncConfig<NavigationRow, string>['sync']>[0] | undefined;
  let publication: NavigationPublication | null = null;
  let destroyed = false;
  let publicationId = 0;
  const pendingTokens = new Set<string>();
  const listeners = new Set<(publication: NavigationPublication) => void>();
  const collection = createCollection<NavigationRow, string>({
    getKey: row => row.key, startSync:true, gcTime:Infinity,
    sync: { rowUpdateMode:'full', sync(params) { sync = params; return () => { sync = undefined; }; } },
  });
  const subscription = collection.subscribeChanges(() => {
    const next = publicationFromRows([...collection.values()]);
    if (next?.publicationId === publication?.publicationId) return;
    publication = next;
    if (next) pendingTokens.delete(next.admissionToken);
    if (publication) for (const listener of listeners) listener(publication);
  });
  function publish(records: NavigationRecord[], deletes: string[], metadata: Omit<PublicationMetadata,'publicationId'>, replace: boolean) {
    if (destroyed || !sync) throw new Error('Navigation collection is closed');
    if (!metadata.admissionToken || pendingTokens.has(metadata.admissionToken)) throw new Error('Invalid publication admission token');
    sync.begin();
    if (replace) sync.truncate();
    else for (const key of deletes) if (collection.has(key)) sync.write({type:'delete', key});
    for (const record of records) {
      const key = recordKey(record);
      sync.write({type: replace || !collection.has(key) ? 'insert' : 'update', value: {key,type:'record',record}});
    }
    sync.write({ type: replace || !collection.has(PUBLICATION_KEY) ? 'insert' : 'update', value:{key:PUBLICATION_KEY,type:'publication',...metadata,publicationId:++publicationId} });
    pendingTokens.add(metadata.admissionToken);
    try { sync.commit(); sync.markReady(); }
    catch (error) { pendingTokens.delete(metadata.admissionToken); throw error; }
  }
  return {
    collection,
    publishSnapshot(response: SnapshotResponse, admissionToken: string, signal?: AbortSignal): void {
      signal?.throwIfAborted();
      const validated = parseSnapshotResponse(response);
      const {records,...continuity} = validated.snapshot;
      publish(records,[],{continuity,cursor:validated.cursor,mode:validated.mode,authority:validated.authority,admissionToken},true);
    },
    publishBatch(envelope: BatchEnvelope, cursor: string, admissionToken: string, signal?: AbortSignal): void {
      signal?.throwIfAborted();
      const validated = parseBatchEnvelope(envelope);
      const batch = validated.batch;
      assertCursorMatches(cursor,batch);
      const current = publication;
      if (!current || current.mode !== 'current' || !sameAuthority(current.authority,validated.authority)
        || current.continuity.scope !== batch.scope || current.continuity.schema_version !== batch.schema_version
        || current.continuity.semantics !== batch.semantics || current.continuity.epoch !== batch.epoch
        || current.continuity.revision !== batch.base_revision) throw new Error('Batch continuity or authority mismatch');
      const {upserts,deletes,base_revision: _,...continuity} = batch;
      publish(upserts,deletes.map(recordKey),{continuity,cursor,mode:'current',authority:validated.authority,admissionToken},false);
    },
    getPublication: () => publication,
    isPublicationPending: (admissionToken: string) => pendingTokens.has(admissionToken),
    subscribe(listener: (publication: NavigationPublication) => void): () => void { listeners.add(listener); return () => { listeners.delete(listener); }; },
    destroy(): void { destroyed = true; listeners.clear(); subscription.unsubscribe(); void collection.cleanup(); publication = null; },
  };
}
export type NavigationCollectionAdapter = ReturnType<typeof createNavigationCollection>;

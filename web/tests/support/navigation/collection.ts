import type { SnapshotResponse, BatchEnvelope } from '../../../src/lib/navigation/contracts';
import type { NavigationPublication } from '../../../src/lib/navigation/publication';
import { recordKey } from '../../../src/lib/navigation/validation';

/** Publish synthetic facts for tests of active consumer projections. */
export function createNavigationCollection() {
  let publication: NavigationPublication | null = null;
  let publicationId = 0;
  const listeners = new Set<(publication: NavigationPublication) => void>();
  return {
    publishSnapshot(response: SnapshotResponse, admissionToken: string): void {
      const { records, ...continuity } = response.snapshot;
      publication = { publicationId: ++publicationId, admissionToken, continuity,
        cursor: response.cursor, mode: response.mode, authority: response.authority, records };
      for (const listener of listeners) listener(publication);
    },
    publishBatch(envelope: BatchEnvelope, cursor: string, admissionToken: string): void {
      if (!publication) throw new Error('Test snapshot must precede a batch');
      const records = new Map(publication.records.map(record => [recordKey(record), record]));
      for (const record of envelope.batch.deletes) records.delete(recordKey(record));
      for (const record of envelope.batch.upserts) records.set(recordKey(record), record);
      const { upserts: _, deletes: __, base_revision: ___, ...continuity } = envelope.batch;
      publication = { publicationId: ++publicationId, admissionToken, continuity,
        cursor, mode: 'current', authority: envelope.authority, records: [...records.values()] };
      for (const listener of listeners) listener(publication);
    },
    getPublication: () => publication,
    subscribe(listener: (publication: NavigationPublication) => void): () => void {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    destroy(): void { publication = null; listeners.clear(); },
  };
}

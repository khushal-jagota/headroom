import { useLiveQuery } from '@tanstack/svelte-db';
import type { NavigationCollectionAdapter } from './collection';
import { publicationFromRows } from './collection';
import { recordKey } from './validation';

/** One live query includes both source rows and publication metadata. */
export function createNavigationSelectors(adapter: NavigationCollectionAdapter) {
  const query = useLiveQuery(q => q.from({ navigation: adapter.collection }));
  const publication = $derived.by(() => {
    const observed = publicationFromRows(query.data);
    const current = adapter.getPublication();
    if (!observed || !current || observed.publicationId !== current.publicationId || observed.cursor !== current.cursor || observed.records.length !== current.records.length) return null;
    const expected = new Map(current.records.map(record => [recordKey(record), record]));
    return observed.records.every(record => {
      const source = expected.get(recordKey(record));
      return source === record || (source !== undefined && JSON.stringify(source) === JSON.stringify(record));
    }) ? current : null;
  });
  return {
    get publication() { return publication; },
    get records() { return publication?.records ?? []; },
    recordsOfKind(kind: string) { return publication?.records.filter(record => record.kind === kind) ?? []; },
  };
}

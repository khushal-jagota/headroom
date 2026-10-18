import { createTransaction } from '@tanstack/db';
import { describe, expect, it } from 'vitest';
import { createNavigationCollection } from '../src/lib/navigation/collection';
import { encodeCursor } from '../src/lib/navigation/validation';
import type { Snapshot } from '../src/lib/navigation/contracts';
const snapshot = (revision:string, ids:string[]):Snapshot => ({scope:'navigation',schema_version:2,semantics:'generic-source-facts-v2',epoch:'a',revision,records:ids.map(id => ({kind:'project',id,value:{name:id,priority:null}}))});
describe('TanStack source publication', () => {
  it('does not announce accepted writes until TanStack publishes the transaction', async () => {
    const adapter = createNavigationCollection();
    const first = snapshot('1', ['x']);
    adapter.publishSnapshot(first, encodeCursor(first));
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    const transaction = createTransaction({ mutationFn: async () => pending });
    transaction.mutate(() => adapter.collection.update('["project","x"]', draft => {
      if (draft.type === 'record' && draft.record.kind === 'project') draft.record.value.name = 'pending';
    }));
    await Promise.resolve();
    const next = snapshot('2', ['x']);
    const {records, ...nextContinuity} = next;
    adapter.publishBatch({...nextContinuity,base_revision:'1',upserts:records,deletes:[]}, encodeCursor(next));
    expect(adapter.getPublication()?.continuity.revision).toBe('1');
    release();
    await transaction.when('settled');
    expect(adapter.getPublication()?.continuity.revision).toBe('2');
    expect(adapter.getPublication()?.records.map(record => record.id)).toEqual(['x']);
    adapter.destroy();
  });

  it('publishes metadata and records atomically; full snapshot removes absent records', () => {
    const adapter = createNavigationCollection();
    const observed: string[][] = [];
    adapter.subscribe(p => observed.push([p.continuity.revision,...p.records.map(r => r.id)]));
    const a = snapshot('9007199254740993',['publication','x']);
    adapter.publishSnapshot(a,encodeCursor(a));
    expect(observed).toEqual([['9007199254740993','publication','x']]);
    const b = snapshot('9007199254740994',['x']);
    adapter.publishSnapshot(b,encodeCursor(b));
    expect(observed.at(-1)).toEqual(['9007199254740994','x']);
    expect(adapter.collection.size).toBe(2);
    adapter.destroy();
  });
  it('rejects invalid continuity without changing collection', () => {
    const adapter=createNavigationCollection(); const a=snapshot('1',['x']); adapter.publishSnapshot(a,encodeCursor(a));
    const {records: _, ...continuity} = a;
    expect(() => adapter.publishBatch({...continuity,revision:'3',base_revision:'2',upserts:[],deletes:[]},encodeCursor({...a,revision:'3'}))).toThrow();
    expect(adapter.getPublication()?.continuity.revision).toBe('1'); adapter.destroy();
  });
});

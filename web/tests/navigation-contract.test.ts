import { describe, expect, it } from 'vitest';
import { compareRevision, decodeCursor, encodeCursor, parseSnapshotResponse, parseRevisionBatch } from '../src/lib/navigation/validation';
const continuity = {scope:'navigation',schema_version:2,semantics:'generic-source-facts-v2' as const,epoch:'é',revision:'9007199254740993'};
describe('canonical browser protocol boundary', () => {
  it('keeps revisions exact and cursor canonical', () => {
    expect(compareRevision('9007199254740993','9007199254740992')).toBe(1);
    expect(decodeCursor(encodeCursor(continuity))).toEqual({...continuity,wire_version:1});
    expect(() => decodeCursor(encodeCursor(continuity)+'=')).toThrow();
    expect(() => compareRevision('01','1')).toThrow();
  });
  it('rejects duplicate keys, unsafe integer values, unsupported versions, cursor mismatch', () => {
    const record={kind:'totals',id:'x',value:{child_count:1,distinct_conversation_count:1}};
    const value={wire_version:1,cursor:encodeCursor(continuity),snapshot:{...continuity,records:[record]},proof:null};
    expect(parseSnapshotResponse(value).snapshot.records).toHaveLength(1);
    expect(() => parseSnapshotResponse({...value,snapshot:{...value.snapshot,records:[record,record]}})).toThrow();
    expect(() => parseSnapshotResponse({...value,snapshot:{...value.snapshot,records:[{...record,value:{...record.value,child_count:9007199254740992}}]}})).toThrow();
    expect(() => parseSnapshotResponse({...value,snapshot:{...value.snapshot,revision:'3'}})).toThrow();
    expect(() => parseSnapshotResponse({...value,wire_version:2})).toThrow();
    expect(() => parseRevisionBatch({...continuity,base_revision:'0',upserts:[record],deletes:[{kind:'totals',id:'x'}]})).toThrow();
  });
});

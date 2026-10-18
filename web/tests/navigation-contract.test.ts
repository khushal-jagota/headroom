import { describe, expect, it } from 'vitest';
import { compareRevision, decodeCursor, encodeCursor, parseSnapshotResponse, parseRevisionBatch } from '../src/lib/navigation/validation';
const continuity = {scope:'navigation',schema_version:3,semantics:'generic-source-facts-v3' as const,epoch:'é',revision:'9007199254740993'};
describe('canonical browser protocol boundary', () => {
  it('requires canonical metadata fields and exact nullability instead of compatibility defaults', () => {
    const ticket={kind:'ticket',id:'t',value:{title:'Ticket',worker_type:'coding',stage:'needs_plan',
      ticket_status:'empty',blocked:false,updated_at:12,worker_step_claim_out:false,priority:'P2',
      deadline:null,project_id:null,orchestrator_kind:'chief',sprint_item_id:null,conversation_id:null,
      is_backlogged:false,ceiling:'needs_plan',ceiling_holder:{kind:'owner',id:'owner'},proposal_field:null,
      proposal_revision:0,proposal_created_at:null,stage_ownership:'worker',terminal:false,locale:'en'}};
    const orchestrator={kind:'orchestrator',id:'o',value:{title:'Orchestrator',project_id:'p',
      priority:'P1',created_at:3,settled:false,conversation_id:null}};
    const parse=(records:unknown[])=>parseSnapshotResponse({wire_version:1,cursor:encodeCursor(continuity),
      snapshot:{...continuity,records},proof:null});
    expect(parse([ticket,orchestrator]).snapshot.records).toHaveLength(2);
    expect(parse([{...ticket,value:{...ticket.value,proposal_created_at:13}}]).snapshot.records).toHaveLength(1);
    for (const field of ['blocked','updated_at','worker_step_claim_out','proposal_created_at']) {
      const value: Record<string,unknown>={...ticket.value};delete value[field];
      expect(()=>parse([{...ticket,value}])).toThrow();
    }
    for (const field of ['priority','created_at']) {
      const value: Record<string,unknown>={...orchestrator.value};delete value[field];
      expect(()=>parse([{...orchestrator,value}])).toThrow();
    }
    for (const [field,bad] of [['blocked',0],['updated_at',null],['updated_at',1.5],
      ['worker_step_claim_out',1],['proposal_created_at',false]]) {
      expect(()=>parse([{...ticket,value:{...ticket.value,[field as string]:bad}}])).toThrow();
    }
    expect(()=>parse([{...orchestrator,value:{...orchestrator.value,priority:null}}])).toThrow();
    expect(()=>parse([{...orchestrator,value:{...orchestrator.value,created_at:true}}])).toThrow();
  });
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

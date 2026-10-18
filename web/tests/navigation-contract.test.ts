import { describe, expect, it } from 'vitest';
import { compareRevision, decodeCursor, encodeCursor, parseSnapshotResponse, parseRevisionBatch, parseBatchEnvelope } from '../src/lib/navigation/validation';
import { authority, conversation, currentSnapshot, degradedSnapshot, envelope } from './support/navigation/fixtures';
import { SCHEMA_VERSION, SUPPORTED_SEMANTICS, WIRE_VERSION } from '../src/lib/navigation/contracts';
const continuity: import('../src/lib/navigation/contracts').Continuity = {scope:'navigation',schema_version:SCHEMA_VERSION,semantics:SUPPORTED_SEMANTICS,epoch:'é',revision:'9007199254740993'};
describe('canonical browser protocol boundary', () => {
  it('requires canonical metadata fields and exact nullability instead of compatibility defaults', () => {
    const ticket={kind:'ticket',id:'t',value:{title:'Ticket',worker_type:'coding',stage:'needs_plan',
      ticket_status:'empty',blocked:false,updated_at:12,worker_step_claim_out:false,priority:'P2',
      deadline:null,project_id:null,orchestrator_kind:'chief',sprint_item_id:null,conversation_id:null,
      is_backlogged:false,ceiling:'needs_plan',ceiling_holder:{kind:'owner',id:'owner'},proposal_field:null,
      proposal_revision:0,proposal_created_at:null,stage_ownership:'worker',terminal:false,locale:'en'}};
    const orchestrator={kind:'orchestrator',id:'o',value:{title:'Orchestrator',project_id:'p',
      priority:'P1',created_at:3,settled:false,conversation_id:null}};
    const parse=(records:unknown[])=>parseSnapshotResponse({wire_version:WIRE_VERSION,mode:'current',authority:authority('navigation'),cursor:encodeCursor(continuity),
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
    expect(decodeCursor(encodeCursor(continuity))).toEqual({...continuity,wire_version:WIRE_VERSION});
    expect(() => decodeCursor(encodeCursor(continuity)+'=')).toThrow();
    expect(() => compareRevision('01','1')).toThrow();
  });
  it('rejects duplicate keys, unsafe integer values, unsupported versions, cursor mismatch', () => {
    const record={kind:'totals',id:'x',value:{child_count:1,distinct_conversation_count:1}};
    const value={wire_version:WIRE_VERSION,mode:'current',authority:authority('navigation'),cursor:encodeCursor(continuity),snapshot:{...continuity,records:[record]},proof:null};
    expect(parseSnapshotResponse(value).snapshot.records).toHaveLength(1);
    expect(() => parseSnapshotResponse({...value,snapshot:{...value.snapshot,records:[record,record]}})).toThrow();
    expect(() => parseSnapshotResponse({...value,snapshot:{...value.snapshot,records:[{...record,value:{...record.value,child_count:9007199254740992}}]}})).toThrow();
    expect(() => parseSnapshotResponse({...value,snapshot:{...value.snapshot,revision:'3'}})).toThrow();
    expect(() => parseSnapshotResponse({...value,wire_version:1})).toThrow();
    expect(() => parseRevisionBatch({...continuity,base_revision:'0',upserts:[record],deletes:[{kind:'totals',id:'x'}]})).toThrow();
  });
});


describe('strict attention authority profiles',()=>{
  it('rejects missing knowledge, contradictory durable bits, and current facts in degraded snapshots',()=>{
    const record=conversation();
    const full=currentSnapshot({...continuity,records:[record]});
    expect(full.authority.permit.scope).not.toBe(full.snapshot.scope);
    expect(parseSnapshotResponse(full).mode).toBe('current');
    const invalid=(attention:unknown)=>({...full,snapshot:{...full.snapshot,records:[{...record,value:{...record.value,attention}}]}});
    expect(()=>parseSnapshotResponse(invalid(undefined))).toThrow();
    expect(()=>parseSnapshotResponse(invalid({current:null,durable:record.value.attention.durable}))).toThrow();
    const current=record.value.attention.current!;
    expect(()=>parseSnapshotResponse(invalid({current,durable:{message_for_owner:false,last_turn_failed:true}}))).toThrow();
    const degraded=degradedSnapshot(full.snapshot);
    expect(parseSnapshotResponse(degraded).cursor).toBeNull();
    expect(()=>parseSnapshotResponse({...degraded,cursor:full.cursor})).toThrow();
    expect(()=>parseSnapshotResponse({...degraded,proof:{status:'included',reason:null}})).toThrow();
    expect(()=>parseSnapshotResponse({...degraded,snapshot:full.snapshot})).toThrow();
    expect(()=>parseSnapshotResponse({...full,authority:{...full.authority,availability:{state:'unavailable',generation:'1',reason:null}}})).toThrow();
    expect(()=>parseSnapshotResponse({...full,authority:{...full.authority,permit:{...full.authority.permit,generation:'2'}}})).toThrow();
    expect(()=>parseSnapshotResponse({...full,authority:{...full.authority,delivery_session_id:''}})).toThrow();
    expect(()=>parseSnapshotResponse({...full,authority:{...full.authority,availability:{...full.authority.availability,generation:'01'}}})).toThrow();
    const batch=envelope({...continuity,base_revision:'0',revision:'1',upserts:[record],deletes:[]});
    expect(batch.authority.permit.scope).not.toBe(batch.batch.scope);
    expect(parseBatchEnvelope(batch).batch.upserts).toHaveLength(1);
    expect(()=>parseBatchEnvelope({...batch,authority:degraded.authority})).toThrow();
  });
});

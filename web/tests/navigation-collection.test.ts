import { createTransaction } from '@tanstack/db';
import { describe, expect, it } from 'vitest';
import { createNavigationCollection } from '../src/lib/navigation/collection';
import { encodeCursor } from '../src/lib/navigation/validation';
import type { Snapshot, TicketRecord } from '../src/lib/navigation/contracts';
import { railTicketEligible } from '../src/lib/workspaceRail';
import { currentSnapshot, degradedSnapshot, envelope } from './support/navigation/fixtures';
import { SCHEMA_VERSION, SUPPORTED_SEMANTICS } from '../src/lib/navigation/contracts';
import type { WorkItemTicketFacts } from '../src/lib/workItemPresentation';
const snapshot = (revision:string, ids:string[]):Snapshot => ({scope:'navigation',schema_version:SCHEMA_VERSION,semantics:SUPPORTED_SEMANTICS,epoch:'a',revision,records:ids.map(id => ({kind:'project',id,value:{name:id}}))});
describe('TanStack source publication', () => {
  it('publishes masked status and independent blockers together for the accepted rail classifier', () => {
    // Amendment/classifier boundary only: synthetic attention facts do not prove Signal parity.
    const adapter = createNavigationCollection();
    const record: TicketRecord = {kind:'ticket',id:'t',value:{
      title:'Ticket',recap_preview:'Ticket recap',worker_type:'coding',stage:'needs_implementation',ticket_status:'empty',
      blocked:false,created_at:1,updated_at:10,worker_step_claim_out:false,deadline:null,
      project_id:null,orchestrator_kind:'chief',sprint_item_id:null,conversation_id:null,
      is_backlogged:false,ceiling:'needs_implementation',ceiling_holder:{kind:'owner',id:'owner'},
      gating_field:'plan',proposal_field:null,proposal_revision:0,proposal_created_at:null,stage_ownership:'worker',
      terminal:false,locale:'en'
    }};
    const known: WorkItemTicketFacts = {id:'t',title:'Ticket',stage:'needs_implementation',
      ticket_status:'empty',awaiting_reply:false,awaiting_answer:false,awaiting_approval:false,
      awaiting_agent_approval:false,assigned:false,agent_state:'idle'};
    const observed: Array<[string,boolean,boolean]> = [];
    adapter.subscribe(publication => {
      const current = publication.records.find((r): r is TicketRecord => r.kind === 'ticket')!;
      const {ticket_status,blocked} = current.value;
      observed.push([ticket_status,blocked,railTicketEligible({...known,ticket_status,blocked})]);
    });
    const initial = {...snapshot('0',[]),records:[record]};
    adapter.publishSnapshot(currentSnapshot(initial),'initial');
    for (const [index,status] of ['awaiting_approval','agent','errored'].entries()) {
      const revision=String(index+1);
      const {records: _,...continuity}={...initial,revision};
      adapter.publishBatch(envelope({...continuity,base_revision:String(index),
        upserts:[{...record,value:{...record.value,ticket_status:status,blocked:true}}],deletes:[]}),
      encodeCursor(continuity),revision);
    }
    const {records: _,...last}={...initial,revision:'4'};
    adapter.publishBatch(envelope({...last,base_revision:'3',upserts:[record],deletes:[]}),encodeCursor(last),'last');
    expect(observed).toEqual([['empty',false,true],['awaiting_approval',true,false],
      ['agent',true,false],['errored',true,false],['empty',false,true]]);
    adapter.destroy();
  });
  it('does not announce accepted writes until TanStack publishes the transaction', async () => {
    const adapter = createNavigationCollection();
    const first = snapshot('1', ['x']);
    adapter.publishSnapshot(currentSnapshot(first),'first');
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    const transaction = createTransaction({ mutationFn: async () => pending });
    transaction.mutate(() => adapter.collection.update('["project","x"]', draft => {
      if (draft.type === 'record' && draft.record.kind === 'project') draft.record.value.name = 'pending';
    }));
    await Promise.resolve();
    const next = snapshot('2', ['x']);
    const {records, ...nextContinuity} = next;
    adapter.publishBatch(envelope({...nextContinuity,base_revision:'1',upserts:records,deletes:[]}), encodeCursor(next),'next');
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
    adapter.publishSnapshot(currentSnapshot(a),'a');
    expect(observed).toEqual([['9007199254740993','publication','x']]);
    const b = snapshot('9007199254740994',['x']);
    adapter.publishSnapshot(currentSnapshot(b),'b');
    expect(observed.at(-1)).toEqual(['9007199254740994','x']);
    expect(adapter.collection.size).toBe(2);
    adapter.destroy();
  });
  it('rejects invalid continuity without changing collection', () => {
    const adapter=createNavigationCollection(); const a=snapshot('1',['x']); adapter.publishSnapshot(currentSnapshot(a),'a');
    const {records: _, ...continuity} = a;
    expect(() => adapter.publishBatch(envelope({...continuity,revision:'3',base_revision:'2',upserts:[],deletes:[]}),encodeCursor({...a,revision:'3'}),'invalid')).toThrow();
    expect(adapter.getPublication()?.continuity.revision).toBe('1'); adapter.destroy();
  });
});

describe('atomic knowledge publication metadata',()=>{
  it('replaces mode and null cursor at the same structural revision without conflating admission tokens',()=>{
    const adapter=createNavigationCollection();
    const full=currentSnapshot(snapshot('1',['x']));
    const observed:unknown[]=[];
    adapter.subscribe(value=>observed.push([value.mode,value.cursor,value.admissionToken,value.records.map(record=>record.id)]));
    try {
      adapter.publishSnapshot(full,'first');
      adapter.publishSnapshot(degradedSnapshot(full.snapshot),'degraded');
      adapter.publishSnapshot(full,'recovered');
      expect(observed).toEqual([
        ['current',full.cursor,'first',['x']],['degraded',null,'degraded',['x']],['current',full.cursor,'recovered',['x']]]);
      expect(adapter.getPublication()?.publicationId).toBe(3);
    } finally {adapter.destroy();}
  });
});

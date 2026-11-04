import { describe,it,expect } from 'vitest';
import { createNavigationPresentationCache } from '../src/lib/navigation/presentation';
import { createNavigationCollection } from '../src/lib/navigation/collection';
import { createNavigationKnowledge } from '../src/lib/navigation/knowledge';
import { currentSnapshot,continuity,conversation } from './support/navigation/fixtures';
import type { TicketRecord,NavigationRecord,ViewRecord } from '../src/lib/navigation/contracts';
import { buildWorkspaceRail } from '../src/lib/workspaceRail';

const ticket:(TicketRecord & {value:import('../src/lib/navigation/contracts').TicketValue})={kind:'ticket',id:'a',value:{title:'A',recap:'',recap_preview:'',worker_type:'coding',stage:'needs_plan',ticket_status:'empty',blocked:false,created_at:1,updated_at:1,worker_step_claim_out:false,deadline:null,project_id:'p',manager_kind:'manager',manager_id:'parent',conversation_id:'a-conversation',is_backlogged:false,ceiling:'needs_plan',ceiling_holder:{kind:'owner',id:'owner'},gating_field:'plan',proposal_field:null,proposal_revision:0,proposal_created_at:null,stage_ownership:'worker',terminal:false,locale:'macos'}};
const records:NavigationRecord[]=[ticket,conversation('a-conversation'),{kind:'manager',id:'parent',value:{title:'Parent',recap:'',recap_preview:'',created_at:1,updated_at:1,project_id:'p',settled:false,conversation_id:null}},{kind:'project',id:'p',value:{name:'Project'}}];

describe('application presentation',()=>{
  it('excludes a backlogged proposal loaded by a child view and restores it after backlog clears',()=>{
    const collection=createNavigationCollection(),knowledge=createNavigationKnowledge(),presentationFor=createNavigationPresentationCache();
    const pending:(TicketRecord & {value:import('../src/lib/navigation/contracts').TicketValue})={...ticket,value:{...ticket.value,ticket_status:'awaiting_approval',
      proposal_field:'plan',proposal_revision:1,proposal_created_at:1,is_backlogged:true}};
    try {
      collection.publishSnapshot(currentSnapshot({...continuity,records:records.map(row=>row===ticket?pending:row)}),'base');
      const base=collection.getPublication()!;
      const childWindow={window_id:'backlog-children',request:{section:'children' as const,
        owner:{kind:'manager' as const,id:'parent'},group:'all' as const,child_filter:'backlogged' as const,
        anchor:null,direction:'after' as const,limit:30},members:[{kind:'ticket' as const,id:'a'}],
        total:1,previous:null,next:null,forward_overlap:null,backward_overlap:null};
      const publication={...base,view:{id:'scoped',scopeGeneration:1,contentVersion:'one',orderingGeneration:'fixture',
        windows:[childWindow],counts:{running_conversations:1,out_claims:0,review:0,unread:0,settled:0}}};
      const excluded=presentationFor(knowledge.accept(publication));
      expect(excluded.ticketsById.get('a')?.is_backlogged).toBe(true);
      expect(excluded.review.items).toEqual([]);
      const restored=presentationFor(knowledge.accept({...publication,records:publication.records.map(row=>
        row.kind==='ticket'?{...pending,value:{...pending.value,is_backlogged:false}}:row),
        view:{...publication.view,contentVersion:'two',counts:{...publication.view.counts,review:1}}}));
      expect(restored.review.items.map(item=>item.review_item_type==='ticket' ? item.ticket_id : item.conversation_id)).toEqual(['a']);
      expect(restored.ticketsById.get('a')?.is_backlogged).toBe(false);
    } finally {collection.destroy();}
  });

  it('shares one effective result and parent indexes while knowledge-only loss/read changes invalidate it',()=>{
    const collection=createNavigationCollection(),knowledge=createNavigationKnowledge(),presentationFor=createNavigationPresentationCache();
    try {
      collection.publishSnapshot(currentSnapshot({...continuity,records}),'initial');
      const view=knowledge.accept(collection.getPublication()!);
      const first=presentationFor(view);
      expect(presentationFor(view)).toBe(first);
      expect(first.managersById.get('parent')?.tickets[0]).toBe(first.ticketsById.get('a'));
      expect(first.ticketsByParent.get('manager:parent')?.[0]).toBe(first.ticketsById.get('a'));
      expect(first.ticketMastheadsById.get('a')?.project).toBe('Project');
      const missingParent={...view,publication:{...view.publication,records:view.publication.records.filter(record=>record.kind!=='manager')}};
      expect(presentationFor(missingParent).ticketMastheadsById.has('a')).toBe(false);
      expect(createNavigationPresentationCache()(view)).not.toBe(first);
      const lost=knowledge.loseRuntime()!;
      const next=presentationFor(lost);
      expect(lost.publication).toBe(view.publication);
      expect(next).not.toBe(first);
      expect(next.tickets[0].conversation_signal?.working).toBeUndefined();
      const read=knowledge.invalidateDurableFact('a-conversation','message_for_owner',10)!;
      expect(read.publication).toBe(view.publication);
      expect(presentationFor(read).tickets[0].conversation_signal?.message_for_owner).toBeUndefined();
      expect(presentationFor(read)).not.toBe(next);
      // Settled messages retain historical done/blocked Tickets, even when the
      // Ticket has no current conversation pointer and is absent from groups.
      const historicalRecords:NavigationRecord[]=[...records.map(record=>record.kind==='ticket' && !('integrity_error' in record.value)
        ? {...record,value:{...record.value,stage:'done',terminal:true,blocked:true,conversation_id:null}}
        : record.kind==='manager' ? {...record,value:{...record.value,settled:true}} : record),
        conversation('history'),{kind:'address',id:'historical-address',value:{owner_kind:'ticket',owner_id:'a',
          conversation_id:'history',current:false,parent_kind:'manager',parent_id:'parent'}}];
      collection.publishSnapshot(currentSnapshot({...continuity,revision:'1',records:historicalRecords}),'historical');
      const historical=presentationFor(knowledge.accept(collection.getPublication()!));
      const historicalRail=buildWorkspaceRail(historical.tickets,historical.managers);

      expect(historicalRail.settledItems[0].groups).toHaveLength(0);
      expect(historical.messages.find(row=>row.parentId==='parent')?.conversationId).toBe('history');

      collection.publishSnapshot(currentSnapshot({...continuity,revision:'2',records:historicalRecords.map(record=>
        record.kind==='conversation' && record.id==='history' ? conversation('history',0,false) : record)}),'quiet-historical');
      const quiet=presentationFor(knowledge.accept(collection.getPublication()!));
      expect(quiet.messages.find(row=>row.parentId==='parent')).toBeUndefined();

    } finally {collection.destroy();}
  });

  it('shares parent unread windows independently of the bounded global page and clears their read facts',()=>{
    const collection=createNavigationCollection(),knowledge=createNavigationKnowledge(),presentationFor=createNavigationPresentationCache();
    try {
      collection.publishSnapshot(currentSnapshot({...continuity,records}),'base');
      const base=collection.getPublication()!;
      const history=conversation('history');
      const window={window_id:'parent-unread',request:{section:'unread' as const,owner:{kind:'manager' as const,id:'parent'},group:'all' as const,child_filter:'all' as const,anchor:null,direction:'after' as const,limit:60},
        members:[{kind:'address' as const,id:'history-address'}],total:7,previous:null,next:'more',forward_overlap:null,backward_overlap:null};
      const scopedRecords:ViewRecord[]=[...records.filter(row=>row.kind!=='totals').map(row=>row.kind==='ticket' ? {...row,value:{...row.value,recap_reference:null,stage:'done',conversation_id:null}}
        : row.kind==='manager' ? {...row,value:{...row.value,recap:'Parent recap',recap_reference:null,settled:true}} : row),
        history,{kind:'address',id:'history-address',value:{owner_kind:'ticket',owner_id:'a',conversation_id:'history',current:false,parent_kind:'manager',parent_id:'parent'}},
        {kind:'manager',id:'lean',value:{title:'Lean row',created_at:2,updated_at:2,settled:true,conversation_id:null}}];
      const publication={...base,records:scopedRecords,view:{id:'scoped',scopeGeneration:1,contentVersion:'one',orderingGeneration:'fixture',
        windows:[window,{...window,window_id:'global-unread',request:{...window.request,owner:null,limit:0},members:[],total:7}],counts:{running_conversations:1,out_claims:0,review:0,unread:7,settled:2}}};
      const effective=knowledge.accept(publication),first=presentationFor(effective);
      expect(presentationFor(effective)).toBe(first);
      expect(first.unreadConversationIdsByWindow.get('parent-unread')).toEqual(new Set(['history']));
      expect(first.unreadConversationIdsByWindow.get('global-unread')).toEqual(new Set());
      expect(first.viewWindows[0].total).toBe(7);
      expect(first.viewWindows[0].next).toBe('more');
      expect(first.ticketMastheadsById.get('a')?.project).toBe('Project');
      expect(first.rawManagersById.get('parent')?.value.recap).toBe('Parent recap');
      const rail=buildWorkspaceRail(first.tickets,[...first.managers,...first.settledManagers]);
      expect(rail.settledItems.find(item=>item.id==='lean')).not.toHaveProperty('recap');
      expect(rail.settledItems.find(item=>item.id==='lean')).not.toHaveProperty('projectId');
      const read=knowledge.invalidateDurableFact('history','message_for_owner',20)!;
      const next=presentationFor(read);
      expect(next).not.toBe(first);
    } finally {collection.destroy();}
  });


  ;


});

describe('mixed review presentation',()=>{
  it('uses oldest unread time, canonical addresses and historical owner context without proposal priority',()=>{
    const collection=createNavigationCollection(),knowledge=createNavigationKnowledge(),presentationFor=createNavigationPresentationCache();
    const pending:(TicketRecord & {value:import('../src/lib/navigation/contracts').TicketValue})={...ticket,value:{...ticket.value,ticket_status:'awaiting_approval',
      recap:'Worker recap',proposal_field:'plan',proposal_revision:1,proposal_created_at:2}};
    const historical=conversation('historical');historical.value.first_unread_owner_message_at=2;
    const current=conversation('a-conversation');current.value.first_unread_owner_message_at=1;
    const addresses:NavigationRecord[]=[
      {kind:'address',id:'old-worker',value:{owner_kind:'ticket',owner_id:'a',conversation_id:'historical',current:false,parent_kind:'manager',parent_id:'parent'}},
      {kind:'address',id:'current-manager',value:{owner_kind:'manager',owner_id:'parent',conversation_id:'historical',current:true,parent_kind:null,parent_id:null}},
      {kind:'address',id:'current-worker',value:{owner_kind:'ticket',owner_id:'a',conversation_id:'a-conversation',current:true,parent_kind:'manager',parent_id:'parent'}},
    ];
    const source=[pending,current,historical,...records.filter(row=>row.kind!=='ticket' && row.kind!=='conversation'),...addresses];
    try {
      collection.publishSnapshot(currentSnapshot({...continuity,records:source}),'mixed');
      const initial=collection.getPublication()!;
      const items=presentationFor(knowledge.accept(initial)).review.items;
      expect(items).toMatchObject([
        {review_item_type:'ticket',ticket_id:'a',waiting_since:1,proposal:{waiting_since:2},messages:[{conversation_id:'a-conversation',owner_id:'a',recap:'Worker recap'}]},
        {review_item_type:'message',conversation_id:'historical',waiting_since:2,owner_id:'parent',title:'Parent'},
      ]);
      const replaced=presentationFor(knowledge.accept({...initial,records:source.filter(row=>row.id!=='current-manager')}));
      expect(replaced.review.items).toHaveLength(1);
      expect(replaced.review.items[0]).toMatchObject({review_item_type:'ticket',ticket_id:'a',messages:[{conversation_id:'a-conversation'},{conversation_id:'historical',owner_id:'a',title:'A',recap:'Worker recap'}]});
      const settled=source.map(row=>row.kind==='manager' ? {...row,value:{...row.value,settled:true}} : row);
      const liveChild=presentationFor(knowledge.accept({...initial,records:settled}));
      expect(liveChild.review.items).toHaveLength(1);
      expect(liveChild.review.items[0]).toMatchObject({ticket_id:'a',messages:[{conversation_id:'a-conversation'}]});
      // The settled canonical Manager must not fall back to the historical Ticket.
      expect(liveChild.messages.some(message=>message.conversationId==='historical')).toBe(true);
      const backlogged=settled.map(row=>row.kind==='ticket' && !('integrity_error' in row.value) ? {...row,value:{...row.value,is_backlogged:true}} : row);
      const excluded=presentationFor(knowledge.accept({...initial,records:backlogged}));
      expect(excluded.review.items).toEqual([]);
      expect(excluded.messages.map(message=>message.conversationId)).toContain('a-conversation');
      const done=settled.map(row=>row.kind==='ticket' && !('integrity_error' in row.value) ? {...row,value:{...row.value,stage:'done',ticket_status:'empty' as const}} : row);
      expect(presentationFor(knowledge.accept({...initial,records:done})).review.items).toEqual([]);
      const messageOnly=settled.map(row=>row.kind==='ticket' && !('integrity_error' in row.value) ? {...row,value:{...row.value,ticket_status:'empty' as const,proposal_field:null,proposal_created_at:null}} : row);
      expect(presentationFor(knowledge.accept({...initial,records:messageOnly})).review.items).toMatchObject([{review_item_type:'ticket',ticket_id:'a',proposal:null,messages:[{conversation_id:'a-conversation'}]}]);
      knowledge.accept({...initial,records:source.filter(row=>row.id!=='current-manager')});
      const lost=knowledge.loseRuntime()!;
      expect(presentationFor(lost).review.items).toHaveLength(1);
      const invalid=knowledge.invalidateDurableFact('historical','message_for_owner',5)!;
      expect(presentationFor(invalid).review.items).toHaveLength(1);
      expect(presentationFor(invalid).review.items[0]).toMatchObject({messages:[{conversation_id:"a-conversation"}]});
    } finally {collection.destroy();}
  });
});

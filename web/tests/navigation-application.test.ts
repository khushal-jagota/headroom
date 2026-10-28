import { describe,it,expect } from 'vitest';
import { createCommandEvidence,type RecordImpact } from '../src/lib/navigation/commandEvidence';
import { createVisibilityTracker } from '../src/lib/navigation/visibility';
import { createNavigationPresentationCache } from '../src/lib/navigation/presentation';
import { createNavigationCollection } from '../src/lib/navigation/collection';
import { createNavigationKnowledge } from '../src/lib/navigation/knowledge';
import { currentSnapshot,continuity,conversation } from './support/navigation/fixtures';
import type { TicketRecord,NavigationRecord,ViewRecord } from '../src/lib/navigation/contracts';
import { workspaceHoverImpact,managerChildrenImpact,workspaceRailImpact } from '../src/lib/workspaceNavigationImpact';
import { buildWorkspaceRail } from '../src/lib/workspaceRail';
import { runtimeCountImpact,reviewBadgeImpact } from '../src/lib/reviewNavigationImpact';

const ticket:TicketRecord={kind:'ticket',id:'a',value:{title:'A',recap:'',recap_preview:'',worker_type:'coding',stage:'needs_plan',ticket_status:'empty',blocked:false,created_at:1,updated_at:1,worker_step_claim_out:false,deadline:null,project_id:'p',manager_kind:'manager',manager_id:'parent',conversation_id:'a-conversation',is_backlogged:false,ceiling:'needs_plan',ceiling_holder:{kind:'owner',id:'owner'},gating_field:'plan',proposal_field:null,proposal_revision:0,proposal_created_at:null,stage_ownership:'worker',terminal:false,locale:'macos'}};
const records:NavigationRecord[]=[ticket,conversation('a-conversation'),{kind:'manager',id:'parent',value:{title:'Parent',recap:'',recap_preview:'',created_at:1,updated_at:1,project_id:'p',settled:false,conversation_id:null}},{kind:'project',id:'p',value:{name:'Project'}}];
const titleImpact:RecordImpact={kind:'ticket',id:'a',fields:['title','updated_at']};
const fence={...continuity,version:1,coverage:'complete',witnesses:['a'.repeat(64)],signature:'synthetic'};

describe('application presentation and command evidence',()=>{
  it('excludes a backlogged proposal loaded by a child view and restores it after backlog clears',()=>{
    const collection=createNavigationCollection(),knowledge=createNavigationKnowledge(),presentationFor=createNavigationPresentationCache();
    const pending:TicketRecord={...ticket,value:{...ticket.value,ticket_status:'awaiting_approval',
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
      expect(first.ticketsById.get('a')?.locale).toBe('macos');
      expect(first.managersById.get('parent')?.tickets[0].locale).toBe('macos');
      expect(first.ticketMastheadsById.get('a')?.locale).toBe('macos');
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
      // Title also updates activity order, but has no runtime-count or badge effect.
      expect(managerChildrenImpact(titleImpact,{kind:'manager',id:'parent'},first,first.tickets,[{key:'empty',tickets:first.tickets}],{})).toBe(true);
      expect(managerChildrenImpact(titleImpact,{kind:'manager',id:'other'},first,[],[],{})).toBe(false);
      expect(managerChildrenImpact({kind:'ticket',id:'a',fields:['manager_id']},{kind:'manager',id:'other'},first,[],[],{})).toBe(true);
      expect(workspaceHoverImpact(titleImpact,'ticket','a','a-conversation',first)).toBe(true);
      expect(workspaceHoverImpact(titleImpact,'ticket','other',null,first)).toBe(false);
      // A tail change can preserve the list preview but must refresh an open popup.
      expect(workspaceHoverImpact({kind:'ticket',id:'a',fields:['recap']},'ticket','a','a-conversation',first)).toBe(true);
      expect(workspaceHoverImpact({kind:'manager',id:'parent',fields:['recap']},'manager','parent',null,first)).toBe(true);
      expect(managerChildrenImpact(titleImpact,{kind:'manager',id:'parent'},first,[],[],{})).toBe(false);
      expect(managerChildrenImpact(titleImpact,{kind:'manager',id:'parent'},first,first.tickets,[{key:'empty',tickets:first.tickets}],{empty:false})).toBe(false);
      const rail=buildWorkspaceRail(first.tickets,first.managers);
      expect(workspaceRailImpact(titleImpact,first,rail,false)).toBe(true);
      expect(workspaceRailImpact({kind:'conversation',id:'unlinked',fields:['attention']},first,rail,false)).toBe(false);
      expect(workspaceRailImpact({kind:'project',id:'other-project',fields:['name']},first,rail,false)).toBe(false);
      const excluded={...first,ticketsById:new Map([['a',{...first.tickets[0],is_backlogged:true}]])};
      const excludedRail=buildWorkspaceRail([...excluded.ticketsById.values()],first.managers.map(item=>({...item,tickets:[...excluded.ticketsById.values()]})));
      expect(workspaceRailImpact(titleImpact,excluded,excludedRail,false)).toBe(false);
      expect(workspaceRailImpact({kind:'ticket',id:'a',fields:['is_backlogged','updated_at']},excluded,excludedRail,false)).toBe(true);
      // A parent's generated fallback recap counts eligible child rows. Saved
      // recap text and unrelated parents do not depend on that count.
      const backlogImpact:RecordImpact={kind:'ticket',id:'a',fields:['is_backlogged','updated_at']};
      expect(workspaceHoverImpact(backlogImpact,'manager','parent',null,first,true)).toBe(true);
      expect(workspaceHoverImpact(backlogImpact,'manager','parent',null,first,false)).toBe(false);
      expect(workspaceHoverImpact(backlogImpact,'manager','other',null,first,true)).toBe(false);
      const chiefView={...view,publication:{...view.publication,records:records.map(record=>record.kind==='ticket'
        ? {...record,value:{...record.value,manager_kind:'chief' as const,manager_id:null}} : record)}};
      const chiefPresentation=presentationFor(chiefView);
      expect(workspaceHoverImpact(backlogImpact,'chief','chief',null,chiefPresentation,true)).toBe(true);
      expect(workspaceHoverImpact(titleImpact,'chief','chief',null,chiefPresentation,true)).toBe(false);

      // Settled messages retain historical done/blocked Tickets, even when the
      // Ticket has no current conversation pointer and is absent from groups.
      const historicalRecords:NavigationRecord[]=[...records.map(record=>record.kind==='ticket'
        ? {...record,value:{...record.value,stage:'done',terminal:true,blocked:true,conversation_id:null}}
        : record.kind==='manager' ? {...record,value:{...record.value,settled:true}} : record),
        conversation('history'),{kind:'address',id:'historical-address',value:{owner_kind:'ticket',owner_id:'a',
          conversation_id:'history',current:false,parent_kind:'manager',parent_id:'parent'}}];
      collection.publishSnapshot(currentSnapshot({...continuity,revision:'1',records:historicalRecords}),'historical');
      const historical=presentationFor(knowledge.accept(collection.getPublication()!));
      const historicalRail=buildWorkspaceRail(historical.tickets,historical.managers);
      const historyImpact:RecordImpact={kind:'conversation',id:'history',fields:['attention']};
      expect(historicalRail.settledItems[0].groups).toHaveLength(0);
      expect(historical.messages.find(row=>row.parentId==='parent')?.conversationId).toBe('history');
      expect(workspaceRailImpact({kind:'manager',id:'off-page',fields:['updated_at']},historical,historicalRail,true)).toBe(true);
      expect(workspaceRailImpact({kind:'manager',id:'off-page',fields:['updated_at']},historical,historicalRail,false)).toBe(false);
      expect(workspaceRailImpact(titleImpact,historical,historicalRail,true)).toBe(false);
      expect(workspaceRailImpact(historyImpact,historical,historicalRail,true)).toBe(false);
      const differentPointer={...historical,ticketsById:new Map([['a',{...historical.tickets[0],conversation_id:'unshown-current'}]])};
      expect(workspaceRailImpact({kind:'conversation',id:'unshown-current',fields:['attention']},differentPointer,historicalRail,true)).toBe(false);
      expect(workspaceRailImpact(titleImpact,historical,historicalRail,false)).toBe(false);
      expect(workspaceRailImpact(historyImpact,historical,historicalRail,false)).toBe(false);
      collection.publishSnapshot(currentSnapshot({...continuity,revision:'2',records:historicalRecords.map(record=>
        record.kind==='conversation' && record.id==='history' ? conversation('history',0,false) : record)}),'quiet-historical');
      const quiet=presentationFor(knowledge.accept(collection.getPublication()!));
      expect(quiet.messages.find(row=>row.parentId==='parent')).toBeUndefined();
      expect(workspaceRailImpact(historyImpact,quiet,buildWorkspaceRail(quiet.tickets,quiet.managers),true)).toBe(false);
      const movedBacklogged={...quiet.tickets[0],is_backlogged:true,
        manager:{kind:'manager' as const,id:'other'},manager_id:'other'};
      const backlogged={...quiet,tickets:[movedBacklogged],ticketsById:new Map([['a',movedBacklogged]])};
      const backloggedRail=buildWorkspaceRail(backlogged.tickets,quiet.managers.map(item=>({...item,tickets:[]})));
      // The historical alias stays under the former parent. Canonical backlog
      // still excludes it even though the Ticket is absent from that parent's rows.
      expect(workspaceRailImpact(historyImpact,backlogged,backloggedRail,true)).toBe(false);
      const unreadBacklogged={...backlogged,messages:historical.messages};
      expect(workspaceRailImpact(titleImpact,unreadBacklogged,backloggedRail,true)).toBe(false);
      expect(workspaceRailImpact(historyImpact,unreadBacklogged,backloggedRail,true)).toBe(false);
      expect(runtimeCountImpact(titleImpact,first)).toBe(false);
      expect(reviewBadgeImpact(titleImpact)).toBe(false);
      expect(reviewBadgeImpact({kind:'ticket',id:'a',fields:['is_backlogged']})).toBe(true);
      expect(reviewBadgeImpact({kind:'ticket',id:'a',fields:['stage']})).toBe(true);
      expect(reviewBadgeImpact({kind:'manager',id:'parent',fields:['settled']})).toBe(true);
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

  it('captures only relevant actual instances before request, returns domain result before paint, and retains bounded outcomes',async()=>{
    const collection=createNavigationCollection();
    const tracker=createVisibilityTracker(async()=>{throw new Error('unexpected certification');});
    try {
      collection.publishSnapshot(currentSnapshot({...continuity,records}),'initial');
      const publication=collection.getPublication()!;
      tracker.generation(1);tracker.publication(publication);
      const commands=createCommandEvidence({getState:()=>({connection:'subscribed',generation:1,error:null,publication,effective:undefined}),registerObserver:tracker.registerObserver,waitForVisibility:tracker.waitForVisibility},2);
      const relevant=commands.register('a',impact=>impact.kind==='ticket' && impact.id==='a');
      const unrelated=commands.register('other',impact=>impact.kind==='ticket' && impact.id==='other');
      const transitions:string[]=[];commands.subscribe(values=>{if(values.length)transitions.push(values.at(-1)!.status);});
      const receipt={fate:'original-domain-result'};
      const result=await commands.runCommand({complete:true,records:[titleImpact]},async metadata=>{
        expect(commands.evidence.at(-1)?.observers).toEqual(['a']);
        // Later registration and unrelated disposal cannot alter the fixed requirement.
        commands.register('late',()=>true);commands.dispose(unrelated);
        metadata.headers=new Headers({'Panels-Navigation-Fence':JSON.stringify(fence)});
        return receipt;
      },'title update');
      expect(result).toBe(receipt);
      expect(commands.evidence.at(-1)).toMatchObject({label:'title update',capturedGeneration:1,fence:{epoch:fence.epoch,revision:fence.revision,coverage:'complete',witnessCount:1}});
      expect(commands.evidence.at(-1)?.status).toBe('pending');
      const observation=relevant.observeSelectors(publication,1);relevant.acknowledgeRender(publication,observation);
      await Promise.resolve();
      expect(commands.evidence.at(-1)?.status).toBe('included');
      expect(transitions).toContain('pending');expect(transitions).toContain('included');
      await commands.runCommand({complete:false,reason:'uncharacterized-side-effect'},async()=>receipt);
      expect(commands.evidence.at(-1)).toMatchObject({status:'unknown',reason:'uncharacterized-side-effect'});
      const failure=new Error('original failure');
      await expect(commands.runCommand({complete:true,records:[titleImpact]},async()=>{throw failure;})).rejects.toBe(failure);
      expect(commands.evidence).toHaveLength(2);
      expect(commands.evidence.at(-1)).toMatchObject({status:'unknown',reason:'command_failed'});
    } finally {tracker.dispose();collection.destroy();}
  });

  it('does not claim vacuous success and a disposed relevant instance stays unknown',async()=>{
    const collection=createNavigationCollection(),tracker=createVisibilityTracker(async()=>{throw new Error('unexpected certification');});
    try {
      collection.publishSnapshot(currentSnapshot({...continuity,records}),'initial');
      tracker.generation(1);tracker.publication(collection.getPublication()!);
      const commands=createCommandEvidence({registerObserver:tracker.registerObserver,waitForVisibility:tracker.waitForVisibility},3);
      const receipt={ok:true};
      await commands.runCommand({complete:true,records:[titleImpact]},async()=>receipt);
      expect(commands.evidence.at(-1)).toMatchObject({status:'unknown',reason:'no_relevant_observer'});
      const relevant=commands.register('a',()=>true);
      await commands.runCommand({complete:true,records:[titleImpact]},async metadata=>{metadata.headers=new Headers({'Panels-Navigation-Fence':JSON.stringify(fence)});return receipt;});
      commands.dispose(relevant);await Promise.resolve();
      expect(commands.evidence.at(-1)).toMatchObject({status:'unknown',reason:'observer_removed'});
    } finally {tracker.dispose();collection.destroy();}
  });
  it('diagnostic subscribers and synchronous visibility failure cannot mask the original domain result or failure',async()=>{
    const tracker=createVisibilityTracker(async()=>{throw new Error('unused');});
    const commands=createCommandEvidence({registerObserver:tracker.registerObserver,waitForVisibility:()=>{throw new Error('visibility failed synchronously');}},2);
    commands.register('a',()=>true);
    commands.subscribe(values=>{if(values.at(-1)?.status==='pending')throw new Error('diagnostic subscriber failed');});
    const receipt={fate:'unchanged'};
    let invoked=false;
    try {
      expect(await commands.runCommand({complete:true,records:[titleImpact]},async metadata=>{
        invoked=true;metadata.headers=new Headers({'Panels-Navigation-Fence':JSON.stringify(fence)});return receipt;
      })).toBe(receipt);
      expect(invoked).toBe(true);
      const entry=commands.evidence[0];
      expect(entry).toMatchObject({status:'unknown',reason:'visibility_failed',diagnosticError:'subscriber_failed',impact:{complete:true,records:[titleImpact]}});
      expect(entry.startedAt).toBeLessThanOrEqual(entry.domainCompletedAt!);
      expect(entry.domainCompletedAt!).toBeLessThanOrEqual(entry.settledAt!);
      expect(Object.isFrozen(entry.impact)).toBe(true);
      const failure=new Error('unchanged original failure');
      await expect(commands.runCommand({complete:true,records:[titleImpact]},async()=>{throw failure;})).rejects.toBe(failure);
      expect(commands.evidence.at(-1)).toMatchObject({status:'unknown',reason:'command_failed',diagnosticError:'subscriber_failed'});
    } finally {tracker.dispose();}
  });

  it('retains no-op evidence and never resurrects an evicted pending invocation',async()=>{
    const tracker=createVisibilityTracker(async()=>{throw new Error('unused');});
    let finish!:(value:{status:'included';reason:null})=>void;
    let calls=0;
    const commands=createCommandEvidence({registerObserver:tracker.registerObserver,waitForVisibility:()=>{
      calls+=1;
      return calls===1 ? new Promise(resolve=>{finish=resolve;}) : Promise.resolve({status:'no_op',reason:null});
    }},2);
    commands.register('a',()=>true);
    const request=async(metadata:{headers?:Headers})=>{metadata.headers=new Headers({'Panels-Navigation-Fence':JSON.stringify(fence)});return 'domain result';};
    try {
      await commands.runCommand({complete:true,records:[titleImpact]},request);
      await commands.runCommand({complete:true,records:[titleImpact]},request);
      await Promise.resolve();
      expect(commands.evidence.at(-1)).toMatchObject({id:2,status:'no_op'});
      await commands.runCommand({complete:false,reason:'explicit uncertainty'},request);
      finish({status:'included',reason:null});await Promise.resolve();
      expect(commands.evidence.map(value=>[value.id,value.status])).toEqual([[2,'no_op'],[3,'unknown']]);
    } finally {tracker.dispose();}
  });

});

describe('mixed review presentation',()=>{
  it('uses oldest unread time, canonical addresses and historical owner context without proposal priority',()=>{
    const collection=createNavigationCollection(),knowledge=createNavigationKnowledge(),presentationFor=createNavigationPresentationCache();
    const pending:TicketRecord={...ticket,value:{...ticket.value,ticket_status:'awaiting_approval',
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
      const backlogged=settled.map(row=>row.kind==='ticket' ? {...row,value:{...row.value,is_backlogged:true}} : row);
      const excluded=presentationFor(knowledge.accept({...initial,records:backlogged}));
      expect(excluded.review.items).toEqual([]);
      expect(excluded.messages.map(message=>message.conversationId)).toContain('a-conversation');
      const done=settled.map(row=>row.kind==='ticket' ? {...row,value:{...row.value,stage:'done',ticket_status:'empty' as const}} : row);
      expect(presentationFor(knowledge.accept({...initial,records:done})).review.items).toEqual([]);
      const messageOnly=settled.map(row=>row.kind==='ticket' ? {...row,value:{...row.value,ticket_status:'empty' as const,proposal_field:null,proposal_created_at:null}} : row);
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

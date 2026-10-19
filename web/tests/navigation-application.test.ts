import { describe,it,expect } from 'vitest';
import { createCommandEvidence,type RecordImpact } from '../src/lib/navigation/commandEvidence';
import { createVisibilityTracker } from '../src/lib/navigation/visibility';
import { createNavigationPresentationCache } from '../src/lib/navigation/presentation';
import { createNavigationCollection } from '../src/lib/navigation/collection';
import { createNavigationKnowledge } from '../src/lib/navigation/knowledge';
import { currentSnapshot,continuity,conversation } from './support/navigation/fixtures';
import type { TicketRecord,NavigationRecord } from '../src/lib/navigation/contracts';
import { workspaceHoverImpact,orchestratorChildrenImpact,workspaceRailImpact } from '../src/lib/workspaceNavigationImpact';
import { buildWorkspaceRail } from '../src/lib/workspaceRail';
import { runtimeCountImpact,reviewBadgeImpact } from '../src/lib/reviewNavigationImpact';

const ticket:TicketRecord={kind:'ticket',id:'a',value:{title:'A',recap_preview:'',worker_type:'coding',stage:'needs_plan',ticket_status:'empty',blocked:false,created_at:1,updated_at:1,worker_step_claim_out:false,deadline:null,project_id:'p',orchestrator_kind:'sprint_item',sprint_item_id:'parent',conversation_id:'a-conversation',is_backlogged:false,ceiling:'needs_plan',ceiling_holder:{kind:'owner',id:'owner'},gating_field:'plan',proposal_field:null,proposal_revision:0,proposal_created_at:null,stage_ownership:'worker',terminal:false,locale:'test'}};
const records:NavigationRecord[]=[ticket,conversation('a-conversation'),{kind:'orchestrator',id:'parent',value:{title:'Parent',recap_preview:'',created_at:1,project_id:'p',settled:false,conversation_id:null}},{kind:'project',id:'p',value:{name:'Project'}}];
const titleImpact:RecordImpact={kind:'ticket',id:'a',fields:['title','updated_at']};
const fence={...continuity,version:1,coverage:'complete',witnesses:['a'.repeat(64)],signature:'synthetic'};

describe('application presentation and command evidence',()=>{
  it('shares one effective result and parent indexes while knowledge-only loss/read changes invalidate it',()=>{
    const collection=createNavigationCollection(),knowledge=createNavigationKnowledge(),presentationFor=createNavigationPresentationCache();
    try {
      collection.publishSnapshot(currentSnapshot({...continuity,records}),'initial');
      const view=knowledge.accept(collection.getPublication()!);
      const first=presentationFor(view);
      expect(presentationFor(view)).toBe(first);
      expect(first.orchestratorsById.get('parent')?.tickets[0]).toBe(first.ticketsById.get('a'));
      expect(first.ticketsByParent.get('orchestrator:parent')?.[0]).toBe(first.ticketsById.get('a'));
      expect(first.ticketMastheadsById.get('a')?.project).toBe('Project');
      const missingParent={...view,publication:{...view.publication,records:view.publication.records.filter(record=>record.kind!=='orchestrator')}};
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
      expect(orchestratorChildrenImpact(titleImpact,{kind:'sprint_item',id:'parent'},first,first.tickets,[{key:'empty',tickets:first.tickets}],{})).toBe(true);
      expect(orchestratorChildrenImpact(titleImpact,{kind:'sprint_item',id:'other'},first,[],[],{})).toBe(false);
      expect(orchestratorChildrenImpact({kind:'ticket',id:'a',fields:['sprint_item_id']},{kind:'sprint_item',id:'other'},first,[],[],{})).toBe(true);
      expect(workspaceHoverImpact(titleImpact,'ticket','a','a-conversation',first)).toBe(true);
      expect(workspaceHoverImpact(titleImpact,'ticket','other',null,first)).toBe(false);
      expect(orchestratorChildrenImpact(titleImpact,{kind:'sprint_item',id:'parent'},first,[],[],{})).toBe(false);
      expect(orchestratorChildrenImpact(titleImpact,{kind:'sprint_item',id:'parent'},first,first.tickets,[{key:'empty',tickets:first.tickets}],{empty:false})).toBe(false);
      const rail=buildWorkspaceRail(first.tickets,first.orchestrators);
      expect(workspaceRailImpact(titleImpact,first,rail,false)).toBe(true);
      expect(workspaceRailImpact({kind:'conversation',id:'unlinked',fields:['attention']},first,rail,false)).toBe(false);
      expect(workspaceRailImpact({kind:'project',id:'other-project',fields:['name']},first,rail,false)).toBe(false);
      const excluded={...first,ticketsById:new Map([['a',{...first.tickets[0],is_backlogged:true}]])};
      const excludedRail=buildWorkspaceRail([...excluded.ticketsById.values()],first.orchestrators.map(item=>({...item,tickets:[...excluded.ticketsById.values()]})));
      expect(workspaceRailImpact(titleImpact,excluded,excludedRail,false)).toBe(false);
      expect(workspaceRailImpact({kind:'ticket',id:'a',fields:['is_backlogged','updated_at']},excluded,excludedRail,false)).toBe(true);
      // A parent's generated fallback recap counts eligible child rows. Saved
      // recap text and unrelated parents do not depend on that count.
      const backlogImpact:RecordImpact={kind:'ticket',id:'a',fields:['is_backlogged','updated_at']};
      expect(workspaceHoverImpact(backlogImpact,'orchestrator','parent',null,first,true)).toBe(true);
      expect(workspaceHoverImpact(backlogImpact,'orchestrator','parent',null,first,false)).toBe(false);
      expect(workspaceHoverImpact(backlogImpact,'orchestrator','other',null,first,true)).toBe(false);
      const chiefView={...view,publication:{...view.publication,records:records.map(record=>record.kind==='ticket'
        ? {...record,value:{...record.value,orchestrator_kind:'chief' as const,sprint_item_id:null}} : record)}};
      const chiefPresentation=presentationFor(chiefView);
      expect(workspaceHoverImpact(backlogImpact,'chief','chief',null,chiefPresentation,true)).toBe(true);
      expect(workspaceHoverImpact(titleImpact,'chief','chief',null,chiefPresentation,true)).toBe(false);

      // Settled messages retain historical done/blocked Tickets, even when the
      // Ticket has no current conversation pointer and is absent from groups.
      const historicalRecords:NavigationRecord[]=[...records.map(record=>record.kind==='ticket'
        ? {...record,value:{...record.value,stage:'done',terminal:true,blocked:true,conversation_id:null}}
        : record.kind==='orchestrator' ? {...record,value:{...record.value,settled:true}} : record),
        conversation('history'),{kind:'address',id:'historical-address',value:{owner_kind:'ticket',owner_id:'a',
          conversation_id:'history',current:false,parent_kind:'orchestrator',parent_id:'parent'}}];
      collection.publishSnapshot(currentSnapshot({...continuity,revision:'1',records:historicalRecords}),'historical');
      const historical=presentationFor(knowledge.accept(collection.getPublication()!));
      const historicalRail=buildWorkspaceRail(historical.tickets,historical.orchestrators);
      const historyImpact:RecordImpact={kind:'conversation',id:'history',fields:['attention']};
      expect(historicalRail.settledItems[0].groups).toHaveLength(0);
      expect(historical.messagesByParent.get('parent')?.[0].conversationId).toBe('history');
      expect(workspaceRailImpact(titleImpact,historical,historicalRail,true)).toBe(true);
      expect(workspaceRailImpact(historyImpact,historical,historicalRail,true)).toBe(true);
      const differentPointer={...historical,ticketsById:new Map([['a',{...historical.tickets[0],conversation_id:'unshown-current'}]])};
      expect(workspaceRailImpact({kind:'conversation',id:'unshown-current',fields:['attention']},differentPointer,historicalRail,true)).toBe(false);
      expect(workspaceRailImpact(titleImpact,historical,historicalRail,false)).toBe(false);
      expect(workspaceRailImpact(historyImpact,historical,historicalRail,false)).toBe(false);
      collection.publishSnapshot(currentSnapshot({...continuity,revision:'2',records:historicalRecords.map(record=>
        record.kind==='conversation' && record.id==='history' ? conversation('history',0,false) : record)}),'quiet-historical');
      const quiet=presentationFor(knowledge.accept(collection.getPublication()!));
      expect(quiet.messagesByParent.get('parent')).toBeUndefined();
      expect(workspaceRailImpact(historyImpact,quiet,buildWorkspaceRail(quiet.tickets,quiet.orchestrators),true)).toBe(true);
      const movedBacklogged={...quiet.tickets[0],is_backlogged:true,
        orchestrator:{kind:'sprint_item' as const,id:'other'},sprint_item_id:'other'};
      const backlogged={...quiet,tickets:[movedBacklogged],ticketsById:new Map([['a',movedBacklogged]])};
      const backloggedRail=buildWorkspaceRail(backlogged.tickets,quiet.orchestrators.map(item=>({...item,tickets:[]})));
      // The historical alias stays under the former parent. Canonical backlog
      // still excludes it even though the Ticket is absent from that parent's rows.
      expect(workspaceRailImpact(historyImpact,backlogged,backloggedRail,true)).toBe(false);
      const unreadBacklogged={...backlogged,messagesByParent:historical.messagesByParent};
      expect(workspaceRailImpact(titleImpact,unreadBacklogged,backloggedRail,true)).toBe(false);
      expect(workspaceRailImpact(historyImpact,unreadBacklogged,backloggedRail,true)).toBe(false);
      expect(runtimeCountImpact(titleImpact,first)).toBe(false);
      expect(reviewBadgeImpact(titleImpact)).toBe(false);
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

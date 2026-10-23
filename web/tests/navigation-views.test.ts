import {describe,it,expect} from 'vitest';
import {parseFrame,applyFrame} from '../src/lib/navigation/views/validation';
import {createNavigationPresentationCache,navigationReviewPresentation,navigationWorkingAgentCount} from '../src/lib/navigation/presentation';
import {WORK_ITEM_TICKET_GROUPS} from '../src/lib/workItemPresentation';
import {createScopedNavigationOwner,viewWindow,defaultViewRequest} from '../src/lib/navigation/views/owner';
import type {CurrentViewSnapshot,ViewFrame,ViewRequest,Fence} from '../src/lib/navigation/contracts';
import type {ViewTransport} from '../src/lib/navigation/views/transport';
import {authority,continuity,conversation} from './support/navigation/fixtures';

function snapshot(request:ViewRequest,revision='0',name='one'):CurrentViewSnapshot {
  const source=conversation('c');
  return {event:'snapshot',wire_version:3,projection_version:4,view_id:`view-${request.scope_generation}`,
    scope_generation:request.scope_generation,ordering_generation:'fixture',coverage:{...continuity,revision},
    content_version:name,mode:'current',authority:authority(),proof:null,
    content:{records:[{kind:'project',id:'p',value:{name}},{kind:'conversation',id:'c',value:{attention:source.value.attention,owner_read_through_sequence:0,error_acknowledged_through_sequence:0}}],windows:[],counts:{running_conversations:1,out_claims:0,review:0,unread:0,settled:0}}};
}
function fixture() {
  const connections:{request:ViewRequest;send:(frame:ViewFrame)=>void;error:()=>void;signal:AbortSignal;closed:boolean}[]=[];
  const transport:ViewTransport={
    snapshot:async(request)=>snapshot(request),
    connect(request,callbacks,signal){const connection={request,send:callbacks.frame,error:callbacks.error,signal,closed:false};connections.push(connection);return()=>connection.closed=true;},
  };
  const owner=createScopedNavigationOwner({transport,reconnectDelayMs:100000});
  return {owner,connections};
}
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
describe('bounded navigation owner',()=>{
  it('supplies the global working count beyond resident pointers and preserves unknown through runtime loss',async()=>{
    const {owner,connections}=fixture();
    try {
      expect(navigationWorkingAgentCount(null)).toBeNull();
      expect(navigationWorkingAgentCount(undefined)).toBeNull();
      const started=owner.start();const source=connections[0];const initial=snapshot(source.request);
      initial.content.records.push({kind:'chief',id:'chief',value:{label:'Chief',conversation_id:'c'}});
      initial.content.counts.running_conversations=7;
      source.send(initial);await started;
      const count=()=>navigationWorkingAgentCount(owner.getState().effective);
      expect(count()).toBe(7);
      expect(navigationReviewPresentation(owner.getState().effective!)).toMatchObject({runningConversationCount:7,currentConversationIds:new Set(['c'])});
      let revision=0;
      for(const total of [9,0,null,6]) {
        const previous=owner.getState().publication!;
        source.send({event:'patch',wire_version:3,projection_version:4,view_id:initial.view_id,
          scope_generation:initial.scope_generation,ordering_generation:initial.ordering_generation,authority:initial.authority,
          coverage:{...continuity,revision:String(++revision)},previous_coverage:previous.continuity,
          content_version:`count-${revision}`,previous_content_version:previous.view!.contentVersion,
          upserts:[],evictions:[],windows:[],counts:{...initial.content.counts,running_conversations:total}});
        expect(count()).toBe(total);
        expect(navigationReviewPresentation(owner.getState().effective!).runningConversationCount).toBe(total);
        expect(owner.getState().publication!.records).toEqual(initial.content.records);
      }
      source.error();
      expect(count()).toBeNull();
      expect(owner.getState().publication!.view!.counts.running_conversations).toBe(6);
      const recovering=owner.recover();
      expect(count()).toBeNull();
      const replacement=snapshot(connections[1].request,'5','recovered');
      replacement.content.counts.running_conversations=12;
      connections[1].send(replacement);await recovering;
      expect(count()).toBe(12);
    } finally {owner.dispose();}
  });
  it('advances a checkpoint without selector work while requiring new DOM evidence for changed content',async()=>{
    const {owner,connections}=fixture();let notifications=0;const stop=owner.subscribe(()=>notifications++);
    try{
      const started=owner.start();const source=connections[0];const initial=snapshot(source.request);source.send(initial);await started;
      const observer=owner.registerObserver('actual-surface');const first=owner.getState().publication!;
      const token=observer.observeSelectors(first,owner.getState().generation);observer.acknowledgeRender(first,token);
      const fence:Fence={version:1,...continuity,revision:'1',coverage:'complete',witnesses:['a'.repeat(64)],signature:'fixture'};
      const before=notifications;
      const checkpoint={...initial,event:'checkpoint' as const,previous_coverage:initial.coverage,coverage:{...continuity,revision:'1'}};
      source.send(checkpoint);
      expect(notifications).toBe(before);expect(owner.getState().publication!.continuity.revision).toBe('1');
      expect(await owner.waitForVisibility(fence,{requiredObservers:[observer],timeoutMs:100})).toEqual({status:'included',reason:null});
      source.send({...checkpoint,event:'patch',previous_coverage:checkpoint.coverage,coverage:{...continuity,revision:'2'},content_version:'two',previous_content_version:'one',upserts:[{kind:'project',id:'p',value:{name:'two'}}],evictions:[],windows:[],counts:initial.content.counts});
      let resolved=false;const pending=owner.waitForVisibility({...fence,revision:'2'},{requiredObservers:[observer],timeoutMs:100}).then(value=>{resolved=true;return value;});
      await flush();expect(resolved).toBe(false);
      const next=owner.getState().publication!;const observed=observer.observeSelectors(next,owner.getState().generation);observer.acknowledgeRender(next,observed);
      expect((await pending).status).toBe('included');
    }finally{stop();owner.dispose();}
  });
  it('settles cancelled startup and coalesces scope changes; queued obsolete replies cannot repopulate a replaced view',async()=>{
    const {owner,connections}=fixture();
    try{
      const first=owner.start();const cancelled=expect(first).rejects.toThrow('Obsolete');
      owner.setDemand('selected',{selected:{kind:'ticket',id:'a'},windows:[]});
      owner.setDemand('selected',{selected:{kind:'ticket',id:'b'},windows:[]});
      await flush();await cancelled;expect(connections).toHaveLength(2);expect(connections[0].closed).toBe(true);
      connections[1].send(snapshot(connections[1].request,'2','new'));
      connections[0].send(snapshot(connections[0].request,'99','obsolete'));
      expect(owner.getState().publication!.records[0].value).toEqual({name:'new'});
      expect(owner.getState().connection).toBe('subscribed');
    }finally{owner.dispose();}
  });
  it('keeps accepted signals during deliberate scope admission, then requires fresh render evidence after atomic handoff',async()=>{
    const {owner,connections}=fixture();
    try{
      const started=owner.start();connections[0].send(snapshot(connections[0].request,'1'));await started;
      const original=owner.getState();const observer=owner.registerObserver('scope');
      const observation=observer.observeSelectors(original.publication!,original.generation);observer.acknowledgeRender(original.publication!,observation);
      owner.setDemand('selected',{selected:{kind:'ticket',id:'next'},windows:[]});await flush();
      expect(owner.getState()).toBe(original);expect(connections[0].closed).toBe(false);
      expect(owner.getState().effective!.runtimeCurrent).toBe(true);
      expect(owner.getState().effective!.conversations.get('c')!.working).toMatchObject({state:'fresh',value:true});
      connections[1].send(snapshot(connections[1].request,'0'));
      expect(owner.getState()).toBe(original); // Pending source coverage is behind the active view.
      owner.setDemand('selected',{selected:{kind:'ticket',id:'newest'},windows:[]});await flush();
      expect(connections[1].closed).toBe(true);
      connections[1].send(snapshot(connections[1].request,'9','obsolete'));
      expect(owner.getState()).toBe(original);
      connections[2].send(snapshot(connections[2].request,'2','new'));
      expect(connections[0].closed).toBe(true);expect(owner.getState().generation).toBe(original.generation+1);
      expect(owner.getState().effective!.runtimeCurrent).toBe(true);
      expect(owner.getState().effective!.conversations.get('c')!.working).toMatchObject({state:'fresh',value:true});
      let resolved=false;const fence:Fence={version:1,...continuity,revision:'2',coverage:'complete',witnesses:['a'.repeat(64)],signature:'fixture'};
      const pending=owner.waitForVisibility(fence,{requiredObservers:[observer],timeoutMs:100}).then(result=>{resolved=true;return result;});
      await flush();expect(resolved).toBe(false);
      const next=owner.getState();const painted=observer.observeSelectors(next.publication!,next.generation);observer.acknowledgeRender(next.publication!,painted);
      expect((await pending).status).toBe('included');
    }finally{owner.dispose();}
  });
  it('accepts only projection 4 and replaces lean/full records under one key',async()=>{
    const {owner,connections}=fixture();
    const presentationFor=createNavigationPresentationCache();
    try {
      expect(defaultViewRequest().projection_version).toBe(4);
      const started=owner.start();
      const initial=snapshot(connections[0].request);
      const lean={kind:'manager' as const,id:'settled',value:{title:'Settled',created_at:1,updated_at:1,settled:true as const,conversation_id:'c'}};
      initial.content.records.push(lean);
      expect(()=>parseFrame({...initial,projection_version:3})).toThrow();
      expect(()=>applyFrame(undefined,{...initial,projection_version:3} as unknown as ViewFrame)).toThrow('projection version');
      connections[0].send(parseFrame(initial));await started;
      const first=presentationFor(owner.getState().effective!);
      expect(first.settledManagers.map(item=>item.id)).toEqual(['settled']);
      expect(first.rawManagersById.has('settled')).toBe(false);
      expect(first.managersById.has('settled')).toBe(false);
      expect(first.settledManagers[0]).not.toHaveProperty('recap');
      expect(first.settledManagers[0].updated_at).toBe(1);
      const full={...lean,value:{...lean.value,recap:'Retained for selection',recap_preview:'Retained',project_id:'p',recap_reference:null}};
      const patch:ViewFrame={event:'patch',wire_version:3,projection_version:4,view_id:initial.view_id,
        scope_generation:initial.scope_generation,ordering_generation:initial.ordering_generation,authority:initial.authority,
        coverage:{...continuity,revision:'1'},previous_coverage:initial.coverage,content_version:'full',previous_content_version:initial.content_version,
        upserts:[full],evictions:[],windows:[],counts:initial.content.counts};
      connections[0].send(parseFrame(patch));
      const promoted=presentationFor(owner.getState().effective!);
      expect(promoted.rawManagersById.get('settled')?.value.recap).toBe('Retained for selection');
      expect(promoted.managersById.get('settled')?.project).toBe('one');
      expect(promoted.settledManagers).toHaveLength(0);
      connections[0].send(parseFrame({...patch,coverage:{...continuity,revision:'2'},previous_coverage:patch.coverage,
        content_version:'lean-again',previous_content_version:'full',upserts:[lean]}));
      const demoted=presentationFor(owner.getState().effective!);
      expect(owner.getState().publication!.records.filter(row=>row.kind==='manager')).toHaveLength(1);
      expect(demoted.rawManagersById.has('settled')).toBe(false);
      expect(demoted.settledManagers).toHaveLength(1);
    } finally {owner.dispose();}
  });
  it('bounds combined open surfaces without mutating demand or changing Chief page size',()=>{
    const {owner}=fixture();
    try{
      const windows=[viewWindow('settled',null,{limit:60}),...['active','backlogged','done'].map(child_filter=>viewWindow('children',{kind:'chief',id:'chief'},{child_filter:child_filter as 'active'|'backlogged'|'done',limit:30})),...Array.from({length:8},(_,i)=>viewWindow('rail_tickets',{kind:'manager',id:`o${i}`},{limit:60}))];
      const before=JSON.stringify(windows);owner.setDemand('rail',{windows});const request=owner.getRequest();
      expect(request.windows.reduce((total,row)=>total+row.limit,0)).toBeLessThanOrEqual(256);
      expect(request.windows.filter(row=>row.section==='children').every(row=>row.limit===30)).toBe(true);
      expect(JSON.stringify(windows)).toBe(before);expect(defaultViewRequest().windows).toHaveLength(3);
      owner.setDemand('rail',undefined);
      const ordinary={kind:'manager' as const,id:'ordinary'};
      const full=[viewWindow('children',ordinary,{child_filter:'active',limit:0}),...WORK_ITEM_TICKET_GROUPS.filter(g=>g.key!=='awaiting_reply').map(g=>viewWindow('children',ordinary,{child_filter:'active',group:g.key,limit:60})),...(['backlogged','done'] as const).map(child_filter=>viewWindow('children',ordinary,{child_filter,limit:60})),...Array.from({length:60},(_,i)=>viewWindow('rail_tickets',{kind:'manager',id:`parent${i}`},{limit:60})),viewWindow('settled',null,{limit:60}),...Array.from({length:60},(_,i)=>viewWindow('unread',{kind:'manager',id:`settled${i}`},{limit:60}))];
      owner.setDemand('full',{windows:full});
      expect(owner.getRequest().windows).toHaveLength(138);
      expect(owner.getRequest().windows.reduce((n,w)=>n+w.limit,0)).toBeLessThanOrEqual(256);
      expect(owner.getRequest().windows.filter(w=>['rail_parents','settled'].includes(w.section)).map(w=>w.limit)).toEqual([60,60]);
      // Additional Chief count/page scopes exercise pressure beyond the ordinary
      // document. The count-only surface remains present and explicit intent admits it.
      owner.setDemand('chief',{windows:windows.filter(w=>w.section==='children')});
      const withheld=owner.getRequest().windows.find(w=>w.section==='unread'&&w.owner&&w.limit===0)!;
      expect(withheld).toBeDefined();owner.prioritizeWindow(withheld);
      expect(owner.getRequest().windows[0].owner).toEqual(withheld.owner);
      expect(owner.getRequest().windows[0].limit).toBeGreaterThan(0);
      expect(owner.getRequest().windows.reduce((n,w)=>n+w.limit,0)).toBeLessThanOrEqual(256);

    }finally{owner.dispose();}
  });
});

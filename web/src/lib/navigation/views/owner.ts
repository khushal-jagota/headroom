import { VIEW_PROJECTION_VERSION } from "../contracts";
import type {Fence,ViewRequest,ViewSnapshot,ViewFrame,ViewWindowRequest,ViewOwner,CurrentAuthority,DegradedAuthority} from '../contracts';
import type {NavigationPublication} from '../collection';
import type {NavigationOwnerState} from '../owner';
import {createNavigationKnowledge,type DurableFact} from '../knowledge';
import {createVisibilityTracker,type Certification} from '../visibility';
import {compareRevision,sameAuthority} from '../validation';
import {applyFrame,sameDomain} from './validation';
import {createViewTransport,type ViewTransport} from './transport';
import {NavigationRequestError} from '../transport';

export type ViewDemand={selected?:ViewOwner|null;selected_conversation_id?:string|null;windows:readonly ViewWindowRequest[]};
export function viewWindow(section:ViewWindowRequest['section'],owner:ViewOwner|null=null,options:Partial<Omit<ViewWindowRequest,'section'|'owner'>>={}):ViewWindowRequest {
  return {section,owner,group:'all',child_filter:'all',anchor:null,direction:'after',limit:30,...options};
}
export function windowIdentity(window:ViewWindowRequest):string {
  return JSON.stringify([window.section,window.owner,window.group,window.child_filter]);
}
export function defaultViewRequest(locale='en-US'):ViewRequest {
  return {projection_version:VIEW_PROJECTION_VERSION,scope_generation:0,locale,selected:null,selected_conversation_id:null,windows:[
    viewWindow('rail_parents',null,{limit:60}),
    viewWindow('rail_tickets',{kind:'chief',id:'chief'}),
    viewWindow('unread',null,{limit:20}),
  ]};
}

/** One raw immutable bounded view owns source admission. No global replica lives beside it. */
export function createScopedNavigationOwner(options:{transport?:ViewTransport;request?:ViewRequest;reconnectDelayMs?:number}={}) {
  const transport=options.transport ?? createViewTransport();
  const knowledge=createNavigationKnowledge();
  const listeners=new Set<(state:NavigationOwnerState)=>void>();
  const demands=new Map<string,ViewDemand>();
  let request=options.request ?? defaultViewRequest(typeof navigator==='undefined'?'en-US':navigator.language.startsWith('de')?'de-DE':'en-US');
  let activeRequest=request;
  let pendingScope:{controller:AbortController;close:()=>void}|undefined;
  let scopeToken=0;
  let state:NavigationOwnerState={connection:'idle',generation:0,error:null,publication:undefined,effective:undefined};
  let current:ViewSnapshot|undefined;
  let publicationSequence=0;
  let started=false,disposed=false;
  let controller=new AbortController();
  let closeStream:(()=>void)|undefined;
  let reconnect:ReturnType<typeof setTimeout>|undefined;
  let demandScheduled=false;
  let priorityWindow:string|undefined;
  const retiredSessions=new Set<string>();
  let lastAuthority:CurrentAuthority|DegradedAuthority|undefined;
  const visibility=createVisibilityTracker(certify);
  function emit(){for(const listener of listeners)listener(state);}
  function canCertify(){return state.effective?.runtimeCurrent===true && [...state.effective.conversations.values()].every(v=>v.message_for_owner.state==='fresh' && v.last_turn_failed.state==='fresh');}
  function publication(value:ViewSnapshot):NavigationPublication {
    return Object.freeze({publicationId:++publicationSequence,admissionToken:`view:${state.generation}:${publicationSequence}`,continuity:value.coverage,cursor:null,mode:value.mode,authority:value.authority,records:Object.freeze(value.content.records),view:Object.freeze({id:value.view_id,scopeGeneration:value.scope_generation,contentVersion:value.content_version,orderingGeneration:value.ordering_generation,windows:Object.freeze(value.content.windows),counts:Object.freeze(value.content.counts)})});
  }
  function admit(value:ViewFrame,generation:number,scope=activeRequest):NavigationPublication|undefined {
    if(disposed || generation!==state.generation) return;
    if(value.scope_generation!==scope.scope_generation)throw new Error('Obsolete navigation view scope');
    if(value.event==='reset')throw new Error('Navigation view reset');
    if(retiredSessions.has(value.authority.delivery_session_id))throw new Error('Retired navigation authority');
    if(lastAuthority?.delivery_session_id===value.authority.delivery_session_id && compareRevision(value.authority.availability.generation,lastAuthority.availability.generation)<0)throw new Error('Obsolete navigation authority');
    if(value.event==='snapshot' && current && sameDomain(value.coverage,current.coverage) && compareRevision(value.coverage.revision,current.coverage.revision)<0)throw new Error('Obsolete navigation coverage');
    const next=applyFrame(current,value);
    const unchanged=current && next.view_id===current.view_id && next.content_version===current.content_version && sameAuthority(next.authority,current.authority) && state.effective?.runtimeCurrent===true;
    if(lastAuthority && lastAuthority.delivery_session_id!==next.authority.delivery_session_id) {
      if(retiredSessions.size>=64)throw new Error('Navigation authority history full; reload workspace');
      retiredSessions.add(lastAuthority.delivery_session_id);
    }
    current=next;lastAuthority=next.authority;
    const admitted=publication(next);
    if(value.event==='checkpoint' || unchanged) {
      // Coverage changes do not create a new selector/DOM generation or rebuild knowledge.
      if(canCertify())visibility.publication(admitted);
      state={...state,publication:admitted};
      return admitted;
    }
    state={...state,publication:admitted,effective:knowledge.accept(admitted),connection:next.mode==='current'?'subscribed':'degraded',error:null};
    if(canCertify())visibility.publication(admitted);
    emit();
    return admitted;
  }
  function cancelPending(){scopeToken++;pendingScope?.controller.abort();pendingScope?.close();pendingScope=undefined;}
  function lose(connection:NavigationOwnerState['connection']) {
    cancelPending();controller.abort();closeStream?.();closeStream=undefined;clearTimeout(reconnect);
    controller=new AbortController();
    const generation=state.generation+1;
    visibility.loseRuntime();visibility.generation(generation);
    state={...state,generation,connection,effective:knowledge.loseRuntime()};emit();
    return generation;
  }
  function failure(generation:number,error=new NavigationRequestError('Navigation unavailable','unavailable')) {
    if(disposed||generation!==state.generation)return;
    state={...state,error};
    const next=lose('reconnecting');
    reconnect=setTimeout(()=>{if(!disposed&&state.generation===next)void startStream().catch(()=>undefined);},options.reconnectDelayMs ?? 1000);
  }
  function connect(generation:number,onSnapshot?:(value:NavigationPublication)=>void,onError?:(error:Error)=>void) {
    const scope=activeRequest,signal=controller.signal;
    closeStream=transport.connect(scope,{frame(frame){
      if(disposed||signal.aborted||generation!==state.generation)return;
      try{const result=admit(frame,generation,scope);if(frame.event==='snapshot'&&result){onSnapshot?.(result);if(frame.mode==='degraded')failure(generation);}}
      catch(error){onError?.(error instanceof Error?error:new Error('Invalid navigation view'));failure(generation);}
    },error(){if(signal.aborted)return;onError?.(new Error('Navigation stream unavailable'));failure(generation);}},signal);
  }
  async function startStream():Promise<void> {
    if(disposed)return;
    const generation=lose('snapshot');
    activeRequest=request;
    const signal=controller.signal;
    await new Promise<void>((resolve,reject)=>{
      const abort=()=>{signal.removeEventListener('abort',abort);reject(new Error('Obsolete navigation read'));};
      signal.addEventListener('abort',abort,{once:true});
      connect(generation,()=>{signal.removeEventListener('abort',abort);resolve();},error=>{signal.removeEventListener('abort',abort);reject(error);});
    });
  }
  function replaceScope():Promise<void> {
    if(!state.effective?.runtimeCurrent || state.connection!=='subscribed')return startStream();
    cancelPending();clearTimeout(reconnect);
    const token=scopeToken,scope=request,nextController=new AbortController();
    let buffered:ViewSnapshot|undefined;
    let activeGeneration:number|undefined;
    let pendingClose=()=>{};
    return new Promise<void>((resolve,reject)=>{
      const abort=()=>{nextController.signal.removeEventListener('abort',abort);reject(new Error('Obsolete navigation scope'));};
      nextController.signal.addEventListener('abort',abort,{once:true});
      const failed=(error:Error)=>{
        if(activeGeneration!==undefined){reject(error);failure(activeGeneration);return;}
        if(disposed||token!==scopeToken)return;
        cancelPending();
        state={...state,error:new NavigationRequestError(error.message,'unavailable')};emit();
        reconnect=setTimeout(()=>{if(!disposed)void replaceScope().catch(()=>undefined);},options.reconnectDelayMs??1000);
      };
      pendingScope={controller:nextController,close:()=>pendingClose()};
      pendingClose=transport.connect(scope,{frame(frame){
        if(disposed || nextController.signal.aborted)return;
        if(activeGeneration!==undefined){
          try{admit(frame,activeGeneration,scope);}catch(error){failed(error instanceof Error?error:new Error('Invalid navigation view'));}
          return;
        }
        if(token!==scopeToken)return;
        try{
          if(frame.scope_generation!==scope.scope_generation || frame.event==='reset')throw new Error('Obsolete navigation scope');
          buffered=applyFrame(buffered,frame);
          if(buffered.mode!=='current'){failure(state.generation);return;}
          if(retiredSessions.has(buffered.authority.delivery_session_id))throw new Error('Retired navigation authority');
          if(lastAuthority?.delivery_session_id===buffered.authority.delivery_session_id && compareRevision(buffered.authority.availability.generation,lastAuthority.availability.generation)<0)throw new Error('Obsolete navigation authority');
          // Active scope remains authoritative while this bounded pending stream catches up.
          if(current && sameDomain(buffered.coverage,current.coverage) && compareRevision(buffered.coverage.revision,current.coverage.revision)<0)return;
          const sameContent=current?.view_id===buffered.view_id && current.content_version===buffered.content_version && sameAuthority(current.authority,buffered.authority);
          controller.abort();closeStream?.();controller=nextController;closeStream=pendingClose;
          pendingScope=undefined;activeRequest=scope;activeGeneration=state.generation+(sameContent?0:1);
          if(!sameContent)visibility.generation(activeGeneration);
          const hadError=state.error!==null;state={...state,generation:activeGeneration,error:null};
          nextController.signal.removeEventListener('abort',abort);
          admit(buffered,activeGeneration,scope);buffered=undefined;if(sameContent&&hadError)emit();resolve();
        }catch(error){failed(error instanceof Error?error:new Error('Invalid navigation scope'));}
      },error(){failed(new Error('Navigation scope unavailable'));}},nextController.signal);
    });
  }
  async function certify(fence:Fence):Promise<Certification> {
    if(disposed)throw new Error('Navigation owner disposed');
    const generation=lose('snapshot');
    activeRequest=request;
    try {
      const result=await transport.snapshot(request,controller.signal,fence);
      if(disposed||generation!==state.generation)throw new Error('Obsolete navigation certification');
      const admitted=admit(result,generation);
      if(!admitted)throw new Error('Navigation certification not admitted');
      if(result.mode==='current')connect(generation);else failure(generation);
      return {publication:admitted,proof:canCertify()?result.proof:null};
    }catch(error){failure(generation);throw error;}
  }
  function setDemand(name:string,demand:ViewDemand|undefined) {
    if(demand)demands.set(name,demand);else demands.delete(name);
    refreshDemand();
  }
  function refreshDemand() {
    const base=defaultViewRequest(request.locale);
    const windows=new Map(base.windows.map(window=>[windowIdentity(window),window]));
    for(const entry of demands.values()) {
      if(entry.selected!==undefined){base.selected=entry.selected;base.selected_conversation_id=entry.selected_conversation_id??null;}
      for(const window of entry.windows)windows.set(windowIdentity(window),{...window});
    }
    base.windows=[...windows.values()];
    // Parent pages define the visible child surfaces. Shrinking these in response
    // to child demand would repeatedly mount/unmount that same demand. Keep them
    // stable, and preserve Chief's existing pages; allocate other bodies fairly.
    const stable=(window:ViewWindowRequest)=>window.section==='rail_parents'||window.section==='settled'||(window.section==='children'&&window.owner?.kind==='chief');
    const fixed=base.windows.filter(stable).reduce((sum,window)=>sum+window.limit,0);
    const elastic=base.windows.filter(window=>!stable(window)&&window.limit>0);
    const desired=new Map(elastic.map(window=>[window,window.limit]));
    elastic.sort((a,b)=>Number(windowIdentity(b)===priorityWindow)-Number(windowIdentity(a)===priorityWindow));
    for(const window of elastic)window.limit=0;
    let remaining=Math.max(0,256-fixed);
    // One row per nonempty intent before another round; no two-row floor can
    // overrun the global budget. Count-only identities are never discarded.
    for(let position=0;remaining>0&&position<60;position++)for(const window of elastic){
      if(remaining>0&&window.limit<(desired.get(window)??0)){window.limit++;remaining--;}
    }
    if(priorityWindow)base.windows.sort((a,b)=>Number(windowIdentity(b)===priorityWindow)-Number(windowIdentity(a)===priorityWindow));
    // Demand is metadata only. Pages never accumulate when a surface replaces its window.
    if(JSON.stringify({...base,scope_generation:0})===JSON.stringify({...request,scope_generation:0}))return;
    request={...base,scope_generation:request.scope_generation+1};
    if(started&&!demandScheduled){demandScheduled=true;queueMicrotask(()=>{demandScheduled=false;if(!disposed)void replaceScope().catch(()=>undefined);});}
  }
  return {
    start(){if(started||disposed)return Promise.resolve();started=true;return startStream();},
    recover:()=>replaceScope(),setDemand,
    prioritizeWindow(window:ViewWindowRequest|undefined){if(!window)return;const identity=windowIdentity(window);if(priorityWindow!==identity){priorityWindow=identity;refreshDemand();}},
    getRequest:()=>request,
    getState:()=>state,
    subscribe(listener:(state:NavigationOwnerState)=>void){listeners.add(listener);listener(state);return()=>{listeners.delete(listener);};},
    registerObserver:visibility.registerObserver,
    waitForVisibility(value:unknown,options:Parameters<typeof visibility.waitForVisibility>[1]){return canCertify()?visibility.waitForVisibility(value,options):Promise.resolve({status:'unknown' as const,reason:'runtime_unavailable'});},
    invalidateDurableFact(conversationId:string,fact:DurableFact,boundary:number){const effective=knowledge.invalidateDurableFact(conversationId,fact,boundary);if(effective!==state.effective)visibility.loseRuntime();state={...state,effective};emit();},
    dispose(){if(disposed)return;disposed=true;cancelPending();controller.abort();closeStream?.();clearTimeout(reconnect);knowledge.clear();visibility.dispose();state={...state,effective:undefined,publication:undefined,connection:'disposed'};emit();listeners.clear();demands.clear();retiredSessions.clear();current=undefined;},
  };
}

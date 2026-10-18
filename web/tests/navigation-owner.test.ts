import { describe, expect, it } from 'vitest';
import { createTransaction } from '@tanstack/db';
import type { RenderObserver, RenderObservation } from '../src/lib/navigation/visibility';
import { createNavigationCollection } from '../src/lib/navigation/collection';
import { createNavigationOwner } from '../src/lib/navigation/owner';
import { encodeCursor } from '../src/lib/navigation/validation';
import type { Continuity, Fence, SnapshotResponse, RevisionBatch } from '../src/lib/navigation/contracts';
import type { NavigationStreamHandlers, NavigationTransport } from '../src/lib/navigation/transport';
const continuity: Continuity = {scope:'workspace',schema_version:2,semantics:'generic-source-facts-v2',epoch:'e1',revision:'0'};
function snapshot(revision = '0', epoch = 'e1', proof: SnapshotResponse['proof'] = null): SnapshotResponse {
  const value = {...continuity, revision, epoch};
  return {wire_version:1,cursor:encodeCursor(value),snapshot:{...value,records:[{kind:'project',id:'p',value:{name:`name-${revision}`,priority:null}}]},proof};
}
function batch(base: string, revision: string, extra = {}): RevisionBatch {
  return {...continuity,base_revision:base,revision,upserts:[{kind:'project',id:'p',value:{name:`name-${revision}`,priority:null}}],deletes:[],...extra};
}
function fixture() {
  let handlers: NavigationStreamHandlers;
  const cursors: string[] = [];
  const requests: (Fence | undefined)[] = [];
  let response = snapshot();
  const transport: NavigationTransport = {
    async snapshot(_signal, fence) { requests.push(fence); return response; },
    connect(cursor, value) { cursors.push(cursor); handlers = value; return () => undefined; },
  };
  const adapter = createNavigationCollection();
  const owner = createNavigationOwner({adapter,transport,reconnectDelayMs:1,maximumPendingBatches:2});
  return {owner,adapter,cursors,requests,setResponse(value: SnapshotResponse){response=value;},
    send(value: RevisionBatch, cursor=encodeCursor(value)) {handlers.batch(value,cursor);},
    error(){handlers.error();}, dispose(){owner.dispose();adapter.destroy();}};
}
const flush = async () => { for(let i=0;i<24;i++) await Promise.resolve(); };
function observerFor(owner:ReturnType<typeof createNavigationOwner>, id:string) {
  const raw=owner.registerObserver(id);let token:RenderObservation|undefined;
  return {raw, ...raw,observeSelectors(publication:Parameters<RenderObserver['observeSelectors']>[0]) {token=raw.observeSelectors(publication,owner.getState().generation);},
    acknowledgeRender(publication:Parameters<RenderObserver['acknowledgeRender']>[0]) {raw.acknowledgeRender(publication,token);}};
}
function fence(revision = '1', extra = {}): Fence {return {version:1,...continuity,revision,coverage:'complete',witnesses:['a'.repeat(64)],signature:'signed',...extra};}
describe('navigation owner', () => {
  it('publishes in order, waits separately for selectors and render, and ignores duplicate batches', async () => {
    const f = fixture();
    try {
      await f.owner.start();
      const observer = observerFor(f.owner,'list');
      let result: unknown;
      void f.owner.waitForVisibility(fence(),{requiredObservers:[observer.raw]}).then(value=>{result=value;});
      f.send(batch('0','1')); await flush();
      const publication = f.adapter.getPublication()!;
      expect(publication.continuity.revision).toBe('1');
      expect(result).toBeUndefined();
      observer.acknowledgeRender(publication); await flush(); expect(result).toBeUndefined();
      observer.observeSelectors(publication); await flush(); expect(result).toBeUndefined();
      observer.acknowledgeRender(publication); await flush(); expect(result).toEqual({status:'included',reason:null});
      f.send(batch('0','1')); await flush(); expect(f.requests).toHaveLength(1);
    } finally {f.dispose();}
  });
  it('validates epoch and event cursor before suppressing duplicates, and rejects jumps', async () => {
    const f=fixture();
    try {
      await f.owner.start();
      f.send(batch('0','1'),encodeCursor({...continuity,revision:'2'})); await flush();
      expect(f.requests).toHaveLength(2);
      f.send(batch('0','9')); await flush(); expect(f.requests).toHaveLength(3);
      f.send(batch('0','0',{epoch:'other'})); await flush(); expect(f.requests).toHaveLength(4);
      expect(f.adapter.getPublication()!.continuity.revision).toBe('0');
    } finally {f.dispose();}
  });
  it('retains a complete prior publication on recovery and rejects same-epoch rollback', async () => {
    const f=fixture();
    try {
      await f.owner.start(); f.send(batch('0','1')); await flush();
      const prior=f.owner.getState().publication;
      await expect(f.owner.recover()).rejects.toThrow('continuity mismatch');
      expect(f.owner.getState().publication).toBe(prior);
      expect(f.adapter.getPublication()).toBe(prior);
    } finally {f.dispose();}
  });
  it('observer removal and late callbacks never manufacture visibility; disposal releases waits', async () => {
    const f=fixture();
    try {
      await f.owner.start();
      const observer=observerFor(f.owner,'route');
      const wait=f.owner.waitForVisibility(fence('0'),{requiredObservers:[observer.raw]});
      const pub=f.adapter.getPublication()!;
      observer.observeSelectors(pub);observer.dispose();observer.acknowledgeRender(pub);
      expect(await wait).toEqual({status:'unknown',reason:'observer_removed'});
      const second=observerFor(f.owner,'route');
      const pending=f.owner.waitForVisibility(fence('4'),{requiredObservers:[second.raw]});
      f.owner.dispose();expect(await pending).toEqual({status:'unknown',reason:'disposed'});
    } finally {f.dispose();}
  });
  it('certifies exact cross-epoch fence and preserves no_op without asserting a relevant change', async () => {
    const f=fixture();
    try {
      await f.owner.start();const observer=observerFor(f.owner,'route');
      f.setResponse(snapshot('0','e2',{status:'no_op',reason:null}));
      const issued=fence('0',{epoch:'e0',witnesses:[]});
      const wait=f.owner.waitForVisibility(issued,{requiredObservers:[observer.raw]});
      await flush();expect(f.requests[1]).toEqual(issued);
      const pub=f.adapter.getPublication()!;observer.observeSelectors(pub);observer.acknowledgeRender(pub);
      expect(await wait).toEqual({status:'no_op',reason:null});
    } finally {f.dispose();}
  });
  it('returns unknown for incomplete capture and expired proof', async () => {
    const f=fixture();
    try {
      await f.owner.start();const observer=observerFor(f.owner,'route');
      expect(await f.owner.waitForVisibility(fence('0',{coverage:'unknown'}),{requiredObservers:[observer.raw]})).toEqual({status:'unknown',reason:'incomplete_capture'});
      f.setResponse(snapshot('0','e2',{status:'unknown',reason:'missing_witness'}));
      expect(await f.owner.waitForVisibility(fence('0',{epoch:'old'}),{requiredObservers:[observer.raw]})).toEqual({status:'unknown',reason:'missing_witness'});
    } finally {f.dispose();}
  });
});
describe('bounded delayed publication',()=>{
  it('reconnects from published cursor and aborts delayed obsolete admission',async()=>{
    const base=createNavigationCollection();
    let delayed:(()=>void)|undefined;
    let handlers:NavigationStreamHandlers;
    const cursors:string[]=[];
    const adapter={...base,publishBatch(value:RevisionBatch,cursor:string,signal?:AbortSignal){
      delayed=()=>base.publishBatch(value,cursor,signal);
    }};
    const transport:NavigationTransport={async snapshot(){return snapshot();},connect(cursor,value){cursors.push(cursor);handlers=value;return()=>undefined;}};
    const owner=createNavigationOwner({adapter,transport,reconnectDelayMs:1});
    try {
      await owner.start();
      handlers!.batch(batch('0','1'),encodeCursor(batch('0','1')));await flush();
      expect(base.getPublication()!.continuity.revision).toBe('0');
      handlers!.error();await new Promise(resolve=>setTimeout(resolve,5));await flush();
      expect(cursors).toEqual([snapshot().cursor,snapshot().cursor]);
      expect(()=>delayed!()).toThrow('aborted');
      expect(base.getPublication()!.continuity.revision).toBe('0');
    }finally{owner.dispose();base.destroy();}
  });
  it('bounds admitted frames, retains prior view, and replaces after overflow',async()=>{
    const base=createNavigationCollection();
    let handlers:NavigationStreamHandlers;
    let responses=0;
    let delayed:(()=>void)|undefined;
    const adapter={...base,publishBatch(value:RevisionBatch,cursor:string,signal?:AbortSignal){delayed=()=>base.publishBatch(value,cursor,signal);}};
    const transport:NavigationTransport={async snapshot(){responses++;return snapshot(responses===1?'0':'8');},connect(_cursor,value){handlers=value;return()=>undefined;}};
    const owner=createNavigationOwner({adapter,transport,maximumPendingBatches:2});
    try {
      await owner.start();const prior=owner.getState().publication;
      handlers!.batch(batch('0','1'),encodeCursor(batch('0','1')));await flush();
      handlers!.batch(batch('1','2'),encodeCursor(batch('1','2')));
      handlers!.batch(batch('2','3'),encodeCursor(batch('2','3')));
      expect(owner.getState().publication).toBe(prior);
      await flush();expect(base.getPublication()!.continuity.revision).toBe('8');
      expect(responses).toBe(2);expect(()=>delayed!()).toThrow('aborted');
    }finally{owner.dispose();base.destroy();}
  });
  it('certifies a wait that outlives an epoch transition and rejects stale render callbacks',async()=>{
    const f=fixture();
    try{
      await f.owner.start();const observer=observerFor(f.owner,'route');
      const old=f.adapter.getPublication()!;observer.observeSelectors(old);
      const pending=f.owner.waitForVisibility(fence('2'),{requiredObservers:[observer.raw]});
      f.setResponse(snapshot('0','e2',{status:'included',reason:null}));
      await f.owner.recover();await flush();
      observer.observeSelectors(old);observer.acknowledgeRender(old);
      let result:unknown;void pending.then(value=>{result=value;});await flush();expect(result).toBeUndefined();
      expect(f.requests.some(value=>value?.revision==='2')).toBe(true);
      const pub=f.adapter.getPublication()!;observer.observeSelectors(pub);observer.acknowledgeRender(pub);
      expect(await pending).toEqual({status:'included',reason:null});
    }finally{f.dispose();}
  });
});
describe('real accepted provider transaction',()=>{
  it('drains accepted source publication before generation rotation and resumes its observed cursor',async()=>{
    const f=fixture();
    try{
      await f.owner.start();
      let release!:()=>void;
      const held=new Promise<void>(resolve=>{release=resolve;});
      const transaction=createTransaction({mutationFn:async()=>held});
      transaction.mutate(()=>f.adapter.collection.update('["project","p"]',draft=>{
        if(draft.type==='record'&&draft.record.kind==='project')draft.record.value.name='optimistic';
      }));
      await flush();
      f.send(batch('0','1'));await flush();
      expect(f.adapter.isPublicationPending(encodeCursor(batch('0','1')))).toBe(true);
      expect(f.adapter.getPublication()!.continuity.revision).toBe('0');
      const oldGeneration=f.owner.getState().generation;
      f.error();await flush();
      expect(f.owner.getState().generation).toBe(oldGeneration);
      expect(f.cursors).toHaveLength(1);
      release();await transaction.when('settled');await flush();
      await new Promise(resolve=>setTimeout(resolve,5));await flush();
      expect(f.adapter.getPublication()!.continuity.revision).toBe('1');
      expect(f.owner.getState().publication).toBe(f.adapter.getPublication());
      expect(f.owner.getState().generation).toBe(oldGeneration+1);
      expect(f.cursors).toEqual([snapshot().cursor,encodeCursor(batch('0','1'))]);
    }finally{f.dispose();}
  });
  it('requires new generation-bound observation after unchanged reconnect',async()=>{
    const f=fixture();
    try{
      await f.owner.start();const observer=f.owner.registerObserver('route');
      const pub=f.adapter.getPublication()!;
      const oldToken=observer.observeSelectors(pub,f.owner.getState().generation);
      f.error();await new Promise(resolve=>setTimeout(resolve,5));await flush();
      const wait=f.owner.waitForVisibility(fence('0'),{requiredObservers:[observer]});
      observer.acknowledgeRender(pub,oldToken);
      let result:unknown;void wait.then(value=>{result=value;});await flush();expect(result).toBeUndefined();
      const token=observer.observeSelectors(pub,f.owner.getState().generation);
      observer.acknowledgeRender(pub,token);
      expect(await wait).toEqual({status:'included',reason:null});
    }finally{f.dispose();}
  });
});

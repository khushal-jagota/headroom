import { describe, expect, it } from 'vitest';
import { createTransaction } from '@tanstack/db';
import { createNavigationCollection } from '../src/lib/navigation/collection';
import { createNavigationOwner } from '../src/lib/navigation/owner';
import { encodeCursor } from '../src/lib/navigation/identity';
import type { Continuity, SnapshotResponse, RevisionBatch, BatchEnvelope, CurrentSnapshotResponse } from '../src/lib/navigation/contracts';
import { NavigationRequestError, type NavigationStreamHandlers, type NavigationTransport } from '../src/lib/navigation/transport';
import { continuity, currentSnapshot, degradedSnapshot, envelope, authority, conversation } from './support/navigation/fixtures';
function snapshot(revision = '0', epoch = 'e1'): CurrentSnapshotResponse {
  const value = {...continuity, revision, epoch};
  return {...currentSnapshot({...value,records:[{kind:'project',id:'p',value:{name:`name-${revision}`}}]})};
}
function batch(base: string, revision: string, extra = {}): RevisionBatch {
  return {...continuity,base_revision:base,revision,upserts:[{kind:'project',id:'p',value:{name:`name-${revision}`}}],deletes:[],...extra};
}
function fixture() {
  let handlers: NavigationStreamHandlers;
  const cursors: string[] = [];
  const requests: undefined[] = [];
  let response: SnapshotResponse = snapshot();
  const transport: NavigationTransport = {
    async snapshot() { requests.push(undefined); return response; },
    connect(cursor, value) { cursors.push(cursor); handlers = value; return () => undefined; },
  };
  const adapter = createNavigationCollection();
  const owner = createNavigationOwner({adapter,transport,reconnectDelayMs:1,maximumPendingBatches:2});
  return {owner,adapter,cursors,requests,setResponse(value: SnapshotResponse){response=value;},
    send(value: RevisionBatch, cursor=encodeCursor(value)) {handlers.batch(envelope(value),cursor);},
    error(){handlers.error();}, dispose(){owner.dispose();adapter.destroy();}};
}
const flush = async () => { for(let i=0;i<24;i++) await Promise.resolve(); };

describe('navigation owner', () => {
  it('retains request failure until an accepted snapshot and ignores obsolete aborted failures',async()=>{
    const adapter=createNavigationCollection();
    let read:NavigationTransport['snapshot']=async()=>{throw new NavigationRequestError('offline','network');};
    const owner=createNavigationOwner({adapter,transport:{snapshot:(signal)=>read(signal),connect:()=>()=>undefined},reconnectDelayMs:100000});
    try {
      await expect(owner.start()).rejects.toThrow('offline');
      expect(owner.getState().error?.code).toBe('network');
      read=async()=>{throw new Error('invalid protocol');};
      await expect(owner.recover()).rejects.toThrow('invalid protocol');
      expect(owner.getState().error?.code).toBe('network');
      read=async()=>snapshot();await owner.recover();
      expect(owner.getState().error).toBeNull();
      let rejectOld!:(error:Error)=>void;let oldSignal!:AbortSignal;
      read=(signal)=>{oldSignal=signal;return new Promise((_resolve,reject)=>{rejectOld=reject;});};
      const old=owner.recover();const oldFailure=expect(old).rejects.toThrow('late failure');await flush();
      read=async()=>snapshot();await owner.recover();
      expect(oldSignal.aborted).toBe(true);
      rejectOld(new NavigationRequestError('late failure','network'));await oldFailure;
      expect(owner.getState().error).toBeNull();
      expect(owner.getState().connection).toBe('subscribed');
    } finally {owner.dispose();adapter.destroy();}
  });
  it('publishes in order and ignores duplicate batches', async () => {
    const f = fixture();
    try {
      await f.owner.start();
      f.send(batch('0','1')); await flush();
      const publication = f.adapter.getPublication()!;
      expect(publication.continuity.revision).toBe('1');
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
  ;
  ;
  ;
});
describe('bounded delayed publication',()=>{
  it('reconnects from published cursor and aborts delayed obsolete admission',async()=>{
    const base=createNavigationCollection();
    let delayed:(()=>void)|undefined;
    let handlers:NavigationStreamHandlers;
    const cursors:string[]=[];
    const adapter={...base,publishBatch(value:BatchEnvelope,cursor:string,token:string,signal?:AbortSignal){
      delayed=()=>base.publishBatch(value,cursor,token,signal);
    }};
    const transport:NavigationTransport={async snapshot(){return snapshot();},connect(cursor,value){cursors.push(cursor);handlers=value;return()=>undefined;}};
    const owner=createNavigationOwner({adapter,transport,reconnectDelayMs:1});
    try {
      await owner.start();
      handlers!.batch(envelope(batch('0','1')),encodeCursor(batch('0','1')));await flush();
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
    const adapter={...base,publishBatch(value:BatchEnvelope,cursor:string,token:string,signal?:AbortSignal){delayed=()=>base.publishBatch(value,cursor,token,signal);}};
    const transport:NavigationTransport={async snapshot(){responses++;return snapshot(responses===1?'0':'8');},connect(_cursor,value){handlers=value;return()=>undefined;}};
    const owner=createNavigationOwner({adapter,transport,maximumPendingBatches:2});
    try {
      await owner.start();const prior=owner.getState().publication;
      handlers!.batch(envelope(batch('0','1')),encodeCursor(batch('0','1')));await flush();
      handlers!.batch(envelope(batch('1','2')),encodeCursor(batch('1','2')));
      handlers!.batch(envelope(batch('2','3')),encodeCursor(batch('2','3')));
      expect(owner.getState().publication).toBe(prior);
      await flush();expect(base.getPublication()!.continuity.revision).toBe('8');
      expect(responses).toBe(2);expect(()=>delayed!()).toThrow('aborted');
    }finally{owner.dispose();base.destroy();}
  });
  ;
});
describe('real accepted provider transaction',()=>{
  it('suppresses runtime before accepted source drain and restores authority only from a complete snapshot',async()=>{
    const f=fixture();
    f.setResponse({...snapshot(),snapshot:{...snapshot().snapshot,records:[...snapshot().snapshot.records,conversation()]}});
    try{
      await f.owner.start();
      expect(f.owner.getState().effective?.conversations.get('c')?.working).toMatchObject({state:'fresh',value:true});
      let release!:()=>void;
      const held=new Promise<void>(resolve=>{release=resolve;});
      const transaction=createTransaction({mutationFn:async()=>held});
      transaction.mutate(()=>f.adapter.collection.update('["project","p"]',draft=>{
        if(draft.type==='record'&&draft.record.kind==='project')draft.record.value.name='optimistic';
      }));
      await flush();
      f.send(batch('0','1'));await flush();
      expect(f.adapter.getPublication()!.continuity.revision).toBe('0');
      const oldGeneration=f.owner.getState().generation;
      const lateCommitKnowledge: unknown[]=[];
      const stop=f.adapter.subscribe(publication=>{if(publication.continuity.revision==='1') lateCommitKnowledge.push(f.owner.getState().effective?.conversations.get('c')?.working);});
      f.setResponse(snapshot('1'));
      f.error();
      expect(f.owner.getState().effective?.runtimeCurrent).toBe(false);
      expect(f.owner.getState().effective?.conversations.get('c')?.message_for_owner).toMatchObject({state:'retained',value:true});
      await flush();
      expect(f.owner.getState().generation).toBe(oldGeneration+1);
      expect(f.cursors).toHaveLength(1);
      release();await transaction.when('settled');await flush();
      await new Promise(resolve=>setTimeout(resolve,5));await flush();
      expect(f.adapter.getPublication()!.continuity.revision).toBe('1');
      expect(f.owner.getState().publication).toBe(f.adapter.getPublication());
      expect(f.owner.getState().generation).toBe(oldGeneration+1);
      expect(f.cursors).toEqual([snapshot().cursor,encodeCursor(batch('0','1'))]);
      expect(lateCommitKnowledge[0]).toEqual({state:'unknown'});
      stop();
    }finally{f.dispose();}
  });
  ;
});

describe('attention availability and independent durable knowledge', () => {
  it('replaces same-cursor current/degraded views atomically and retains the message when runtime authority is lost',async()=>{
    const f=fixture();
    const full={...snapshot(),snapshot:{...snapshot().snapshot,records:[conversation()]}};
    f.setResponse(full);
    try {
      await f.owner.start();
      const first=f.owner.getState().publication!;
      expect(f.owner.getState().effective?.conversations.get('c')?.working).toMatchObject({state:'fresh',value:true});
      const recovery=f.owner.recover();
      expect(f.owner.getState().effective?.runtimeCurrent).toBe(false);
      expect(f.owner.getState().effective?.conversations.get('c')?.working).toEqual({state:'unknown'});
      expect(f.owner.getState().effective?.conversations.get('c')?.message_for_owner).toMatchObject({state:'retained',value:true,provenance:{conversationId:'c',publicationId:first.publicationId}});
      await recovery;
      f.setResponse(degradedSnapshot(full.snapshot));
      await f.owner.recover();
      const degraded=f.owner.getState().publication!;
      expect(degraded.cursor).toBeNull();
      expect(degraded.continuity).toEqual(first.continuity);
      expect(degraded.publicationId).toBeGreaterThan(first.publicationId);
      expect(f.owner.getState().effective?.conversations.get('c')?.message_for_owner).toMatchObject({state:'fresh',value:true});
      expect(f.owner.getState().effective?.conversations.get('c')?.working).toEqual({state:'unknown'});
      expect(f.cursors).toHaveLength(2);
      f.setResponse(full);
      await f.owner.recover();
      expect(f.owner.getState().publication?.cursor).toBe(first.cursor);
      expect(f.owner.getState().publication?.admissionToken).not.toBe(first.admissionToken);
      expect(f.owner.getState().effective?.runtimeCurrent).toBe(true);
      expect(f.cursors).toHaveLength(3);
    } finally {f.dispose();}
  });
  it('invalidates only the confirmed fact until its canonical watermark covers the read, preserving a newer message after a late read response',async()=>{
    const f=fixture();
    const full=(read:number,message=true)=>({...snapshot(),snapshot:{...snapshot().snapshot,records:[conversation('c',read,message)]}});
    f.setResponse(full(0));
    try {
      await f.owner.start();
      f.owner.invalidateDurableFact('c','message_for_owner',10);
      expect(f.owner.getState().effective?.conversations.get('c')?.message_for_owner).toEqual({state:'unknown'});
      expect(f.owner.getState().effective?.conversations.get('c')?.last_turn_failed).toMatchObject({state:'fresh',value:true});
      // latest_sequence is 20, but only the owner-read watermark covers confirmation.
      f.setResponse(full(9));await f.owner.recover();
      expect(f.owner.getState().effective?.conversations.get('c')?.message_for_owner).toEqual({state:'unknown'});
      f.setResponse(full(10));await f.owner.recover();
      const newer=f.owner.getState().effective!.conversations.get('c')!.message_for_owner;
      expect(newer).toMatchObject({state:'fresh',value:true,provenance:{boundary:10}});
      f.owner.invalidateDurableFact('c','message_for_owner',8);
      expect(f.owner.getState().effective?.conversations.get('c')?.message_for_owner).toBe(newer);
      f.owner.invalidateDurableFact('c','last_turn_failed',3);
      expect(f.owner.getState().effective?.conversations.get('c')?.last_turn_failed).toEqual({state:'unknown'});
      expect(f.owner.getState().effective?.conversations.get('c')?.message_for_owner).toBe(newer);
      f.setResponse(snapshot());await f.owner.recover();
      expect(f.owner.getState().effective?.conversations.has('c')).toBe(false);
    } finally {f.dispose();}
  });
  it('rejects changed full authority before duplicate handling, including unchanged generation with changed state',async()=>{
    const base=createNavigationCollection();
    let handlers!:NavigationStreamHandlers;
    let calls=0;
    const transport:NavigationTransport={async snapshot(){calls++;return snapshot();},connect(_cursor,value){handlers=value;return()=>undefined;}};
    const owner=createNavigationOwner({adapter:base,transport});
    try {
      await owner.start();
      const duplicate=envelope(batch('0','1'));
      duplicate.authority={...authority(),permit:{...authority().permit,scope:'changed-signal-scope'}};
      handlers.batch(duplicate,encodeCursor(duplicate.batch));
      expect(owner.getState().effective?.runtimeCurrent).toBe(false);
      await flush();expect(calls).toBe(2);
      const invalid={...envelope(batch('0','1')),authority:{delivery_session_id:'delivery',availability:{state:'unavailable',generation:'1',reason:'lost'},permit:null}};
      handlers.batch(invalid as unknown as import('../src/lib/navigation/contracts').BatchEnvelope,encodeCursor(batch('0','1')));
      expect(owner.getState().effective?.runtimeCurrent).toBe(false);
      await flush();expect(calls).toBe(3);
    } finally {owner.dispose();base.destroy();}
  });
  it('discards obsolete snapshot replies and retired sessions without comparing different sessions numerically',async()=>{
    const base=createNavigationCollection();
    let release!:(response:SnapshotResponse)=>void;
    let next:SnapshotResponse=snapshot();
    let held=false;
    const transport:NavigationTransport={snapshot(){return held ? new Promise(resolve=>{release=resolve;}) : Promise.resolve(next);},connect(){return()=>undefined;}};
    const owner=createNavigationOwner({adapter:base,transport,reconnectDelayMs:10000});
    try {
      await owner.start();
      held=true;
      const obsolete=owner.recover();await flush();
      held=false;
      next={...snapshot(),authority:authority('workspace','0','replacement')};
      await owner.recover();
      const accepted=owner.getState().publication;
      release(degradedSnapshot(snapshot().snapshot,'99','delivery'));
      await expect(obsolete).rejects.toThrow('obsolete');
      expect(owner.getState().publication).toBe(accepted);
      next=snapshot();
      await expect(owner.recover()).rejects.toThrow('obsolete delivery session');
      expect(owner.getState().effective?.runtimeCurrent).toBe(false);
    } finally {owner.dispose();base.destroy();}
  });
});

import type { NavigationRecord } from './contracts';
import type { NavigationOwnerState } from './owner';
import { parseFence } from './validation';
import type { RenderObserver, VisibilityResult } from './visibility';

/** Local command footprint; keys come from the generated canonical record values. */
export type RecordImpact = {
  [Kind in NavigationRecord['kind']]: {
    readonly kind: Kind;
    readonly id: string;
    readonly fields: '*' | readonly (keyof Extract<NavigationRecord,{kind:Kind}>['value'])[];
  }
}[NavigationRecord['kind']];
export function recordImpactTouches(impact:RecordImpact,fields:readonly string[]):boolean {
  return impact.fields==='*' || impact.fields.some(field=>fields.includes(field));
}
export type CommandImpact = {readonly complete:true;readonly records:readonly RecordImpact[]}
  | {readonly complete:false;readonly reason:string};
export type CommandEvidence = Readonly<{
  id:number;
  label:string;
  capturedGeneration:number | null;
  capturedPublicationId:number | null;
  fence:Readonly<{epoch:string;revision:string;coverage:string;witnessCount:number}> | null;
  status:'pending' | VisibilityResult['status'];
  reason:string | null;
  observers:readonly string[];
  impact:CommandImpact;
  startedAt:number;
  domainCompletedAt:number | null;
  settledAt:number | null;
  diagnosticError:string | null;
}>;
export type ResponseMetadata = {headers?:Headers};
type EvidenceOwner = {
  getState?():NavigationOwnerState;
  registerObserver(name:string):RenderObserver;
  waitForVisibility(fence:unknown,options:{requiredObservers:readonly RenderObserver[]}):Promise<VisibilityResult>;
};

/** Domain results and bounded render evidence have independent lifetimes. */
export function createCommandEvidence(owner:EvidenceOwner,maximum:number) {
  const boundaries=new Map<RenderObserver,(impact:RecordImpact)=>boolean>();
  const listeners=new Set<(evidence:readonly CommandEvidence[])=>void>();
  let evidence:readonly CommandEvidence[]=Object.freeze([]);
  let sequence=0;
  function update(entry:CommandEvidence,initial=false) {
    // An evicted pending operation cannot displace more recent evidence when it ends.
    const previous=evidence.find(value=>value.id===entry.id);
    if(!initial && !previous) return;
    if(previous?.diagnosticError && !entry.diagnosticError) entry={...entry,diagnosticError:previous.diagnosticError};
    evidence=Object.freeze([...evidence.filter(value=>value.id!==entry.id),Object.freeze(entry)]
      .sort((left,right)=>left.id-right.id).slice(-maximum));
    let failed=false;
    for(const listener of listeners) {try{listener(evidence);}catch{failed=true;}}
    if(failed) evidence=Object.freeze(evidence.map(value=>value.id===entry.id ? Object.freeze({...value,diagnosticError:'subscriber_failed'}) : value));
  }
  return {
    get evidence(){return evidence;},
    subscribe(listener:(evidence:readonly CommandEvidence[])=>void){listeners.add(listener);try{listener(evidence);}catch{/* No invocation exists yet; failures during updates are attached to that evidence. */}return()=>{listeners.delete(listener);};},
    register(name:string,matchesImpact:(impact:RecordImpact)=>boolean) {
      const observer=owner.registerObserver(name);
      boundaries.set(observer,matchesImpact);
      return observer;
    },
    dispose(observer:RenderObserver) {
      boundaries.delete(observer);
      observer.dispose();
    },
    capture:()=>Object.freeze([...boundaries.keys()]),
    async runCommand<T>(impact:CommandImpact,request:(metadata:ResponseMetadata)=>Promise<T>,label='navigation command'):Promise<T>{
      const captured=owner.getState?.();
      const capturedGeneration=captured?.generation ?? null;
      const capturedPublicationId=captured?.effective?.publication.publicationId ?? captured?.publication?.publicationId ?? null;
      let fenceMetadata:CommandEvidence['fence']=null;
      let uncertain=impact.complete ? null : impact.reason;
      let required:readonly RenderObserver[]=[];
      if(impact.complete) {
        try {
          required=Object.freeze([...boundaries]
            .filter(([,matches])=>impact.records.some(matches))
            .map(([observer])=>observer));
        } catch {
          uncertain='impact_match_failed';
        }
      }
      const id=++sequence;
      const observers=Object.freeze(required.map(observer=>observer.id));
      const retainedImpact=structuredClone(impact);
      if(retainedImpact.complete) {
        for(const record of retainedImpact.records) {if(record.fields!=='*') Object.freeze(record.fields);Object.freeze(record);}
        Object.freeze(retainedImpact.records);
      }
      Object.freeze(retainedImpact);
      const startedAt=performance.now();
      let domainCompletedAt:number | null=null;
      const entry=(status:CommandEvidence['status'],reason:string | null):CommandEvidence=>({id,label,status,reason,observers,impact:retainedImpact,capturedGeneration,capturedPublicationId,fence:fenceMetadata,
        startedAt,domainCompletedAt,settledAt:status==='pending' ? null : performance.now(),diagnosticError:null});
      update(entry('pending',null),true);
      const metadata:ResponseMetadata={};
      let result:T;
      try {
        result=await request(metadata);
      } catch(error) {
        domainCompletedAt=performance.now();
        update(entry('unknown','command_failed'));
        throw error;
      }
      domainCompletedAt=performance.now();
      update(entry('pending',null));
      let fence:ReturnType<typeof parseFence> | null;
      try {
        fence=parseFence(JSON.parse(metadata.headers?.get('Panels-Navigation-Fence') ?? 'null'));
        fenceMetadata=Object.freeze({
          epoch:fence.epoch,
          revision:fence.revision,
          coverage:fence.coverage,
          witnessCount:fence.witnesses.length,
        });
        update(entry('pending',null));
      } catch {
        fence=null;
      }
      if(uncertain!==null || required.length===0){
        update(entry('unknown',uncertain ?? 'no_relevant_observer'));
        return result;
      }
      try {
        void owner.waitForVisibility(fence,{requiredObservers:required}).then(
          value=>update(entry(value.status,value.reason)),
          ()=>update(entry('unknown','visibility_failed')));
      } catch {
        update(entry('unknown','visibility_failed'));
      }
      return result;
    },
    clear() {
      boundaries.clear();
      listeners.clear();
    },
  };
}

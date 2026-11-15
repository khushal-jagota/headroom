import type {ViewContentReference} from '../contracts';
/** One selected body, aborted and released with its owning surface; never an archive cache. */
export function selectedRecap(reference:()=>ViewContentReference|null|undefined,enabled:()=>boolean) {
  let body=$state<string|undefined>();
  let version=$state<string|undefined>();
  let error=$state<Error|null>(null);
  $effect(()=>{
    const ref=reference();const active=enabled();body=undefined;version=undefined;error=null;
    if(!active||!ref)return;
    const controller=new AbortController();
    const query=new URLSearchParams({kind:ref.owner.kind,identity:ref.owner.id,version:ref.version});
    void fetch(`/api/navigation/view/content?${query}`,{signal:controller.signal,cache:'no-store'}).then(async response=>{
      if(!response.ok)throw new Error('Recap changed while it was read');
      const value=await response.text();
      if(!controller.signal.aborted){body=value;version=ref.version;}
    }).catch(reason=>{if(!controller.signal.aborted)error=reason instanceof Error?reason:new Error('Recap unavailable');});
    return()=>controller.abort();
  });
  return {get body(){return version===reference()?.version?body:undefined;},get error(){return error;}};
}

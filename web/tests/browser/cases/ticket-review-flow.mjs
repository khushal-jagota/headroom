import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const stem = `ticket-review-${process.pid}`;
const host = join(scratchRoot, `${stem}.svelte`);
const main = join(scratchRoot, `${stem}.ts`);
const index = join(scratchRoot, `${stem}.html`);
  await writeFile(host, `<script lang="ts">
import { QueryClientProvider } from '@tanstack/svelte-query';
import { queryClient as client } from '../src/lib/queryClient';
import TicketRoute from '../src/routes/TicketRoute.svelte';
import ReviewRoute from '../src/routes/ReviewRoute.svelte';
import ReviewProposalCard from '../src/components/ReviewProposalCard.svelte';
import { navigationReviewPresentation } from '../src/lib/navigation/presentation';
import { reviewBadgeDomMatches } from '../src/lib/shellDom';
(window as any).__badgeProof=()=>{
 const link=document.createElement('a');const badge=document.createElement('span');link.append(badge);
 badge.className='nav-badge hidden';
 const emptyHidden=reviewBadgeDomMatches(link,0);
 badge.classList.remove('hidden');const emptyVisible=reviewBadgeDomMatches(link,0);
 badge.classList.add('hidden');badge.textContent='2';const nonemptyHidden=reviewBadgeDomMatches(link,0);
 badge.classList.remove('hidden');const positiveVisible=reviewBadgeDomMatches(link,2);
 const wrongCount=reviewBadgeDomMatches(link,1);
 badge.classList.add('hidden');const positiveHidden=reviewBadgeDomMatches(link,2);
 badge.remove();const absent=reviewBadgeDomMatches(link,0);
 return {emptyHidden,emptyVisible,nonemptyHidden,positiveVisible,wrongCount,positiveHidden,absent};
};
import ShellStatus from '../src/components/ShellStatus.svelte';
import { tick } from 'svelte';
import { provideNavigationApplication } from '../src/lib/navigation/application.svelte';
const navigation=provideNavigationApplication();
(window as any).__navigation=navigation;
const wait=navigation.owner.waitForVisibility;
navigation.owner.waitForVisibility=(fence,options)=>{
 (window as any).__commandWait='pending';
 (window as any).__commandObservers=options.requiredObservers.slice();
 return wait(fence,options).then(result=>{(window as any).__commandWait=result.status;return result;});
};
(window as any).__refresh=()=>navigation.owner.recover().catch(()=>undefined);
(window as any).__batch=async()=>{
 const ready=async()=>{
  if(navigation.owner.getState().connection==='subscribed')return;
  await new Promise<void>((resolve,reject)=>{
   let stop=()=>{};
   const timer=setTimeout(()=>{stop();reject(new Error('fixture stream not ready: '+JSON.stringify({connection:navigation.owner.getState().connection,continuity:navigation.owner.getState().publication?.continuity})));},5000);
   stop=navigation.owner.subscribe(state=>{if(state.connection==='subscribed'){clearTimeout(timer);stop();resolve();}});
  });
 };
 for(let attempt=0;attempt<3;attempt++){
  await ready();
  const generation=navigation.owner.getState().generation;
  const response=await (await fetch('/api/test/navigation-publication',{method:'POST'})).json();
  const current=navigation.owner.getState();
  if(current.generation!==generation || current.connection!=='subscribed')continue;
  const previous=current.publication!;
  if(response.snapshot.revision===previous.continuity.revision){await tick();return;}
  if(BigInt(response.snapshot.revision)!==BigInt(previous.continuity.revision)+1n)throw new Error('controlled review source revision gap');
  const keys=new Set(response.snapshot.records.map((row:any)=>JSON.stringify([row.kind,row.id])));
  const batch={...response.snapshot,base_revision:previous.continuity.revision,upserts:response.snapshot.records,
  deletes:previous.records.filter(row=>!keys.has(JSON.stringify([row.kind,row.id]))).map(row=>({kind:row.kind,id:row.id}))};delete batch.records;
  const published=new Promise<void>((resolve,reject)=>{
   const timer=setTimeout(()=>{stop();reject(new Error('fixture batch not admitted: '+JSON.stringify({wanted:batch.revision,generation,current:navigation.owner.getState()})));},5000);
   const stop=navigation.owner.subscribe(state=>{if(state.publication?.continuity.revision===batch.revision){clearTimeout(timer);stop();resolve();}});
  });
  (window as any).__navigationSource.dispatchEvent(new MessageEvent('batch',{data:JSON.stringify({wire_version:response.wire_version,authority:response.authority,batch}),lastEventId:response.cursor}));
  await published;await tick();return;
 }
 throw new Error('fixture source changed generation during publication');
};
(window as any).__wait=()=>{
 const publication=navigation.owner.getState().publication!;
 return navigation.owner.waitForVisibility({...publication.continuity,version:1,coverage:'complete',witnesses:['a'.repeat(64)],signature:'synthetic'},
 {requiredObservers:navigation.captureRenderBoundaries(),timeoutMs:250});
};
import ConfigRoute from '../src/routes/ConfigRoute.svelte';
client.setDefaultOptions({ queries: { retry: false } });
(window as any).__detail=()=>client.invalidateQueries({queryKey:['ticket']});
(window as any).__manifest=()=>client.invalidateQueries({queryKey:['worker-types']});

let review = $state(false);
let reviewTicketId = $state('t_review');
let historicalIdentity=$derived(navigation.selectors.effective ? navigationReviewPresentation(navigation.selectors.effective).items.find(item=>item.ticket_id===reviewTicketId) : undefined);
let reviewField = $state('success_condition');
let config = $state(false);
let ticketId = $state('t_review');
(window as any).__showReview = (id = "t_review", field = "success_condition") => { reviewTicketId = id; reviewField = field; review = true; };
(window as any).__showTicket = (id: string) => { review = false; ticketId = id; };
(window as any).__showConfig = () => { config = true; };
</script>
<QueryClientProvider {client}>
{#if config}<ConfigRoute roleKind="worker" roleId="coding" />
{:else if review}{#if reviewTicketId === "t_personal_pending"}{#if historicalIdentity}<ReviewProposalCard identity={historicalIdentity} ticketId={reviewTicketId} field={reviewField} />{/if}{:else}<ShellStatus connectionState="connected" /><ReviewRoute />{/if}

{:else}{#key ticketId}<TicketRoute id={ticketId} />{/key}{/if}
</QueryClientProvider>`);
  await writeFile(main, `globalThis.__openStreams=new Set();globalThis.EventSource=class extends EventTarget{onerror=null;constructor(url){super();this.url=String(url);globalThis.__openStreams.add(this.url);if(this.url.startsWith('/api/navigation/events?'))globalThis.__navigationSource=this;}close(){globalThis.__openStreams.delete(this.url);}}; import { mount } from 'svelte'; import Host from './${stem}.svelte'; import '../../assets/tokens.css'; import '../../assets/app.css'; mount(Host, {target: document.getElementById('app')!});`);
  await writeFile(index, `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);

  return {inputs: [index], arguments: [index], python: fileURLToPath(new URL("./ticket-review-flow.py", import.meta.url))};
}

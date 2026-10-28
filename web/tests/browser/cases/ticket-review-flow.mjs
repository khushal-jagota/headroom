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
import FilePreviewRoute from '../src/routes/FilePreviewRoute.svelte';
import { navigationReviewPresentation } from '../src/lib/navigation/presentation';
import { reviewBadgeDomMatches } from '../src/lib/shellDom';
import {readComposerDraft,writeComposerDraft} from '../src/lib/conversation/composerDraft';
(window as any).__writeChiefDraft=(text:string)=>writeComposerDraft('owner:chief',text);
(window as any).__readChiefDraft=()=>readComposerDraft('owner:chief');
(window as any).__badgeProof=()=>{
 const link=document.createElement('a');const badge=document.createElement('span');link.append(badge);
 badge.className='count-badge hidden';
 badge.setAttribute('data-review-badge','');
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
 const request=navigation.owner.getRequest();
 const generation=navigation.state.generation;
 const response=await (await fetch('/api/test/navigation-publication?view='+encodeURIComponent(JSON.stringify(request)),{method:'POST'})).json();
 if(navigation.state.generation!==generation)return navigation.owner.recover();
 const before=navigation.owner.getState().publication!;
 if(response.coverage.revision===before.continuity.revision)return;
 const keys=new Set(response.content.records.map((row:any)=>JSON.stringify([row.kind,row.id])));
 const frame:any={event:'patch',wire_version:3,projection_version:7,view_id:response.view_id,
 scope_generation:response.scope_generation,ordering_generation:response.ordering_generation,
 coverage:response.coverage,previous_coverage:before.continuity,authority:response.authority,
 content_version:response.content_version,previous_content_version:before.view!.contentVersion,
 upserts:response.content.records,evictions:before.records.filter(row=>!keys.has(JSON.stringify([row.kind,row.id]))).map(row=>({kind:row.kind,id:row.id})),
 windows:response.content.windows,counts:response.content.counts,review_context:response.content.review_context,review_summary:response.content.review_summary};
 const event=response.content_version===before.view!.contentVersion?'checkpoint':'patch';
 if(event==='checkpoint'){delete frame.upserts;delete frame.evictions;delete frame.windows;delete frame.counts;delete frame.review_context;delete frame.review_summary;delete frame.previous_content_version;frame.event=event;}
 (window as any).__navigationSource.dispatchEvent(new MessageEvent(event,{data:JSON.stringify(frame)}));
 await tick();
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
let historicalIdentity=$derived(navigation.selectors.effective ? navigationReviewPresentation(navigation.selectors.effective).items.find(item=>item.review_item_type==='ticket' && item.ticket_id===reviewTicketId)?.proposal : undefined);
let reviewField = $state('success_condition');
let config = $state(false);
let ticketId = $state('t_review');
// The one-document preview address, mounted the way the shell mounts it: the route reads
// the file out of the hash on arrival, so each visit has to be a fresh arrival.
let preview = $state(false);
let previewArrival = $state(0);
$effect(() => navigation.owner.setDemand('historical-review-fixture', review && reviewTicketId === 't_personal_pending'
  ? {selected:{kind:'ticket',id:reviewTicketId},windows:[]} : undefined));
(window as any).__showReview = (id = "t_review", field = "success_condition") => { preview = false; reviewTicketId = id; reviewField = field; review = true; };
(window as any).__showTicket = (id: string) => { preview = false; review = false; ticketId = id; };
(window as any).__showPreview = (hash: string) => { location.hash = hash; previewArrival += 1; preview = true; };
(window as any).__showConfig = () => { config = true; };
</script>
<QueryClientProvider {client}>
{#if config}<ConfigRoute roleKind="worker" roleId="coding" />
{:else if preview}{#key previewArrival}<FilePreviewRoute />{/key}
{:else if review}{#if reviewTicketId === "t_personal_pending"}{#if historicalIdentity}<ReviewProposalCard identity={historicalIdentity} ticketId={reviewTicketId} field={reviewField} />{/if}{:else}<ShellStatus connectionState="connected" /><ReviewRoute />{/if}

{:else}{#key ticketId}<TicketRoute id={ticketId} />{/key}{/if}
</QueryClientProvider>
<style>:global(html),:global(body),:global(#app){height:100%;margin:0;}</style>`);
  await writeFile(main, `import {viewSnapshot,viewChanges} from '../tests/support/conversation/view-fixture'; globalThis.__openStreams=new Set();globalThis.EventSource=class extends EventTarget{onerror=null;constructor(url){super();this.url=String(url);globalThis.__openStreams.add(this.url);if(this.url.includes('/view/tail?')){const request=new URL(this.url,location.origin);const base=request.pathname.split('/view/')[0];Promise.all([fetch(base).then(r=>r.json()),fetch(base+'/events?after=0').then(r=>r.json())]).then(([metadata,record])=>{if(this.closed)return;const id=base.split('/').at(-1);const snapshot=viewSnapshot(id,request.searchParams.get('lens')==='full'?'full':'focus',record.events??[],request.searchParams.get('transcript')!=='false');snapshot.coverage.through_sequence=metadata.latest_sequence??0;const after=request.searchParams.get('after');this.dispatchEvent(new MessageEvent(after===null?'snapshot':'changes',{data:JSON.stringify(after===null?snapshot:viewChanges(snapshot,Number(after)))}));}).catch(()=>this.onerror?.());}if(this.url.startsWith('/api/navigation/view/events?')){globalThis.__navigationSource=this;fetch(this.url.replace('/events?','/snapshot?')).then(response=>{if(!response.ok)throw new Error('Navigation unavailable');return response.json();}).then(value=>{if(!this.closed)this.dispatchEvent(new MessageEvent('snapshot',{data:JSON.stringify(value)}));}).catch(()=>{if(!this.closed)this.onerror?.();});}}close(){this.closed=true;globalThis.__openStreams.delete(this.url);}}; import { mount } from 'svelte'; import Host from './${stem}.svelte'; import '../../assets/tokens.css'; import '../../assets/app.css'; mount(Host, {target: document.getElementById('app')!});`);
  await writeFile(index, `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);

  return {inputs: [index], arguments: [index], python: fileURLToPath(new URL("./ticket-review-flow.py", import.meta.url))};
}

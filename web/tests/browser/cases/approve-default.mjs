import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const stem = `approve-default-${process.pid}`;
const host = join(scratchRoot, `${stem}.svelte`);
const main = join(scratchRoot, `${stem}.ts`);
const index = join(scratchRoot, `${stem}.html`);

  await writeFile(host, `<script lang="ts">
import { QueryClientProvider } from '@tanstack/svelte-query';
import { queryClient as client } from '../src/lib/queryClient';
import TicketRoute from '../src/routes/TicketRoute.svelte';
import ReviewRoute from '../src/routes/ReviewRoute.svelte';
import { tick } from 'svelte';
import { provideNavigationApplication } from '../src/lib/navigation/application.svelte';
const navigation=provideNavigationApplication();
import {navigationScrollWindow} from '../src/lib/navigation/views/scrollWindow';
import {viewWindow} from '../src/lib/navigation/views/owner';
(window as any).__scrollWindowProof=async(size:number,short=false,empty=false)=>{
 const root=document.createElement('div');root.style.cssText='position:fixed;top:0;left:0;height:64px;width:200px;overflow-y:auto;background:white;z-index:99999';document.body.append(root);
 let start=0,moves=0,activations=0,admitted=!empty;const rowHeight=32;if(short)root.style.height='300px';
 const draw=()=>{root.replaceChildren(...Array.from({length:admitted?size:0},(_,i)=>{const row=document.createElement('div');row.dataset.scrollKey=String(start+i);row.style.height=rowHeight+'px';row.textContent=String(start+i);return row;}));};
 const view=()=>({window_id:'fixture',request:viewWindow('rail_parents',null,{limit:size,anchor:start?String(start):null}),members:Array.from({length:admitted?size:0},(_,i)=>({kind:'orchestrator' as const,id:String(start+i)})),total:180,previous:start?String(Math.max(0,start-size)):null,next:String(start+size),forward_overlap:size>1?String(start+Math.floor(size/2)):null,backward_overlap:start&&size>1?String(Math.max(0,start-Math.floor(size/2))):null});
 const options=()=>({window:view(),activate:()=>{if(!admitted){activations++;admitted=true;draw();action.update(options());}},selector:'[data-scroll-key]',key:(node:HTMLElement)=>node.dataset.scrollKey!,move:(next:string,direction:'before'|'after')=>{moves++;start=Number(next);draw();action.update(options());}});
 draw();const action=navigationScrollWindow(root,options());
 const paint=()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve())));
 try{
  await paint();const height=root.scrollHeight;const initialMoves=moves;if(empty){root.dispatchEvent(new Event('pointerenter'));await paint();}if(short)root.dispatchEvent(new WheelEvent('wheel',{deltaY:100,cancelable:true,bubbles:true}));else root.scrollTop=Math.floor(size/2)*rowHeight;await paint();await paint();
  const key=String(Math.floor(size/2));const node=root.querySelector<HTMLElement>('[data-scroll-key="'+key+'"]');
  const forward={moves,start,top:node?node.getBoundingClientRect().top-root.getBoundingClientRect().top:null,height:root.scrollHeight,rows:root.children.length};
  if(short)root.dispatchEvent(new WheelEvent('wheel',{deltaY:-100,cancelable:true,bubbles:true}));else{root.scrollTop=1;await paint();root.scrollTop=0;}await paint();await paint();
  const backward=root.querySelector<HTMLElement>('[data-scroll-key="'+key+'"]');
  return {height,initialMoves,activations,forward,backward:{moves,start,top:backward?backward.getBoundingClientRect().top-root.getBoundingClientRect().top:null,rows:root.children.length}};
 }finally{action.destroy();root.remove();}
};

(window as any).__publishSource=async()=>{
 const ready=async()=>{
  if(navigation.owner.getState().connection==='subscribed')return;
  await new Promise<void>((resolve,reject)=>{
   let stop=()=>{};
   const timer=setTimeout(()=>{stop();reject(new Error('fixture stream not ready: '+JSON.stringify(navigation.owner.getState())));},5000);
   stop=navigation.owner.subscribe(state=>{if(state.connection==='subscribed'){clearTimeout(timer);stop();resolve();}});
  });
 };
 for(let attempt=0;attempt<3;attempt++){
  await ready();
  const generation=navigation.owner.getState().generation;
  const response=await (await fetch('/api/navigation/view/snapshot?view='+encodeURIComponent(JSON.stringify(navigation.owner.getRequest())))).json();
  const current=navigation.owner.getState();
  if(current.generation!==generation || current.connection!=='subscribed')continue;
  const previous=current.publication!;
  if(response.coverage.revision===previous.continuity.revision)return;
  const keys=new Set(response.content.records.map((row:any)=>JSON.stringify([row.kind,row.id])));
  const frame:any={event:'patch',wire_version:3,projection_version:2,view_id:response.view_id,
    scope_generation:response.scope_generation,ordering_generation:response.ordering_generation,
    coverage:response.coverage,previous_coverage:previous.continuity,authority:response.authority,
    content_version:response.content_version,previous_content_version:previous.view!.contentVersion,
    upserts:response.content.records,evictions:previous.records.filter(row=>!keys.has(JSON.stringify([row.kind,row.id]))).map(row=>({kind:row.kind,id:row.id})),
    windows:response.content.windows,counts:response.content.counts};
  const event=response.content_version===previous.view!.contentVersion?'checkpoint':'patch';
  if(event==='checkpoint'){delete frame.upserts;delete frame.evictions;delete frame.windows;delete frame.counts;delete frame.previous_content_version;frame.event=event;}
  (window as any).__navigationSource.dispatchEvent(new MessageEvent(event,{data:JSON.stringify(frame)}));
  await tick();return;
 }
 throw new Error('fixture source changed generation during publication');
};
client.setDefaultOptions({ queries: { retry: false } });

let hostKind = $state<'ticket' | 'review'>('ticket');
let ticketId = $state('t_coding');
let field = $state('success_condition');
(window as any).__showTicket = (id: string) => { hostKind = 'ticket'; ticketId = id; };
(window as any).__showReview = async (id: string, nextField: string) => { await navigation.owner.recover(); hostKind = 'review'; ticketId = id; field = nextField; };
(window as any).__refresh = () => Promise.all([
  client.refetchQueries({ queryKey: ['ticket'] }),
  client.refetchQueries({ queryKey: ['worker-types'] }),
  (window as any).__publishSource()
]);
</script>
<QueryClientProvider {client}>
  {#if hostKind === 'review'}
    {#key ticketId}<ReviewRoute />{/key}
  {:else}
    {#key ticketId}<TicketRoute id={ticketId} />{/key}
  {/if}
</QueryClientProvider>`);
  await writeFile(main, `globalThis.EventSource=class extends EventTarget {onerror=null;constructor(url){super();this.url=String(url);if(this.url.startsWith('/api/navigation/view/events?')){globalThis.__navigationSource=this;fetch(this.url.replace('/events?','/snapshot?')).then(response=>{if(!response.ok)throw new Error('unavailable');return response.json();}).then(value=>{if(!this.closed)this.dispatchEvent(new MessageEvent('snapshot',{data:JSON.stringify(value)}));}).catch(()=>{if(!this.closed)this.onerror?.();});}}close(){this.closed=true;}};import { mount } from 'svelte'; import Host from './${stem}.svelte'; import '../../assets/tokens.css'; import '../../assets/app.css'; mount(Host, {target: document.getElementById('app')!});`);
  await writeFile(index, `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);

  // A second entry reuses the same API fixture but preserves BoardRoute's real lazy import.
  const workspaceHost = join(scratchRoot, `${stem}-workspace.svelte`);
  const workspaceMain = join(scratchRoot, `${stem}-workspace.ts`);
  const workspaceIndex = join(scratchRoot, `${stem}-workspace.html`);
  await writeFile(workspaceHost, `<script lang="ts">
import { QueryClientProvider } from '@tanstack/svelte-query';
import { queryClient as client } from '../src/lib/queryClient';
import { provideNavigationApplication } from '../src/lib/navigation/application.svelte';
import BoardRoute from '../src/routes/BoardRoute.svelte';
provideNavigationApplication();
client.setDefaultOptions({queries:{retry:false}});
let address = $state<import('../src/lib/workspaceAddress').WorkspaceAddress>({selection:{kind:'ticket',id:'t_coding'},view:'tickets',openFile:null});
(window as any).__workspaceAddressChange = () => { address = {...address,view:'items',openFile:null}; };
</script>
<QueryClientProvider {client}><BoardRoute {address} /></QueryClientProvider>
<style>:global(html),:global(body),:global(#app){height:100%;margin:0;overflow:hidden;}</style>`);
  const existingMain = await (await import('node:fs/promises')).readFile(main, 'utf8');
  await writeFile(workspaceMain, existingMain.replace(`./${stem}.svelte`, `./${stem}-workspace.svelte`));
  await writeFile(workspaceIndex, `<html><body><div id="app"></div><script type="module" src="./${stem}-workspace.ts"></script></body></html>`);

  return {inputs: [index, workspaceIndex], arguments: [index, workspaceIndex], python: fileURLToPath(new URL("./approve-default.py", import.meta.url))};
}

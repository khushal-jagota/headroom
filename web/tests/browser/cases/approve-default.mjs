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
  const response=await (await fetch('/api/navigation/snapshot')).json();
  const current=navigation.owner.getState();
  if(current.generation!==generation || current.connection!=='subscribed')continue;
  const previous=current.publication!;
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
client.setDefaultOptions({ queries: { retry: false } });

let hostKind = $state<'ticket' | 'review'>('ticket');
let ticketId = $state('t_coding');
let field = $state('success_condition');
(window as any).__showTicket = async (id: string) => { if(!navigation.owner.getState().publication?.records.some(record=>record.kind==='ticket' && record.id===id)) await navigation.owner.recover(); hostKind = 'ticket'; ticketId = id; };
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
  await writeFile(main, `globalThis.EventSource=class extends EventTarget {onerror=null;constructor(url){super();if(String(url).startsWith('/api/navigation/events?'))globalThis.__navigationSource=this;}close(){}};import { mount } from 'svelte'; import Host from './${stem}.svelte'; import '../../assets/tokens.css'; import '../../assets/app.css'; mount(Host, {target: document.getElementById('app')!});`);
  await writeFile(index, `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);

  return {inputs: [index], arguments: [index], python: fileURLToPath(new URL("./approve-default.py", import.meta.url))};
}

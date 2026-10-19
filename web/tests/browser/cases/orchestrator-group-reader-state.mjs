import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const stem = `group-reader-state-${process.pid}`;
const files = ["svelte", "ts", "html"].map(ext => join(scratch, `${stem}.${ext}`));
  await writeFile(files[0], `<script lang="ts">
import { QueryClient, QueryClientProvider } from '@tanstack/svelte-query';
import OrchestratorChildren from '../src/components/OrchestratorChildren.svelte';
import BoardRoute from '../src/routes/BoardRoute.svelte';
import { tick } from 'svelte';
import { provideNavigationApplication } from '../src/lib/navigation/application.svelte';
const navigation=new URLSearchParams(location.search).has('mirror') ? provideNavigationApplication() : undefined;
(window as any).__refresh = async () => {
  await client.invalidateQueries();
  if (navigation) {
    const response=await (await fetch('/api/navigation/snapshot')).json();
    const previous=navigation.owner.getState().publication!;
    const keys=new Set(response.snapshot.records.map((row:any)=>JSON.stringify([row.kind,row.id])));
    const batch={...response.snapshot,base_revision:previous.continuity.revision,upserts:response.snapshot.records,
      deletes:previous.records.filter(row=>!keys.has(JSON.stringify([row.kind,row.id]))).map(row=>({kind:row.kind,id:row.id}))};
    delete batch.records;
    const published=new Promise<void>(resolve=>{const stop=navigation.owner.subscribe(state=>{if(state.publication?.continuity.revision===batch.revision){stop();resolve();}});});
    (window as any).__navigationSource.dispatchEvent(new MessageEvent('batch',{data:JSON.stringify({wire_version:response.wire_version,authority:response.authority,batch}),lastEventId:response.cursor}));
    await published;
  }
  await tick();
};
const mirror = new URLSearchParams(location.search).has('mirror');
import type { SprintItemWorkspaceTicket } from '../src/lib/types';
const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
let tickets = $state(['done','needs_implementation'].map((stage, i) => ({
 id:'t_'+i,title:stage,stage,is_backlogged:false,activity_at:1,
 ticket_status:'empty',waiting_to_closeout:false,has_pending_proposal:false,gating_field:null,
 blocked:false,worker_type:'coding',day_ids:[],sprint_id:null,sprint_name:null,
 awaiting_reply:false,awaiting_answer:false,awaiting_approval:false,
 awaiting_agent_approval:false,assigned:false,agent_state:'idle'
})) as SprintItemWorkspaceTicket[]);
(window as any).__setDone = (done: boolean) => {tickets = tickets.map(t => t.id === "t_0" ? {...t,stage:done ? "done" : "needs_implementation"} : t);};
</script>
<QueryClientProvider {client}>{#if mirror}<BoardRoute address={{selection:{kind:'item',id:'si_mirror'},view:'items',openFile:null}}/>{:else}<OrchestratorChildren principal={{kind:'sprint_item',id:'si_probe'}} {tickets}/>{/if}</QueryClientProvider>`);
  await writeFile(files[1], `globalThis.EventSource=class extends EventTarget {onerror=null;constructor(url){super();if(String(url).startsWith('/api/navigation/events?'))globalThis.__navigationSource=this;}close(){}};import {mount} from 'svelte';import Host from './${stem}.svelte';import '../../assets/tokens.css';import '../../assets/app.css';mount(Host,{target:document.body});`);
  await writeFile(files[2], `<html><body><script type="module" src="./${stem}.ts"></script></body></html>`);

  return {inputs: [files[2]], arguments: [files[2]], python: fileURLToPath(new URL("./orchestrator-group-reader-state.py", import.meta.url))};
}

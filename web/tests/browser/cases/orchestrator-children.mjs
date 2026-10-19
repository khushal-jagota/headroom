import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const stem = `orchestrator-children-${process.pid}`;
const files = [join(scratch, `${stem}.svelte`), join(scratch, `${stem}.ts`), join(scratch, `${stem}.html`)];
  await writeFile(files[0], `<script lang="ts">
import { QueryClient, QueryClientProvider } from "@tanstack/svelte-query";
import { tick } from "svelte";
import { provideNavigationApplication } from "../src/lib/navigation/application.svelte";
const navigation=provideNavigationApplication();
(window as any).__navigation=navigation;
import OrchestratorChildren from "../src/components/OrchestratorChildren.svelte";
import type { OrchestratorPrincipal, SprintItemWorkspaceTicket } from "../src/lib/types";
const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
let mounted=$state(true);
(window as any).__unmount=()=>mounted=false;
let principal = $state<OrchestratorPrincipal>({kind:"chief",id:"chief"});
let tickets = $state<SprintItemWorkspaceTicket[] | undefined>(undefined);
(window as any).__refresh = async () => {
 await client.invalidateQueries();
 const response=await (await fetch('/api/navigation/snapshot')).json();
 const previous=navigation.owner.getState().publication!;
 const keys=new Set(response.snapshot.records.map((row:any)=>JSON.stringify([row.kind,row.id])));
 const batch={...response.snapshot,base_revision:previous.continuity.revision,upserts:response.snapshot.records,
 deletes:previous.records.filter(row=>!keys.has(JSON.stringify([row.kind,row.id]))).map(row=>({kind:row.kind,id:row.id}))};delete batch.records;
 const published=new Promise<void>(resolve=>{const unsubscribe=navigation.owner.subscribe(state=>{if(state.publication?.continuity.revision===batch.revision){unsubscribe();resolve();}});});
 (window as any).__navigationSource.dispatchEvent(new MessageEvent('batch',{data:JSON.stringify({wire_version:response.wire_version,authority:response.authority,batch}),lastEventId:response.cursor}));
 await published;await tick();
};
(window as any).__setRows = (rows: SprintItemWorkspaceTicket[]) => { tickets = rows; };
(window as any).__setItem = (rows: SprintItemWorkspaceTicket[]) => { principal = {kind:"sprint_item",id:"item one"}; tickets = rows; };
</script>
<QueryClientProvider {client}>{#if mounted}<OrchestratorChildren {principal} {tickets} />{/if}</QueryClientProvider>`);
  await writeFile(files[1], `globalThis.EventSource=class extends EventTarget {onerror=null;constructor(url){super();if(String(url).startsWith('/api/navigation/events?'))globalThis.__navigationSource=this;}close(){}};import { mount } from "svelte"; import Host from "./${stem}.svelte"; import "../../assets/tokens.css"; import "../../assets/app.css"; mount(Host,{target:document.getElementById("app")!});`);
  await writeFile(files[2], `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);

  return {inputs: [files[2]], arguments: [files[2]], python: fileURLToPath(new URL("./orchestrator-children.py", import.meta.url))};
}

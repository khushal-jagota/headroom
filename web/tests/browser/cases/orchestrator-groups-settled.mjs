import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const stem = `orchestrator-groups-settled-${process.pid}`;
const paths = ["svelte", "ts", "html"].map((ext) => join(scratch, `${stem}.${ext}`));
const startedAt = performance.now();
  await writeFile(paths[0], `<script lang="ts">
import { QueryClientProvider } from '@tanstack/svelte-query';
import { tick } from 'svelte';
import BoardRoute from '../src/routes/BoardRoute.svelte';

import { provideNavigationApplication } from '../src/lib/navigation/application.svelte';
const navigation=provideNavigationApplication();
(window as any).__navigation=navigation;
const wait=navigation.owner.waitForVisibility;
navigation.owner.waitForVisibility=(fence,options)=>{
 (window as any).__commandWaitStatus='pending';
 (window as any).__commandRequired=options.requiredObservers.slice();
 return wait(fence,options).then(result=>{(window as any).__commandWaitStatus=result.status;return result;});
};
(window as any).__startAllWait=()=>{
 const publication=navigation.owner.getState().publication!;
 (window as any).__allWaitStatus='pending';
 void navigation.owner.waitForVisibility({...publication.continuity,version:1,coverage:'complete',witnesses:['a'.repeat(64)],signature:'synthetic-proof'},
 {requiredObservers:navigation.captureRenderBoundaries(),timeoutMs:1000}).then(result=>(window as any).__allWaitStatus=result.status);
};
import { parseWorkspaceAddress } from '../src/lib/workspaceAddress';
import { queryClient as client } from '../src/lib/queryClient';
client.setDefaultOptions({ queries: { retry: false } });
let address = $state(parseWorkspaceAddress(window.location.hash)!);
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

</script>
<svelte:window onhashchange={() => address = parseWorkspaceAddress(window.location.hash) ?? address} />
<div class="shell">
  <header class="shell-mobile-head"><span class="shell-mobile-title">Panels</span></header>
  <header class="shell-nav"><nav class="shell-links">Panels · Workspace</nav></header>
  <main class="shell-content"><div class="screen"><QueryClientProvider {client}><BoardRoute {address} /></QueryClientProvider></div></main>
</div>`);
  await writeFile(paths[1], `globalThis.__openStreams=new Set();globalThis.EventSource=class extends EventTarget {onerror=null;constructor(url){super();this.url=String(url);globalThis.__openStreams.add(this.url);if(this.url.startsWith('/api/navigation/events?'))globalThis.__navigationSource=this;}close(){globalThis.__openStreams.delete(this.url);}};import { mount } from 'svelte'; import Host from './${stem}.svelte'; import '../../assets/tokens.css'; import '../../assets/app.css'; mount(Host, { target: document.getElementById('app')! });`);
  await writeFile(paths[2], `<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);

  return {inputs: [paths[2]], arguments: [paths[2]], python: fileURLToPath(new URL("./orchestrator-groups-settled.py", import.meta.url))};
}

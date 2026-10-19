import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const stem = `workspace-rail-at-rest-${process.pid}`;
const host = join(scratchRoot, `${stem}.svelte`);
const main = join(scratchRoot, `${stem}.ts`);
const index = join(scratchRoot, `${stem}.html`);
const cacheDir = join(scratchRoot, `${stem}.vite`);
const startedAt = performance.now();

  await writeFile(host, `<script lang="ts">
import { tick, onMount } from "svelte";
import { parseWorkspaceAddress } from "../src/lib/workspaceAddress";
import { QueryClient, QueryClientProvider } from "@tanstack/svelte-query";
import BoardRoute from "../src/routes/BoardRoute.svelte";
import { provideNavigationApplication } from "../src/lib/navigation/application.svelte";
const navigation=provideNavigationApplication();
(window as any).__navigation=navigation;
const railObservers: ReturnType<typeof navigation.owner.registerObserver>[]=[];
const register=navigation.owner.registerObserver;
navigation.owner.registerObserver=(name) => { const observer=register(name); if(name.startsWith('workspace-rail:')) railObservers.push(observer); return observer; };
(window as any).__startRailWait=() => {
  const publication=navigation.owner.getState().publication!;
  (window as any).__railWaitStatus='pending';
  void navigation.owner.waitForVisibility({...publication.continuity,version:1,coverage:'complete',witnesses:['a'.repeat(64)],signature:'synthetic-proof'},
    {requiredObservers:railObservers.slice(-1)}).then(result => (window as any).__railWaitStatus=result.status);
};

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
(window as any).__refresh = async () => {
  await client.invalidateQueries();
  const response=await (await fetch('/api/navigation/snapshot')).json();
  const previous=navigation.owner.getState().publication!;
  if(response.snapshot.revision===previous.continuity.revision){await tick();return;}
  if(BigInt(response.snapshot.revision)!==BigInt(previous.continuity.revision)+1n)throw new Error('controlled source revision gap');
  const keys=new Set(response.snapshot.records.map((record:any)=>JSON.stringify([record.kind,record.id])));
  const batch={...response.snapshot,base_revision:previous.continuity.revision,
    upserts:response.snapshot.records,deletes:previous.records.filter(record=>!keys.has(JSON.stringify([record.kind,record.id]))).map(record=>({kind:record.kind,id:record.id}))};
  delete batch.records;
  const published=new Promise<void>(resolve=>{ const unsubscribe=navigation.owner.subscribe(state=>{if(state.publication?.continuity.revision===batch.revision){unsubscribe();resolve();}}); });
  (window as any).__navigationSource.dispatchEvent(new MessageEvent('batch',{data:JSON.stringify({wire_version:response.wire_version,authority:response.authority,batch}),lastEventId:response.cursor}));
  await published; await tick(); };
const refresh=(window as any).__refresh;
(window as any).__refresh=()=>Promise.race([refresh(),new Promise((_,reject)=>setTimeout(()=>reject(new Error('source refresh exceeded5s: '+JSON.stringify({connection:navigation.owner.getState().connection,generation:navigation.owner.getState().generation,continuity:navigation.owner.getState().publication?.continuity}))),5000))]);
let address = $state(parseWorkspaceAddress(location.hash) ?? { selection: {kind:"none" as const},view:"items" as const,openFile:null });
onMount(() => {
  const update = () => address = parseWorkspaceAddress(location.hash) ?? {selection:{kind:"none"},view:"items",openFile:null};
  window.addEventListener("hashchange", update);
  return () => window.removeEventListener("hashchange", update);
});
</script>

<QueryClientProvider {client}>
  <BoardRoute {address} />
</QueryClientProvider>
`, "utf8");
  await writeFile(
    main,
    `globalThis.EventSource = class extends EventTarget { onerror = null; constructor(url) { super(); if(String(url).startsWith('/api/navigation/events?')) globalThis.__navigationSource=this; } close() {} }; import { mount } from "svelte"; import Host from "./${stem}.svelte"; import "../../assets/tokens.css"; import "../../assets/app.css"; mount(Host, { target: document.getElementById("app")! });`,
    "utf8"
  );
  await writeFile(
    index,
    `<html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`,
    "utf8"
  );

  return {inputs: [index], arguments: [index], python: fileURLToPath(new URL("./workspace-rail-at-rest.py", import.meta.url))};
}

import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const stem = `workspace-order-${process.pid}`;
const host = join(scratchRoot, `${stem}.svelte`);
const main = join(scratchRoot, `${stem}.ts`);
const index = join(scratchRoot, `${stem}.html`);
const cacheDir = join(scratchRoot, `${stem}.vite`);

  await writeFile(host, `<script lang="ts">
import { QueryClient, QueryClientProvider } from "@tanstack/svelte-query";
import BoardRoute from "../src/routes/BoardRoute.svelte";
import { provideNavigationApplication } from "../src/lib/navigation/application.svelte";
const navigation=provideNavigationApplication();
(window as any).__navigation=navigation;
const hoverObservers: ReturnType<typeof navigation.owner.registerObserver>[]=[];
const register=navigation.owner.registerObserver;
navigation.owner.registerObserver=(name)=>{const observer=register(name);if(name.startsWith('workspace-hover:'))hoverObservers.push(observer);return observer;};
(window as any).__startHoverWait=()=>{
 const publication=navigation.owner.getState().publication!;
 (window as any).__hoverWaitStatus='pending';
 void navigation.owner.waitForVisibility({...publication.continuity,version:1,coverage:'complete',witnesses:['a'.repeat(64)],signature:'synthetic-proof'},
 {requiredObservers:hoverObservers.slice(-1),timeoutMs:250}).then(result=>(window as any).__hoverWaitStatus=result.status);
};
(window as any).__recover=()=>navigation.owner.recover().catch(()=>undefined);
(window as any).__waitChief=()=>{
 const publication=navigation.owner.getState().publication!;
 return navigation.owner.waitForVisibility({...publication.continuity,version:1,coverage:'complete',witnesses:['a'.repeat(64)],signature:'synthetic-proof'},
 {requiredObservers:navigation.captureRenderBoundaries().filter(observer=>observer.id.startsWith('chief-conversation:')),timeoutMs:250});
};
import { parseWorkspaceAddress } from "../src/lib/workspaceAddress";

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
let address = $state(parseWorkspaceAddress(window.location.hash)!);
(window as any).__refresh = async () => {
  await client.invalidateQueries();
  const response=await (await fetch('/api/navigation/snapshot')).json();
  const previous=navigation.owner.getState().publication!;
  const keys=new Set(response.snapshot.records.map((record:any)=>JSON.stringify([record.kind,record.id])));
  const batch={...response.snapshot,base_revision:previous.continuity.revision,
    upserts:response.snapshot.records,deletes:previous.records.filter(record=>!keys.has(JSON.stringify([record.kind,record.id]))).map(record=>({kind:record.kind,id:record.id}))};
  delete batch.records;
  const published=new Promise<void>(resolve=>{ const unsubscribe=navigation.owner.subscribe(state=>{if(state.publication?.continuity.revision===batch.revision){unsubscribe();resolve();}}); });
  (window as any).__navigationSource.dispatchEvent(new MessageEvent('batch',{data:JSON.stringify({wire_version:response.wire_version,authority:response.authority,batch}),lastEventId:response.cursor}));
  await published; };
</script>

<svelte:window onhashchange={() => address = parseWorkspaceAddress(window.location.hash)!} />
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
    `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`,
    "utf8"
  );

  return {inputs: [index], arguments: [index], python: fileURLToPath(new URL("./workspace-order.py", import.meta.url))};
}

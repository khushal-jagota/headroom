import {writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

export async function prepare({scratchRoot}) {
  const host = `vps-page-host-${process.pid}.svelte`;
  const main = `vps-page-main-${process.pid}.ts`;
  const html = join(scratchRoot, `vps-page-index-${process.pid}.html`);
  await writeFile(join(scratchRoot, host), `<script lang="ts">
import {QueryClient, QueryClientProvider} from '@tanstack/svelte-query';
import VpsPage from '../src/components/vps/VpsPage.svelte';
const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
let mounted = $state(true); let visible = $state(true);
(window as any).__vpsMount=(value:boolean)=>mounted=value;
(window as any).__vpsVisible=(value:boolean)=>visible=value;
</script>
<QueryClientProvider client={client}>{#if mounted}<VpsPage {visible}/>{/if}</QueryClientProvider>
<style>:global(html),:global(body),:global(#app){margin:0;min-height:100%;}</style>`);
  await writeFile(join(scratchRoot, main), `import {mount} from 'svelte'; import Host from './${host}'; import '../../assets/tokens.css'; import '../../assets/app.css'; mount(Host,{target:document.getElementById('app')!});`);
  await writeFile(html, `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="app"></div><script type="module" src="./${main}"></script></body></html>`);
  return {inputs:[html], arguments:[html], python:fileURLToPath(new URL('./vps-page.py',import.meta.url))};
}

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
import { parseWorkspaceAddress } from '../src/lib/workspaceAddress';
import { queryClient as client } from '../src/lib/queryClient';
client.setDefaultOptions({ queries: { retry: false } });
let address = $state(parseWorkspaceAddress(window.location.hash)!);
(window as any).__refresh = async () => { await client.invalidateQueries(); await tick(); };
</script>
<svelte:window onhashchange={() => address = parseWorkspaceAddress(window.location.hash) ?? address} />
<div class="shell">
  <header class="shell-mobile-head"><span class="shell-mobile-title">Panels</span></header>
  <header class="shell-nav"><nav class="shell-links">Panels · Workspace</nav></header>
  <main class="shell-content"><div class="screen"><QueryClientProvider {client}><BoardRoute {address} /></QueryClientProvider></div></main>
</div>`);
  await writeFile(paths[1], `import { mount } from 'svelte'; import Host from './${stem}.svelte'; import '../../assets/tokens.css'; import '../../assets/app.css'; mount(Host, { target: document.getElementById('app')! });`);
  await writeFile(paths[2], `<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);

  return {inputs: [paths[2]], arguments: [paths[2]], python: fileURLToPath(new URL("./orchestrator-groups-settled.py", import.meta.url))};
}

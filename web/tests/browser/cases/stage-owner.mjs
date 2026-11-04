import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const stem = `stage-owner-${process.pid}`;
const host = join(scratchRoot, `${stem}.svelte`);
const main = join(scratchRoot, `${stem}.ts`);
const index = join(scratchRoot, `${stem}.html`);

  await writeFile(host, `<script lang="ts">
import { QueryClientProvider } from '@tanstack/svelte-query';
import ConfigRoute from '../src/routes/ConfigRoute.svelte';
// The app's own client, because a write's invalidation goes through that one instance.
import { queryClient } from '../src/lib/queryClient';
</script>
<QueryClientProvider client={queryClient}>
  <ConfigRoute roleKind="worker" roleId="coding" />
</QueryClientProvider>`);
  await writeFile(main, `import { mount } from 'svelte'; import Host from './${stem}.svelte'; import '../../assets/tokens.css'; import '../../assets/app.css'; mount(Host, {target: document.getElementById('app')!});`);
  await writeFile(index, `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);


  return {inputs: [index], arguments: [index], python: fileURLToPath(new URL("./stage-owner.py", import.meta.url))};
}

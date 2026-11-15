import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const host = `skill-editors-host-${process.pid}.svelte`;
  const main = `skill-editors-main-${process.pid}.ts`;
  const html = join(scratchRoot, `skill-editors-index-${process.pid}.html`);
  await writeFile(join(scratchRoot, host), `<script lang="ts">
import { QueryClientProvider } from '@tanstack/svelte-query';
import ConfigRoute from '../src/routes/ConfigRoute.svelte';
import { queryClient } from '../src/lib/queryClient';
import type { ConfigSelection } from '../src/lib/shellRouting';
const selection = new URLSearchParams(location.search);
const roleKind = selection.get('roleKind') as ConfigSelection['roleKind'];
const roleId = selection.get('roleId') ?? undefined;
</script>
<QueryClientProvider client={queryClient}><ConfigRoute {roleKind} {roleId} /></QueryClientProvider>
`);
  await writeFile(join(scratchRoot, main), `import {mount} from 'svelte'; import Host from './${host}'; import '../../assets/tokens.css'; import '../../assets/app.css'; mount(Host,{target:document.getElementById('app')!});`);
  await writeFile(html, `<!doctype html><html><body><div id="app"></div><script type="module" src="./${main}"></script></body></html>`);
  return {inputs: [html], arguments: [html], python: fileURLToPath(new URL("./skill-editors.py", import.meta.url))};
}

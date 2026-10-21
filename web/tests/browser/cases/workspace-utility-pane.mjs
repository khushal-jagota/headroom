import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const host = `workspace-utility-pane-host-${process.pid}.svelte`;
  const main = `workspace-utility-pane-main-${process.pid}.ts`;
  const html = join(scratchRoot, `workspace-utility-pane-index-${process.pid}.html`);
  // The whole shell, so the address is read by the real router on every load and on a
  // reload. Only the change stream is replaced: it never settles and nothing here reads it.
  await writeFile(join(scratchRoot, host), `<script lang="ts">
import AppWithQueryClient from '../src/AppWithQueryClient.svelte';
class QuietEventSource { onopen = null; onmessage = null; onerror = null; addEventListener() {} removeEventListener() {} close() {} }
(globalThis as any).EventSource = QuietEventSource;
</script>
<AppWithQueryClient />
<style>:global(html),:global(body),:global(#app){height:100%;margin:0;overflow:hidden;}</style>
`);
  await writeFile(join(scratchRoot, main), `import {mount} from 'svelte'; import Host from './${host}'; import '../../assets/tokens.css'; import '../../assets/app.css'; mount(Host,{target:document.getElementById('app')!});`);
  await writeFile(html, `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="app"></div><script type="module" src="./${main}"></script></body></html>`);
  return {inputs: [html], arguments: [html], python: fileURLToPath(new URL("./workspace-utility-pane.py", import.meta.url))};
}

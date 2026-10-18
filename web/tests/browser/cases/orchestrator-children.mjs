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
import OrchestratorChildren from "../src/components/OrchestratorChildren.svelte";
import type { OrchestratorPrincipal, SprintItemWorkspaceTicket } from "../src/lib/types";
const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
let principal = $state<OrchestratorPrincipal>({kind:"chief",id:"chief"});
let tickets = $state<SprintItemWorkspaceTicket[] | undefined>(undefined);
(window as any).__refresh = async () => { await client.invalidateQueries(); await tick(); };
(window as any).__setRows = (rows: SprintItemWorkspaceTicket[]) => { tickets = rows; };
(window as any).__setItem = (rows: SprintItemWorkspaceTicket[]) => { principal = {kind:"sprint_item",id:"item one"}; tickets = rows; };
</script>
<QueryClientProvider {client}><OrchestratorChildren {principal} {tickets} /></QueryClientProvider>`);
  await writeFile(files[1], `import { mount } from "svelte"; import Host from "./${stem}.svelte"; import "../../assets/tokens.css"; import "../../assets/app.css"; mount(Host,{target:document.getElementById("app")!});`);
  await writeFile(files[2], `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);

  return {inputs: [files[2]], arguments: [files[2]], python: fileURLToPath(new URL("./orchestrator-children.py", import.meta.url))};
}

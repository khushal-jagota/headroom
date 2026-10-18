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
import { parseWorkspaceAddress } from "../src/lib/workspaceAddress";

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
let address = $state(parseWorkspaceAddress(window.location.hash)!);
(window as any).__refresh = () => client.invalidateQueries();
</script>

<svelte:window onhashchange={() => address = parseWorkspaceAddress(window.location.hash)!} />
<QueryClientProvider {client}>
  <BoardRoute {address} />
</QueryClientProvider>
`, "utf8");
  await writeFile(
    main,
    `import { mount } from "svelte"; import Host from "./${stem}.svelte"; import "../../assets/tokens.css"; import "../../assets/app.css"; mount(Host, { target: document.getElementById("app")! });`,
    "utf8"
  );
  await writeFile(
    index,
    `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`,
    "utf8"
  );


  return {inputs: [index], arguments: [index], python: fileURLToPath(new URL("./workspace-order.py", import.meta.url))};
}

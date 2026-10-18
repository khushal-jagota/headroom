import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const stem = `approve-default-${process.pid}`;
const host = join(scratchRoot, `${stem}.svelte`);
const main = join(scratchRoot, `${stem}.ts`);
const index = join(scratchRoot, `${stem}.html`);

  await writeFile(host, `<script lang="ts">
import { QueryClientProvider } from '@tanstack/svelte-query';
import { queryClient as client } from '../src/lib/queryClient';
import TicketRoute from '../src/routes/TicketRoute.svelte';
import ReviewRoute from '../src/routes/ReviewRoute.svelte';
let hostKind = $state<'ticket' | 'review'>('ticket');
let ticketId = $state('t_coding');
let field = $state('success_condition');
(window as any).__showTicket = (id: string) => { hostKind = 'ticket'; ticketId = id; };
(window as any).__showReview = (id: string, nextField: string) => { hostKind = 'review'; ticketId = id; field = nextField; };
(window as any).__refresh = () => Promise.all([
  client.refetchQueries({ queryKey: ['ticket'] }),
  client.refetchQueries({ queryKey: ['worker-types'] })
]);
</script>
<QueryClientProvider {client}>
  {#if hostKind === 'review'}
    {#key ticketId}<ReviewRoute />{/key}
  {:else}
    {#key ticketId}<TicketRoute id={ticketId} />{/key}
  {/if}
</QueryClientProvider>`);
  await writeFile(main, `import { mount } from 'svelte'; import Host from './${stem}.svelte'; import '../../assets/tokens.css'; import '../../assets/app.css'; mount(Host, {target: document.getElementById('app')!});`);
  await writeFile(index, `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);


  return {inputs: [index], arguments: [index], python: fileURLToPath(new URL("./approve-default.py", import.meta.url))};
}

import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const stem = `ticket-review-${process.pid}`;
const host = join(scratchRoot, `${stem}.svelte`);
const main = join(scratchRoot, `${stem}.ts`);
const index = join(scratchRoot, `${stem}.html`);
  await writeFile(host, `<script lang="ts">
import { QueryClientProvider } from '@tanstack/svelte-query';
import { queryClient as client } from '../src/lib/queryClient';
import TicketRoute from '../src/routes/TicketRoute.svelte';
import ReviewProposalCard from '../src/components/ReviewProposalCard.svelte';
import ConfigRoute from '../src/routes/ConfigRoute.svelte';
client.setDefaultOptions({ queries: { retry: false } });
let review = $state(false);
let reviewTicketId = $state('t_review');
let reviewField = $state('success_condition');
let config = $state(false);
let ticketId = $state('t_review');
(window as any).__showReview = (id = "t_review", field = "success_condition") => { reviewTicketId = id; reviewField = field; review = true; };
(window as any).__showTicket = (id: string) => { review = false; ticketId = id; };
(window as any).__showConfig = () => { config = true; };
</script>
<QueryClientProvider {client}>
{#if config}<ConfigRoute roleKind="worker" roleId="coding" />
{:else if review}<ReviewProposalCard ticketId={reviewTicketId} field={reviewField} />
{:else}{#key ticketId}<TicketRoute id={ticketId} />{/key}{/if}
</QueryClientProvider>`);
  await writeFile(main, `import { mount } from 'svelte'; import Host from './${stem}.svelte'; import '../../assets/tokens.css'; import '../../assets/app.css'; mount(Host, {target: document.getElementById('app')!});`);
  await writeFile(index, `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);

  return {inputs: [index], arguments: [index], python: fileURLToPath(new URL("./ticket-review-flow.py", import.meta.url))};
}

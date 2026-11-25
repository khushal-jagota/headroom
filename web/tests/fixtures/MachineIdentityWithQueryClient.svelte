<script lang="ts">
  import { QueryClient, QueryClientProvider } from "@tanstack/svelte-query";
  import MachineIdentity from "../../src/components/machines/MachineIdentity.svelte";
  import type { Machine } from "../../src/lib/machines/contracts";
  import { untrack } from "svelte";
  import type { TicketLocale } from "../../src/lib/ticketMachines";
  let { machines, locale, machineId }: { machines: Machine[]; locale?: TicketLocale; machineId?: string } = $props();
  const client = new QueryClient({ defaultOptions: { queries: { enabled: false } } });
  untrack(() => client.setQueryData(["machines"], { body: { machines }, epoch: null, revision: null, decodedBytes: 0 }));
</script>
<QueryClientProvider {client}><MachineIdentity {locale} {machineId} showName /></QueryClientProvider>

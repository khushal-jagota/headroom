<script lang="ts">
  /** A live conversation inside the query client it reads its machine and drivers through. */
  import { QueryClient, QueryClientProvider } from "@tanstack/svelte-query";
  import LiveConversation from "../../src/components/conversation/LiveConversation.svelte";
  import type { ComponentProps } from "svelte";
  let props: ComponentProps<typeof LiveConversation> = $props();
  const client = new QueryClient({ defaultOptions: { queries: { enabled: false } } });
</script>

<QueryClientProvider {client}><LiveConversation {...props} /></QueryClientProvider>

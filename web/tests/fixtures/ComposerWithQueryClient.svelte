<script lang="ts">
  /** The conversation composer inside the query client it reads its drivers through. */
  import { QueryClient, QueryClientProvider } from "@tanstack/svelte-query";
  import ConversationComposer from "../../src/components/conversation/ConversationComposer.svelte";
  import type { ComponentProps } from "svelte";
  let props: ComponentProps<typeof ConversationComposer> = $props();
  const client = new QueryClient({ defaultOptions: { queries: { enabled: false } } });
</script>

<QueryClientProvider {client}><ConversationComposer {...props} /></QueryClientProvider>

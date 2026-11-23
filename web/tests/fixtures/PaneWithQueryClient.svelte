<script lang="ts">
  /** The conversation pane inside the query client its composer reads its drivers through. */
  import { QueryClient, QueryClientProvider } from "@tanstack/svelte-query";
  import ConversationPane from "../../src/components/conversation/ConversationPane.svelte";
  import type { ComponentProps } from "svelte";
  let props: ComponentProps<typeof ConversationPane> = $props();
  const client = new QueryClient({ defaultOptions: { queries: { enabled: false } } });
</script>

<QueryClientProvider {client}><ConversationPane {...props} /></QueryClientProvider>

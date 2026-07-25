<script lang="ts">
  import { QueryClientProvider } from "@tanstack/svelte-query";
  import App from "./App.svelte";
  import { queryClient } from "./lib/queryClient";
</script>

<!--
  The shared query client has to be provided above App, because App reads a
  query of its own (the review badge) and a component cannot serve itself the
  context it sets.
-->
<QueryClientProvider client={queryClient}>
  <App />
</QueryClientProvider>

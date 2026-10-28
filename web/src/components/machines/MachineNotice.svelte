<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../../lib/queryCatalogue";
  import { macActivityKnown, measuredContact } from "../../lib/ticketMachines";
  let { relevant = false }: { relevant?: boolean } = $props();
  const health = createQuery(() => ({...queries.executorHealth(), enabled:relevant}));
</script>
{#if relevant && (health.isError || !macActivityKnown(health.data))}
  <aside class="machine-notice" data-machine-notice role="status"><strong>Panels cannot see the Mac.</strong> Current activity on Mac Tickets is unknown. VPS Tickets are unaffected.<br /><small>{measuredContact(health.data)}</small></aside>
{/if}
<style>.machine-notice { position:sticky; top:0; z-index:3; background:var(--surface-1); color:var(--text-muted); padding:var(--space-3) var(--page-gutter); font-size:var(--type-sm); line-height:1.5; }.machine-notice strong { color:var(--text-strong); }.machine-notice small { font-size:var(--type-sm); }</style>

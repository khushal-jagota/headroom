<script lang="ts">
  import type { TicketLocale } from "../../lib/ticketMachines";
  let { locale }: { locale: TicketLocale } = $props();
</script>
<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
  {#if locale === "macos"}<path d="M5 4h14v12H5zM2 20l3-4h14l3 4H2Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" />
  {:else}<rect x="5" y="3" width="14" height="18" rx="2" stroke="currentColor" stroke-width="1.5" /><path d="M5 9h14M5 15h14M8 6h1m-1 6h1m-1 6h1" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />{/if}
</svg>
<style>svg { width:18px; height:18px; flex:none; }</style>

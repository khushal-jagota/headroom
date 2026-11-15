<script lang="ts">
  import type { TicketLocale } from "../../lib/ticketMachines";
  import type { MachineIcon } from "../../lib/machines/contracts";
  let { locale, icon = locale === "macos" ? "laptop" : "server" }: { locale?: TicketLocale; icon?: MachineIcon } = $props();
</script>
<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
  {#if icon === "laptop"}<path d="M5 4h14v12H5zM2 20l3-4h14l3 4H2Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" />
  {:else if icon === "desktop"}<path d="M3 3h18v13H3zM12 16v5m-5 0h10" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" />
  {:else if icon === "cloud"}<path d="M7 18h11a4 4 0 0 0 0-8 6 6 0 0 0-11-2 5 5 0 0 0 0 10Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" />
  {:else}<rect x="5" y="3" width="14" height="18" rx="2" stroke="currentColor" stroke-width="1.5" /><path d="M5 9h14M5 15h14M8 6h1m-1 6h1m-1 6h1" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />{/if}
</svg>
<style>svg { width:var(--type-md); height:var(--type-md); flex:none; }</style>

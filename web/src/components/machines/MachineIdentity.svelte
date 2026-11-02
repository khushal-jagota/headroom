<script lang="ts">
  import { machineName, type TicketLocale } from "../../lib/ticketMachines";
  import MachineSymbol from "./MachineSymbol.svelte";
  let { locale, accessible = true }: { locale: TicketLocale; accessible?: boolean } = $props();
</script>
<span class="machine-identity" data-ticket-locale={locale} role={accessible ? "img" : undefined} aria-label={accessible ? `Runs on ${machineName(locale)}` : undefined} aria-hidden={accessible ? undefined : "true"}>
  <MachineSymbol {locale} />
</span>
<style>
  .machine-identity { display:inline-flex; align-items:center; flex:none; width:var(--type-md); height:var(--type-md); color:var(--text-muted); }
</style>

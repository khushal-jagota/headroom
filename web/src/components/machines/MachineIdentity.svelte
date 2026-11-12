<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../../lib/queryCatalogue";
  import { machineName, type TicketLocale } from "../../lib/ticketMachines";
  import MachineSymbol from "./MachineSymbol.svelte";
  let { locale, machineId, accessible = true, showName = false }: { locale?: TicketLocale; machineId?: string; accessible?: boolean; showName?: boolean } = $props();
  const machines = createQuery(() => queries.machines());
  const machine = $derived(machines.data?.machines.find(item => machineId ? item.machine_id === machineId : item.is_home === (locale !== "macos")));
  const name = $derived(machine?.name ?? (machineId ? "Machine unavailable" : machineName(locale ?? "vps")));
</script>
<span class="machine-identity" class:with-name={showName} data-ticket-locale={locale} data-machine-id={machineId} data-machine-color={machine?.color} role={accessible ? "img" : undefined} aria-label={accessible ? `Runs on ${name}` : undefined} title={`Runs on ${name}`} aria-hidden={accessible ? undefined : "true"}>
  <MachineSymbol {locale} icon={machine?.icon} />{#if showName}<span>{name}</span>{/if}
</span>
<style>
  .machine-identity { display:inline-flex; align-items:center; gap:var(--space-1); flex:none; color:var(--machine-color, var(--text-faint)); }
  .with-name { font-size:var(--type-sm); }
  .with-name { min-width:0; max-width:100%; }
  .with-name > span { color:var(--tone-secondary); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
</style>

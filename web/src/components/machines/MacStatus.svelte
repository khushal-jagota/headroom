<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../../lib/queryCatalogue";
  import { measuredContact, macPresenceLabel, macTicketActivityLabel, macVersionDescription, type MacStatus, type MacTicketFacts } from "../../lib/ticketMachines";
  import { macUpdateLabel, macUpdateNeedsAttention } from "../../lib/machines/macUpdateStatus";
  // `ticket`: the Mac Ticket this status sits on, so a Mac away also says what that Ticket is doing.
  let { health, ticket, machineId }: { health?: MacStatus; ticket?: MacTicketFacts; machineId?: string } = $props();
  const machines = createQuery(() => queries.machines());
  const name = $derived(machines.data?.machines.find(item => item.machine_id === machineId)?.name ?? "Machine");
  const refusal = $derived(health?.presence === "refused" ? health.refusal ?? null : null);
  const presenceLabel = $derived(macPresenceLabel(health)?.replace("The Mac", name));
  const ticketLabel = $derived(ticket ? macTicketActivityLabel(health, ticket)?.replace("the Mac", name) : null);
  const update = $derived(health?.update);
</script>

{#if update || refusal || presenceLabel}
  <div class="mac-status" class:needs-attention={Boolean(refusal) || macUpdateNeedsAttention(update)} data-mac-status={refusal ? "refused" : update?.phase ?? health?.presence} role="status">
    {#if update}
      <span class="mac-status-title">{macUpdateLabel(update)}</span>
      <span class="mac-version">Target {update.target_revision.slice(0, 12)}</span>
      {#if update.detail}<span class="mac-contact">{update.detail}</span>{/if}
    {/if}
    {#if refusal}
      <span class="mac-status-title">{refusal.reason}</span>
      {#if refusal.peer_identity}<span class="mac-version">{name}: {macVersionDescription(refusal.peer_identity)}</span>{/if}
      <span class="mac-version">Server: {macVersionDescription(refusal.server_identity)}</span>
      <span class="mac-contact">Last refusal: {new Date(refusal.refused_at_ms).toLocaleString()}. {name} is not connected.</span>
    {:else if presenceLabel}
      {#if ticketLabel}<span class="mac-status-title" data-mac-ticket-activity>{ticketLabel}</span>{/if}
      <span class="mac-contact" data-mac-presence>{presenceLabel}</span>
    {/if}
    <span class="mac-contact">{measuredContact(health)}</span>
  </div>
{/if}

<style>
  .mac-status { display:flex; flex-direction:column; gap:var(--space-1); padding:var(--space-3); font-size:var(--type-sm); line-height:1.5; overflow-wrap:anywhere; }
  .needs-attention { border-radius:var(--radius-md); background:var(--accent-surface); }
  .mac-status-title { color:var(--text-strong); font-weight:660; }
  .mac-version { color:var(--text-default); }
  .mac-contact { color:var(--text-muted); }
</style>

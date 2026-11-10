<script lang="ts">
  import { measuredContact, macPresenceLabel, macTicketActivityLabel, macVersionDescription, type MacStatus, type MacTicketFacts } from "../../lib/ticketMachines";
  import { macUpdateLabel, macUpdateNeedsAttention } from "../../lib/machines/macUpdateStatus";
  // `ticket`: the Mac Ticket this status sits on, so a Mac away also says what that Ticket is doing.
  let { health, ticket }: { health?: MacStatus; ticket?: MacTicketFacts } = $props();
  const refusal = $derived(health?.presence === "refused" ? health.refusal ?? null : null);
  const presenceLabel = $derived(macPresenceLabel(health));
  const ticketLabel = $derived(ticket ? macTicketActivityLabel(health, ticket) : null);
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
      {#if refusal.peer_identity}<span class="mac-version">Mac: {macVersionDescription(refusal.peer_identity)}</span>{/if}
      <span class="mac-version">Server: {macVersionDescription(refusal.server_identity)}</span>
      <span class="mac-contact">Last refusal: {new Date(refusal.refused_at_ms).toLocaleString()}. The Mac is not connected.</span>
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

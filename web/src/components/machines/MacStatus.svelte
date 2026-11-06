<script lang="ts">
  import { measuredContact, macVersionDescription, type MacStatus } from "../../lib/ticketMachines";
  import { macUpdateLabel, macUpdateNeedsAttention } from "../../lib/machines/macUpdateStatus";
  let { health, showDisconnected = false }: { health?: MacStatus; showDisconnected?: boolean } = $props();
  const refusal = $derived(health?.connection_state === "disconnected" ? health.refusal : null);
  const update = $derived(health?.update);
</script>

{#if update || refusal || showDisconnected && health?.connection_state === "disconnected"}
  <div class="mac-status" class:needs-attention={Boolean(refusal) || macUpdateNeedsAttention(update)} data-mac-status={refusal ? "refused" : update?.phase ?? "disconnected"} role="status">
    {#if update}
      <span class="mac-status-title">{macUpdateLabel(update)}</span>
      <span class="mac-version">Target {update.target_revision.slice(0, 12)}</span>
      {#if update.detail}<span class="mac-contact">{update.detail}</span>{/if}
    {/if}
    {#if refusal}
      <span class="mac-status-title">{refusal.reason}</span>
      <span class="mac-version">Mac: {macVersionDescription(refusal.peer_identity)}</span>
      <span class="mac-version">Server: {macVersionDescription(refusal.server_identity)}</span>
      <span class="mac-contact">Last refusal: {new Date(refusal.refused_at_ms).toLocaleString()}. The Mac is not connected.</span>
    {:else if showDisconnected && health?.connection_state === "disconnected"}
      <span class="mac-contact">Mac disconnected</span>
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

<script lang="ts">
  import { measuredContact, macVersionDescription, type MacStatus } from "../../lib/ticketMachines";
  let { health, showDisconnected = false }: { health?: MacStatus; showDisconnected?: boolean } = $props();
  const refusal = $derived(health?.connection_state === "disconnected" ? health.refusal : null);
</script>

{#if refusal || showDisconnected && health?.connection_state === "disconnected"}
  <div class="mac-status" data-mac-status={refusal ? "refused" : "disconnected"} role="status">
    <span class="mac-status-title">{refusal?.reason ?? "Mac disconnected"}</span>
    {#if refusal}
      <span class="mac-version">Mac: {macVersionDescription(refusal.peer_identity)}</span>
      <span class="mac-version">Server: {macVersionDescription(refusal.server_identity)}</span>
      <span class="mac-contact">Last refusal: {new Date(refusal.refused_at_ms).toLocaleString()}. The Mac is not connected.</span>
    {/if}
    <span class="mac-contact">{measuredContact(health)}</span>
  </div>
{/if}

<style>
  .mac-status { display:flex; flex-direction:column; gap:var(--space-1); padding:var(--space-3); border-left:var(--border-strong) solid var(--accent-attention); font-size:var(--type-sm); line-height:1.5; overflow-wrap:anywhere; }
  .mac-status-title { color:var(--text-strong); font-weight:600; }
  .mac-version { color:var(--text-default); }
  .mac-contact { color:var(--text-muted); }
</style>

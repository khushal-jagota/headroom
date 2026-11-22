<script lang="ts">
  import { untrack } from "svelte";
  import { settingsSearch } from "../../lib/settings/search.svelte";
  import VpsPage from "../vps/VpsPage.svelte";
  import SettingsPage from "./SettingsPage.svelte";

  /* The home machine's diagnostics: the VPS page, with its freshness status and window
     control in the title row. A search result for one of its panels scrolls to that panel
     and flashes it, as a row would. */
  let element = $state<HTMLElement | null>(null);
  $effect(() => {
    const rowId = settingsSearch.flashRowId;
    if (!element || !rowId?.startsWith("diagnostics:")) return;
    const key = CSS.escape(rowId.slice("diagnostics:".length));
    const panel = element.querySelector<HTMLElement>(`[data-vps-metric="${key}"], [data-vps-workload="${key}"]`);
    if (!panel) return;
    // Clearing the request re-runs this effect, so the flash is not tied to its cleanup.
    untrack(() => (settingsSearch.flashRowId = null));
    panel.scrollIntoView({ block: "center" });
    panel.classList.add("diagnostics-flash");
    setTimeout(() => panel.classList.remove("diagnostics-flash"), 1600);
  });
</script>

<div bind:this={element}>
  <VpsPage>
    {#snippet frame(controls, body)}
      <SettingsPage title="Diagnostics" actions={controls}>{@render body()}</SettingsPage>
    {/snippet}
  </VpsPage>
</div>

<style>
  div :global(.diagnostics-flash) { position:relative; }
  div :global(.diagnostics-flash)::after { content:""; position:absolute; inset:0; border-radius:inherit; pointer-events:none; animation:diagnostics-flash 1.6s var(--motion-ease) forwards; }
  @keyframes diagnostics-flash { 0%, 35% { background:color-mix(in srgb, var(--accent-bright) 12%, transparent); } 100% { background:transparent; } }
  @media (prefers-reduced-motion: reduce) { div :global(.diagnostics-flash)::after { animation:none; } }
</style>

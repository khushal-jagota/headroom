<script lang="ts">
  import { resolvePreview, type ManagedFileTarget } from "../lib/filePreview";
  import FileDocument from "./FileDocument.svelte";

  let {
    target,
    reloadSignal = 0,
    element = $bindable(),
    onRefresh,
    onClose
  }: {
    target: ManagedFileTarget;
    reloadSignal?: number;
    element?: HTMLElement | null;
    onRefresh: () => void;
    onClose: () => void;
  } = $props();
  let resolved = $derived(resolvePreview(target));
</script>

<aside
  class="ticket-artifact"
  data-ticket-artifact
  aria-label={resolved.label}
  tabindex="-1"
  bind:this={element}
>
  <div class="ticket-artifact-bar">
    <span class="ticket-artifact-name">{resolved.label}</span>
    <div class="ticket-artifact-actions">
      {#if resolved.kind === "html"}
        <button type="button" class="ticket-artifact-action" data-ticket-artifact-refresh onclick={onRefresh}>Refresh</button>
      {/if}
      <button type="button" class="ticket-artifact-action" data-ticket-artifact-close onclick={onClose}>Close</button>
    </div>
  </div>
  <div class="ticket-artifact-body">
    <FileDocument {target} {reloadSignal} />
  </div>
</aside>

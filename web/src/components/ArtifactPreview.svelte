<script lang="ts">
  import type { Snippet } from "svelte";
  import type { RegisterManagedArtifactFrame } from "../lib/managedArtifactPress";
  import { resolvePreview, type ManagedFileTarget } from "../lib/filePreview";
  import FileDocument from "./FileDocument.svelte";

  let {
    target,
    reloadSignal = 0,
    registerArtifactFrame,
    element = $bindable(),
    onRefresh,
    onClose,
    bar
  }: {
    target: ManagedFileTarget;
    reloadSignal?: number;
    registerArtifactFrame?: RegisterManagedArtifactFrame;
    element?: HTMLElement | null;
    onRefresh: () => void;
    onClose: () => void;
    /** The controls of what held the link, such as a proposal's note and decisions. Given,
     *  they stand under the artifact as its own footer, sized by their content, and the
     *  page's conversation steps aside while the artifact is open. */
    bar?: Snippet;
  } = $props();
  let resolved = $derived(resolvePreview(target));
</script>

<aside
  class="ticket-artifact"
  data-ticket-artifact
  aria-label={resolved.label}
  tabindex="-1"
  bind:this={element}
  data-artifact-with-bar={bar ? "" : undefined}
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
    <div class="ticket-artifact-slot">
      <FileDocument retainWhileSelected {target} {reloadSignal} {registerArtifactFrame} />
    </div>
  </div>
  {#if bar}
    <div class="ticket-artifact-footer" data-artifact-bar>
      <div class="ticket-artifact-footer-column">{@render bar()}</div>
    </div>
  {/if}
</aside>

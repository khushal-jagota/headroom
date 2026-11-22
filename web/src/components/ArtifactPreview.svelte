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
        <button type="button" class="button button--ghost ticket-artifact-action" data-ticket-artifact-refresh onclick={onRefresh}>Refresh</button>
      {/if}
      <!-- THE button (app.css, "Buttons"): the page's own ghost, at its full height, rather than
           a small accent word in the corner. -->
      <button type="button" class="button button--secondary ticket-artifact-action" data-ticket-artifact-close onclick={onClose}>Close</button>
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

<style>
  /* The artifact reads in the conversation's column: its name, its way out and its document
     share the measure the dock below is centred on, rather than running flush to the frame's
     left edge while the dock sits in the middle. An HTML artifact or a player still fills the
     frame; only the bar and a Markdown document take the column. */
  .ticket-artifact {
    --artifact-column-inset: max(var(--space-5), calc((100% - var(--page-measure)) / 2));
  }
  .ticket-artifact-bar {
    min-height: calc(var(--button-height) + var(--space-2) * 2);
    box-sizing: border-box;
    padding-inline: var(--artifact-column-inset);
    background: var(--surface-1);
  }
  .ticket-artifact-name { color: var(--text-default); }
  /* The shared .ticket-artifact-action strip paints these as bare accent words; the button's
     own fills and tones are restated here so they hold whatever order the sheets load in. */
  .ticket-artifact-action {
    padding: 0 var(--button-pad-x);
    font-size: var(--type-button);
    font-weight: var(--weight-button);
  }
  .ticket-artifact-action.button--secondary {
    background: var(--button-secondary-fill);
    color: var(--tone-primary);
  }
  .ticket-artifact-action.button--ghost {
    background: transparent;
    color: var(--tone-primary);
  }
  .ticket-artifact-actions .ticket-artifact-action:hover {
    background: var(--button-secondary-fill-hover);
    color: var(--tone-strong);
  }
  .ticket-artifact-actions { gap: var(--space-2); }
  .ticket-artifact-slot :global(.file-preview-markdown-document) {
    box-sizing: border-box;
    padding: var(--space-6) var(--artifact-column-inset) var(--space-7);
  }
  /* A table here reads as the conversation's tables do: rows, not a grid. Hairlines between
     rows only, the heading set apart by weight and one firmer rule, and room in each cell. */
  .ticket-artifact-slot :global(.file-preview-markdown-document .markdown th),
  .ticket-artifact-slot :global(.file-preview-markdown-document .markdown td) {
    border: 0;
    border-block-end: var(--border-hairline) solid var(--border-color);
    padding: var(--space-2) var(--space-5) var(--space-2) 0;
  }
  .ticket-artifact-slot :global(.file-preview-markdown-document .markdown th) {
    background: transparent;
    border-block-end-color: var(--border-hover);
  }
  .ticket-artifact-slot :global(.file-preview-markdown-document .markdown tr:last-child > td) { border-block-end: 0; }
  .ticket-artifact-slot :global(.file-preview-markdown-document .markdown :is(th, td):last-child) { padding-inline-end: 0; }
</style>

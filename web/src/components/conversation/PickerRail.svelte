<script lang="ts">
  import type { Snippet } from "svelte";

  // The left half of a rail picker panel: the parts of the thing you can edit, with the
  // chosen part's choices on the right. Two pickers use it — the model picker, whose
  // parts are backends plus Reasoning, and the ceiling picker, whose parts are how far
  // the Worker may go and who reviews it. Rows come from `PickerRailRow.svelte`.
  let { label, attributes = {}, children }: {
    label: string;
    attributes?: Record<string, string | undefined>;
    children: Snippet;
  } = $props();
</script>

<div class="picker-rail" role="group" aria-label={label} {...attributes}>
  {@render children()}
</div>

<style>
  .picker-rail {
    display: flex; flex-direction: column;
    border-inline-end: var(--border-hairline) solid var(--border-color);
    padding-block: var(--space-1);
  }
  /* In the composer the rail is the left pane of the floating plane, set apart by its own
     slightly deeper tone rather than a rule. */
  :global([data-conversation-composer]) .picker-rail {
    border-inline-end: 0;
    padding: 6px;
    gap: 2px;
    background: rgb(0 0 0 / 14%);
  }
</style>

<script lang="ts">
  import type { Snippet } from 'svelte';
  let { itemKey, marker = false, assistantContinuation = false, tool = false, toolContinuation = false, children }: {
      itemKey: string;
      marker?: boolean;
      assistantContinuation?: boolean;
      tool?: boolean;
      toolContinuation?: boolean;
      children: Snippet;
  } = $props();
</script>
<!-- A row is as tall as what it contains. It carries no provisional height, because a
     height that is only right once the row has been looked at makes the thread's own
     size a function of where the reader has already been. -->
<div data-conversation-view-key={itemKey} class:marker class:assistant-continuation={assistantContinuation} class:tool class:tool-continuation={toolContinuation}>
  {@render children()}
</div>

<style>
  /* The thread's flex alignment for rows rendered through display: contents
     transcript containers. */
  div { display: flex; flex-direction: column; }
  /* An active flow skeleton temporarily owns the row's layout. Keep its children
     in the same flex column until it returns to display: contents after the fade. */
  div > :global(.skeleton-region[data-active]:not(.skeleton-region--fill):not(.skeleton-region--inline)) {
    display: flex;
    flex-direction: column;
  }
  /* A structural head takes no height and no share of the thread's flex gap. It
     requests its own bounded scope from the action inside it. */
  .marker { height: 0; margin-block-start: calc(var(--space-5) * -1); }
  /* These are the same relationships as the unwrapped transcript. They follow
     admitted logical neighbors, including when a neighbor's body is unmounted. */
  .assistant-continuation { position: relative; margin-block-start: var(--space-4); }
  .assistant-continuation::before {
    content: "";
    position: absolute;
    inset-block-start: calc(var(--space-3) * -1);
    inset-inline-start: 0;
    inline-size: var(--space-6);
    border-block-start: var(--border-hairline) solid var(--text-faintest);
    opacity: 0.55;
    pointer-events: none;
  }
  .tool {
    position: relative;
    min-width: 0;
    max-width: 100%;
    border-inline-start: var(--border-hairline) solid var(--border-color);
    margin-inline-start: var(--space-3);
    padding-inline-start: var(--space-3);
  }
  .tool-continuation { margin-block-start: calc(var(--space-1) - var(--space-5)); }
  .tool-continuation::before {
    content: "";
    position: absolute;
    inset-block-start: calc(var(--space-1) * -1);
    inset-inline-start: calc(var(--border-hairline) * -1);
    block-size: var(--space-1);
    border-inline-start: var(--border-hairline) solid var(--border-color);
    pointer-events: none;
  }
</style>

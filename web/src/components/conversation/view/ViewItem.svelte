<script lang="ts">
  import type { Snippet } from 'svelte';
  let { displayKey = null, registerDisplayNode, itemKey, rhythm = undefined, children }: {
      displayKey?: string | null;
      registerDisplayNode?: (key:string,node:HTMLElement)=>(()=>void);
      itemKey: string;
      /** Where this item stands in the thread's rhythm; the stylesheet turns it into the
       *  gap above it (.chat-thread-content > [data-conversation-transcript] > [data-rhythm]). Absent takes the block gap. */
      rhythm?: 'first' | 'turn' | 'meta' | 'block' | 'section' | 'row' | 'none';
      children: Snippet;
  } = $props();
  function register(node: HTMLElement, key: string | null) {
    let cleanup = key ? registerDisplayNode?.(key,node) : undefined;
    return { update(next: string | null) { cleanup?.(); cleanup = next ? registerDisplayNode?.(next,node) : undefined; }, destroy() { cleanup?.(); } };
  }
</script>
<!-- A row is as tall as what it contains. It carries no provisional height, because a
     height that is only right once the row has been looked at makes the thread's own
     size a function of where the reader has already been. -->
<div use:register={displayKey} data-conversation-view-key={itemKey} data-conversation-display-key={displayKey ?? undefined} data-rhythm={rhythm} class:marker={rhythm === 'none'}>
  {@render children()}
</div>

<style>
  /* The thread's flex alignment for rows rendered through display: contents
     transcript containers. */
  div { display: flex; flex-direction: column; min-width: 0; }
  /* An active flow skeleton temporarily owns the row's layout. Keep its children
     in the same flex column until readiness restores display: contents. */
  div > :global(.skeleton-region[data-active]:not(.skeleton-region--fill):not(.skeleton-region--inline)) {
    display: flex;
    flex-direction: column;
  }
  /* An item that draws nothing takes no height and no gap. It may still hold an action
     that asks for its own bounded scope. */
  .marker { height: 0; }
</style>

<script lang="ts">
  // The doing-surface: a real screen raised over the world.
  //
  // This component is a frame and nothing else. It gives the screen inside a
  // definite height — which is what `.ticket-screen { height: 100% }` needs, and
  // the reason the Workspace pane works — and it owns closing. What goes inside is
  // the app's own Ticket screen, Sprint Item screen, or conversation, unchanged.
  import type { Snippet } from "svelte";

  let {
    open,
    onClose,
    children
  }: {
    open: boolean;
    onClose: () => void;
    children: Snippet;
  } = $props();

  let panelElement = $state<HTMLElement | null>(null);

  // Escape belongs to the conversation first: it steps an opened conversation back
  // to peeked, then to rest. Only when nothing inside claimed it does Escape close
  // the panel, so the two never fight over the key.
  function onKeydown(event: KeyboardEvent): void {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    const pane = panelElement?.querySelector("[data-conversation-pane]");
    const conversationState = pane?.getAttribute("data-conversation-state");
    if (conversationState && conversationState !== "rest") return;
    onClose();
  }
</script>

<svelte:window onkeydown={onKeydown} />

{#if open}
  <aside class="atlas-panel" bind:this={panelElement} data-atlas-panel>
    <button type="button" class="atlas-panel-close" onclick={onClose} aria-label="Close">×</button>
    <div class="atlas-panel-body">
      {@render children()}
    </div>
  </aside>
{/if}

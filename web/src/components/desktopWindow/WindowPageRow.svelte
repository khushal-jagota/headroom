<script lang="ts">
  /** A page's top row (its context on the left, its actions on the right). In a browser it
   *  renders where it stands, unchanged. In the desktop window it is lifted onto the window's
   *  top row, where WindowPageRowSlot pins it while the page scrolls. */
  import type { Snippet } from "svelte";
  import { isDesktopWindow, liftPageRow } from "../../lib/desktopWindow/windowState.svelte";

  let { children }: { children: Snippet } = $props();
  const lifted = isDesktopWindow();
  $effect(() => {
    if (lifted) return liftPageRow(children);
  });
</script>

{#if !lifted}{@render children()}{/if}

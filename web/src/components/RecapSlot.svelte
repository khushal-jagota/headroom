<script lang="ts">
  /* The place a recap sits, and keeps sitting while the next one is written.
   *
   * Asking clears the line, so without this the page would jump twice: up when the words
   * go, and down again when the new ones land. The slot remembers how tall it was while
   * it had something to say and holds exactly that, so nothing below it moves.
   *
   * Empty is deliberate. The spinner beside the ask says a recap is coming, and a second
   * thing saying it here would be words standing where the recap goes.
   */
  import type { Snippet } from "svelte";

  let { pending, children }: { pending: boolean; children: Snippet } = $props();

  let element = $state<HTMLDivElement | null>(null);
  let heldHeight = $state(0);

  $effect(() => {
    if (pending || !element) return;
    const measured = element;
    const observer = new ResizeObserver(() => {
      if (measured.offsetHeight > 0) heldHeight = measured.offsetHeight;
    });
    observer.observe(measured);
    return () => observer.disconnect();
  });
</script>

<div
  bind:this={element}
  class="recap-slot"
  data-recap-slot
  style:min-height={pending && heldHeight ? `${heldHeight}px` : undefined}
>
  {#if pending}
    <span class="recap-slot-pending" data-recap-pending>writing a recap</span>
  {:else}
    {@render children()}
  {/if}
</div>

<style>
  .recap-slot { transition:min-height var(--motion-base) var(--motion-ease); }
  /* Said to anyone who cannot see the spinner, and to nobody who can. */
  .recap-slot-pending {
    position:absolute; width:1px; height:1px; overflow:hidden;
    clip-path:inset(50%); white-space:nowrap;
  }
</style>

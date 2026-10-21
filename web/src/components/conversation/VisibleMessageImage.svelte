<script lang="ts">
  import { observeVisibleContent, resetVisibleContentHeight } from '../../lib/visibleContent';
  let { src, alt, outgoing }: { src: () => string; alt: string; outgoing: boolean } = $props();
  let visible = $state(false);
  function visibility(element: HTMLElement, _identity: () => string) {
    const stop = observeVisibleContent(element, next => { visible = next; });
    return { destroy: stop, update() { resetVisibleContentHeight(element); } };
  }
</script>
<div data-conversation-image-slot use:visibility={src}>
  {#if visible}
    <img class="c2-piece-image" data-conversation-piece="image"
      data-conversation-piece-outgoing={outgoing ? "true" : undefined}
      src={src()} {alt} decoding="async" />
  {/if}
</div>
<style>
  .c2-piece-image { display: block; max-width: 100%; height: auto;
    margin-block: var(--space-2); border-radius: var(--radius-sm); }
</style>

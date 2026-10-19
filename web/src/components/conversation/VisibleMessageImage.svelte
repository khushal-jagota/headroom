<script lang="ts">
  import { observeVisibleContent } from '../../lib/visibleContent';
  let { src, alt, outgoing }: { src: () => string; alt: string; outgoing: boolean } = $props();
  let visible = $state(false);
  let loaded = $state(false);
  let height = $state(160);
  function visibility(element: HTMLElement) {
    return { destroy: observeVisibleContent(element, (next, measured) => {
      if (!next && measured > 0) height = measured;
      if (!next) loaded = false;
      visible = next;
    }) };
  }
</script>
<div data-conversation-image-slot use:visibility style:min-height={!visible || !loaded ? `${height}px` : undefined}>
  {#if visible}
    <img class="c2-piece-image" data-conversation-piece="image"
      data-conversation-piece-outgoing={outgoing ? "true" : undefined}
      src={src()} {alt} decoding="async" onload={() => { loaded = true; }} />
  {/if}
</div>
<style>
  .c2-piece-image { display: block; max-width: 100%; height: auto;
    margin-block: var(--space-2); border-radius: var(--radius-sm); }
</style>

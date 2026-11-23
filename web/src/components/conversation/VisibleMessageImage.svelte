<script lang="ts">
  import { observeVisibleContent, resetVisibleContentHeight } from '../../lib/visibleContent';
  import { openMediaViewer } from '../../lib/mediaViewer.svelte';
  let { src, alt, outgoing }: { src: () => string; alt: string; outgoing: boolean } = $props();
  let visible = $state(false);
  let naturallyWide = $state(false);
  function visibility(element: HTMLElement, _identity: () => string) {
    const stop = observeVisibleContent(element, next => { visible = next; });
    return { destroy: stop, update() { resetVisibleContentHeight(element); } };
  }
</script>
<!-- A picture in a message is the same picture box as an embedded one, and opens the viewer. -->
<div class="c2-piece-image-slot" data-conversation-image-slot use:visibility={src}>
  {#if visible}
    <button type="button" class="file-preview-media" data-media-open="image" aria-label={`Open ${alt}`}
      onclick={event => openMediaViewer({ href: src(), label: alt, kind: "image", downloadName: alt }, event.currentTarget)}>
      <img class="file-preview-image" class:file-preview-media--wide={naturallyWide} data-conversation-piece="image"
        data-conversation-piece-outgoing={outgoing ? "true" : undefined}
        src={src()} {alt} decoding="async"
        onload={event => { naturallyWide = (event.currentTarget as HTMLImageElement).naturalWidth >= 120; }} />
    </button>
  {/if}
</div>
<style>
  /* A picture is a block in its message: a block's gap from the words around it. In the
     person's message the column's 8 px stack spacing takes over (UserMessage). */
  .c2-piece-image-slot { margin-block: var(--cv-s2); }
  .c2-piece-image-slot :global(.file-preview-media:focus-visible) { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: var(--cv-focus-offset); }
  :global(.file-preview-grid) > .c2-piece-image-slot { margin: 0; min-width: 0; }
</style>

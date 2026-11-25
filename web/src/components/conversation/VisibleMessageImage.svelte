<script lang="ts">
  import { observeVisibleContent, resetVisibleContentHeight } from '../../lib/visibleContent';
  import { openMediaViewer } from '../../lib/mediaViewer.svelte';
  import { mediaFrame, revealWhenLoaded, type MediaSize } from '../../lib/mediaFrame';
  let { src, alt, outgoing, size = null, backdrop }: {
    src: () => string;
    alt: string;
    outgoing: boolean;
    /** The picture's recorded size. Without one, the picture is fitted into a fixed frame. */
    size?: MediaSize | null;
    /** This browser's own copy of a picture it sent, drawn under the stored one. */
    backdrop?: string;
  } = $props();
  let visible = $state(false);
  const frame = $derived(mediaFrame(size));
  function visibility(element: HTMLElement, _identity: () => string) {
    const stop = observeVisibleContent(element, next => { visible = next; });
    return { destroy: stop, update() { resetVisibleContentHeight(element); } };
  }
</script>
<!-- A picture in a message is the same picture box as an embedded one, and opens the viewer.
     The box is drawn before the picture comes into view, at the size it will have. -->
<div class="c2-piece-image-slot" data-conversation-image-slot use:visibility={src}>
  <button type="button" class="file-preview-media" data-media-open="image" aria-label={`Open ${alt}`}
    data-media-frame={frame.kind} style={frame.style}
    onclick={event => openMediaViewer({ href: src(), label: alt, kind: "image", downloadName: alt }, event.currentTarget)}>
    {#if backdrop}<img class="file-preview-backdrop" src={backdrop} alt="" aria-hidden="true" />{/if}
    {#if visible}
      <img class="file-preview-image" data-conversation-piece="image"
        data-conversation-piece-outgoing={outgoing ? "true" : undefined}
        src={src()} {alt} decoding="async" use:revealWhenLoaded />
    {/if}
  </button>
</div>
<style>
  /* A picture is a block in its message: a block's gap from the words around it. In the
     person's message the column's 8 px stack spacing takes over (UserMessage). */
  .c2-piece-image-slot { margin-block: var(--cv-s2); }
  .c2-piece-image-slot :global(.file-preview-media:focus-visible) { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: var(--cv-focus-offset); }
  :global(.file-preview-grid) > .c2-piece-image-slot { margin: 0; min-width: 0; }
</style>

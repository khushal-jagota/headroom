<script lang="ts">
  /* The expanded view of one embedded picture or video. There is no counter and no
   * stepping: the reader opens one thing, looks at it, and comes back to the sentence. */
  import { closeMediaViewer, mediaViewer } from "../lib/mediaViewer.svelte";

  let closeButton = $state<HTMLButtonElement>();
  let item = $derived(mediaViewer.item);

  $effect(() => {
    if (!item) return;
    const root = document.documentElement;
    root.classList.add("media-viewer-open");
    closeButton?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeMediaViewer(); }
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      root.classList.remove("media-viewer-open");
      window.removeEventListener("keydown", onKey, true);
    };
  });

  /** Only a press on the backdrop itself closes; the media and the bar keep theirs. */
  function backdrop(event: MouseEvent) {
    if (event.target === event.currentTarget) closeMediaViewer();
  }
</script>

{#if item}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div class="media-viewer" role="dialog" aria-modal="true" aria-label={item.label} tabindex="-1"
    data-media-viewer onclick={backdrop}>
    <div class="media-viewer-bar">
      <span class="media-viewer-name">{item.label}</span>
      <a class="media-viewer-button" href={item.href} download={item.downloadName} aria-label={`Download ${item.label}`}>
        <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 3.5v9M6 9l4 4 4-4M4.5 16h11" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </a>
      <button bind:this={closeButton} type="button" class="media-viewer-button" aria-label="Close" onclick={closeMediaViewer}>
        <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
      </button>
    </div>
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
    <div class="media-viewer-stage" onclick={backdrop}>
      {#if item.kind === "image"}
        <img class="media-viewer-media" src={item.href} alt={item.label} />
      {:else}
        <!-- svelte-ignore a11y_media_has_caption -->
        <video class="media-viewer-media" src={item.href} controls autoplay playsinline aria-label={item.label}></video>
      {/if}
    </div>
  </div>
{/if}

<style>
  :global(html.media-viewer-open), :global(html.media-viewer-open body) { overflow: hidden; }
  .media-viewer {
    position: fixed; inset: 0; z-index: var(--media-viewer-layer);
    display: grid; grid-template-rows: auto minmax(0, 1fr);
    background: var(--media-viewer-fill);
    backdrop-filter: blur(var(--media-viewer-blur));
    -webkit-backdrop-filter: blur(var(--media-viewer-blur));
    animation: media-viewer-in var(--motion-base) var(--motion-ease);
  }
  .media-viewer:focus { outline: 0; }
  @keyframes media-viewer-in { from { opacity: 0; } to { opacity: 1; } }
  @media (prefers-reduced-motion: reduce) { .media-viewer { animation: none; } }
  .media-viewer-bar { display: flex; align-items: center; gap: var(--space-3); padding: var(--space-3) var(--space-4); }
  .media-viewer-name {
    flex: 1; min-width: 0; color: var(--media-overlay-text); font-weight: var(--weight-title);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .media-viewer-button {
    display: inline-grid; place-items: center; flex: none;
    width: var(--media-viewer-button-size); height: var(--media-viewer-button-size);
    padding: 0; border: 0; border-radius: var(--radius-pill);
    background: var(--media-viewer-button-fill); color: var(--media-overlay-text); cursor: pointer;
  }
  .media-viewer-button:hover { background-image: var(--interaction-hover); }
  .media-viewer-button:focus { outline: 0; }
  .media-viewer-button:focus-visible { outline: var(--focus-width) solid var(--focus-ring); outline-offset: var(--focus-offset); }
  .media-viewer-button svg { width: calc(var(--space-5) - var(--space-1)); height: calc(var(--space-5) - var(--space-1)); }
  .media-viewer-stage { display: grid; place-items: center; min-height: 0; padding: var(--space-4) var(--space-7) var(--space-7); }
  .media-viewer-media {
    display: block; max-width: 100%; max-height: calc(100dvh - var(--media-viewer-chrome-height));
    width: auto; height: auto; object-fit: contain; border-radius: var(--radius-md);
  }
  @media (max-width: 600px) {
    .media-viewer-stage { padding: 0 0 var(--space-6); }
    .media-viewer-media { border-radius: 0; }
  }
</style>

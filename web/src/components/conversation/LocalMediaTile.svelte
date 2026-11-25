<script lang="ts">
  /** A picture or video just sent, drawn from the file in this browser until Panels stores
   *  it. It wears the same wrappers as the recorded picture or video, so it takes the same
   *  box, and the recorded one replaces it without a change of size. */
  import { mediaFrame } from "../../lib/mediaFrame";
  import type { LocalMediaPiece } from "../../lib/conversation/localSentMedia";
  let { media }: { media: LocalMediaPiece } = $props();
  const frame = $derived(mediaFrame(media.size));
</script>

{#snippet tile()}
  <div class="file-preview-media local-media-tile" class:file-preview-media--video={media.kind === "video"}
    data-media-frame={frame.kind} style={frame.style} data-local-media={media.kind}>
    {#if media.kind === "image"}
      <img class="file-preview-image" src={media.url} alt={media.name} />
    {:else}
      <video class="file-preview-video-poster" src={`${media.url}#t=0.1`} preload="metadata" muted playsinline
        tabindex="-1" aria-label={media.name}></video>
    {/if}
    {#if media.progress !== null}
      <span class="local-media-progress" role="progressbar" aria-label={`Uploading ${media.name}`}
        aria-valuemin="0" aria-valuemax="100" aria-valuenow={media.progress}>
        <span style={`inline-size: ${media.progress}%`}></span>
      </span>
    {/if}
  </div>
{/snippet}

{#if media.kind === "image"}
  <div class="c2-piece-image-slot">{@render tile()}</div>
{:else}
  <article class="conversation-file-card conversation-file-card--media" data-conversation-file-name={media.name}>
    <div class="file-preview file-preview--embedded">{@render tile()}</div>
  </article>
{/if}

<style>
  .local-media-tile { cursor: default; }
  /* Upload progress, quiet, along the foot of the picture. The bar goes when the upload
     ends; the message's status line says what happens next. */
  .local-media-progress {
    position: absolute;
    inset-inline: 8px;
    inset-block-end: 8px;
    block-size: 4px;
    border-radius: 999px;
    background: rgb(0 0 0 / 45%);
    overflow: hidden;
  }
  .local-media-progress > span {
    display: block;
    block-size: 100%;
    background: rgb(255 255 255 / 90%);
    transition: inline-size 120ms linear;
  }
  @media (prefers-reduced-motion: reduce) { .local-media-progress > span { transition: none; } }
</style>

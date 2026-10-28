<script lang="ts">
  import FilePreview from "../FilePreview.svelte";
  import { conversationFileTarget } from "../../lib/filePreview";

  let {
    href,
    fileName,
    mediaType,
    byteCount
  }: {
    href: string;
    fileName: string;
    mediaType: string;
    byteCount: number;
  } = $props();

  let target = $derived(conversationFileTarget(href, fileName));
  let playbackFailed = $state(false);
  let isVideo = $derived(mediaType.startsWith("video/"));

  function formattedBytes(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KiB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  }
</script>

{#if target}
  <article class="conversation-file-card" data-conversation-piece="file" data-conversation-file-name={fileName}>
    <div class="conversation-file-card__head">
      <span class="conversation-file-card__name">{fileName}</span>
      <span class="conversation-file-card__meta">{mediaType} · {formattedBytes(byteCount)}</span>
      <a class="conversation-file-card__download" {href} download={fileName}>Download</a>
    </div>
    {#if isVideo}
      <video controls preload="metadata" src={href} aria-label={fileName}
        onerror={() => (playbackFailed = true)} style="max-width: 100%; max-height: 65vh;">
        <track kind="captions" />
      </video>
      {#if playbackFailed}<p role="status">This video cannot play in this browser. Use Download to open it.</p>{/if}
    {:else}
      <FilePreview {target} mode="embedded" />
    {/if}
  </article>
{/if}

<style>
  .conversation-file-card {
    display: grid;
    gap: var(--space-2);
    margin-block: var(--space-2);
    padding: var(--space-3);
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-sm);
    background: var(--surface-recessed);
    min-width: 0;
  }
  .conversation-file-card__head {
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
    min-width: 0;
    font-family: var(--font-ui);
    font-size: var(--type-xs);
  }
  .conversation-file-card__name {
    color: var(--text-strong);
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .conversation-file-card__meta { color: var(--text-faint); }
  .conversation-file-card__download {
    margin-inline-start: auto;
    color: var(--accent-bright);
    white-space: nowrap;
  }
</style>

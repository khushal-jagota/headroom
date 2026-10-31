<script lang="ts">
  import EmbeddedMediaPreview from "../EmbeddedMediaPreview.svelte";
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
  $effect(() => { href; playbackFailed = false; });

  function formattedBytes(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KiB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  }
</script>

{#if target}
  <article class="conversation-file-card" data-conversation-piece="file" data-conversation-file-name={fileName}>
    {#if isVideo}
      <a class="file-preview-link file-preview-name-link" {href}>Open {fileName}</a>
      <a class="pv-act" {href} download={fileName}>Download</a>
      {#key href}<EmbeddedMediaPreview {href} label={fileName} kind="video" onerror={() => (playbackFailed = true)} />{/key}
      {#if playbackFailed}<p role="status">Use Download to open this video.</p>{/if}
    {:else}
      <FilePreview {target} mode="embedded" knownSizeLabel={formattedBytes(byteCount)} download={{href, fileName}} />
    {/if}
  </article>
{/if}

<style>
  .conversation-file-card { min-width: 0; margin-block: var(--space-2); }
  .conversation-file-card > .file-preview-name-link { display: inline-block; margin-block-end: var(--space-1); }
</style>

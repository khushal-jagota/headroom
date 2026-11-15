<script lang="ts">
  import FilePreview from "../FilePreview.svelte";
  import { conversationFileTarget } from "../../lib/filePreview";

  let {
    href,
    fileName,
    byteCount
  }: {
    href: string;
    fileName: string;
    byteCount: number;
  } = $props();

  let target = $derived(conversationFileTarget(href, fileName));

  function formattedBytes(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KiB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  }
</script>

{#if target}
  <article class="conversation-file-card" data-conversation-piece="file" data-conversation-file-name={fileName}>
    <FilePreview {target} mode="embedded" knownSizeLabel={formattedBytes(byteCount)} download={{href, fileName}} />
  </article>
{/if}

<style>
  .conversation-file-card { min-width: 0; margin-block: var(--space-2); }
</style>

<script lang="ts">
  import FilePreview from "../FilePreview.svelte";
  import { conversationFileTarget } from "../../lib/filePreview";
  import { attachmentByteLabel } from "./composer/attachments";

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
</script>

{#if target}
  <article class="conversation-file-card" data-conversation-piece="file" data-conversation-file-name={fileName}>
    <FilePreview {target} mode="embedded" knownSizeLabel={attachmentByteLabel(byteCount)} download={{href, fileName}} />
  </article>
{/if}

<style>
  .conversation-file-card { min-width: 0; margin-block: var(--space-2); }
</style>

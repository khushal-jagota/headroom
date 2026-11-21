<script lang="ts">
  /** What is attached and waiting to send: a picture as itself, a file by its kind, name and size.
   *  The strip the conversation composer draws, for the boxes that hold pictures and files. */
  import { releasePendingImages, type PendingConversationImage } from "../../lib/conversation/pendingImages";
  import type { PendingConversationFile } from "../../lib/conversation/pendingFiles";
  import { attachmentByteLabel, attachmentExtension } from "./composer/attachments";
  let { images = $bindable([]), files = $bindable([]), disabled = false }: {
    images?: PendingConversationImage[];
    files?: PendingConversationFile[];
    disabled?: boolean;
  } = $props();
  function removeImage(id: number): void {
    const removed = images.filter((image) => image.id === id);
    images = images.filter((image) => image.id !== id);
    releasePendingImages(removed);
  }
</script>

<div class="chat-attachments" aria-label="Attached and waiting to send">
  {#each images as image (image.id)}
    <div class="chat-att img review-attachment" title={`${image.fileName} · ${image.mediaType} · ${attachmentByteLabel(image.byteCount)}`}>
      <img src={image.previewUrl} alt={image.fileName} />
      <button type="button" class="chat-att-x" aria-label={`Remove ${image.fileName}`} title="Remove" {disabled} onclick={() => removeImage(image.id)}>×</button>
    </div>
  {/each}
  {#each files as file (file.id)}
    <div class="chat-att file review-attachment" title={`${file.fileName} · ${file.mediaType} · ${attachmentByteLabel(file.byteCount)}`}>
      <span class="chat-att-kind" aria-hidden="true">{attachmentExtension(file.fileName)}</span>
      <span class="chat-att-words">
        <span class="chat-att-name">{file.fileName}</span>
        <span class="chat-att-meta">{file.mediaType} · {attachmentByteLabel(file.byteCount)}</span>
      </span>
      <button type="button" class="chat-att-x" aria-label={`Remove ${file.fileName}`} title="Remove" {disabled} onclick={() => (files = files.filter((entry) => entry.id !== file.id))}>×</button>
    </div>
  {/each}
</div>

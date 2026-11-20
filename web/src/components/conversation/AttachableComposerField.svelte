<script lang="ts">
  /** The composer field with pictures and files: the + picks them, and pasting or dropping
   *  them works as in the conversation composer. The caller owns what is attached. */
  import ComposerField from "./ComposerField.svelte";
  import AttachmentChips from "./AttachmentChips.svelte";
  import { takePendingAttachments, ATTACHMENT_REJECTED } from "../../lib/conversation/pendingAttachments";
  import { CONVERSATION_FILE_ACCEPT, type PendingConversationFile } from "../../lib/conversation/pendingFiles";
  import type { PendingConversationImage } from "../../lib/conversation/pendingImages";
  import type { ComponentProps } from "svelte";

  let { value = $bindable(""), images = $bindable([]), files = $bindable([]), intakeBusy = $bindable(false), disabled = false, onError, ...field }: {
    value?: string;
    images?: PendingConversationImage[];
    files?: PendingConversationFile[];
    /** Files are being read: a decision waits for them. */
    intakeBusy?: boolean;
    disabled?: boolean;
    onError?: (error: unknown) => void;
  } & Omit<ComponentProps<typeof ComposerField>, "value" | "disabled" | "attach" | "attachments"> = $props();

  let fileInput = $state<HTMLInputElement | null>(null);
  let dragging = $state(false);
  let intakeError = $state("");

  async function intake(chosen: FileList | null | undefined): Promise<void> {
    if (!chosen || chosen.length === 0 || disabled || intakeBusy) return;
    intakeBusy = true; intakeError = "";
    try {
      const taken = await takePendingAttachments([...chosen], { images, files });
      images = taken.images; files = taken.files;
      if (taken.rejected) intakeError = ATTACHMENT_REJECTED;
    } catch (problem) {
      intakeError = problem instanceof Error ? problem.message : "The attachment could not be read.";
      onError?.(problem);
    } finally {
      intakeBusy = false;
      if (fileInput) fileInput.value = "";
    }
  }
  const hasFiles = (event: DragEvent) => Array.from(event.dataTransfer?.items ?? []).some((item) => item.kind === "file");
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  class="attachable"
  class:drag={dragging}
  ondragover={(event) => { if (disabled || !hasFiles(event)) return; event.preventDefault(); dragging = true; }}
  ondragleave={() => (dragging = false)}
  ondrop={(event) => { if (disabled || !hasFiles(event)) return; event.preventDefault(); dragging = false; void intake(event.dataTransfer?.files); }}
  onpaste={(event) => { const pasted = event.clipboardData?.files; if (disabled || !pasted || pasted.length === 0) return; event.preventDefault(); void intake(pasted); }}
>
  <input bind:this={fileInput} hidden type="file" multiple accept={`image/png,image/jpeg,image/gif,image/webp,${CONVERSATION_FILE_ACCEPT}`} onchange={() => void intake(fileInput?.files)} />
  <ComposerField {...field} bind:value {disabled}
    attach={{ label: "Attach a file", onclick: () => fileInput?.click(), attributes: { "data-review-attach": "" } }}
    attachments={images.length || files.length ? attached : undefined} />
  {#if intakeError}<p class="attachable-error" role="alert">{intakeError}</p>{/if}
</div>

{#snippet attached()}
  <AttachmentChips bind:images bind:files {disabled} />
{/snippet}

<style>
  .attachable.drag { outline: var(--focus-width) dashed var(--focus-ring); outline-offset: 2px; border-radius: var(--page-card-radius); }
  .attachable-error { margin: var(--space-1) 0 0; font-size: var(--type-sm); color: var(--text-danger, inherit); }
</style>

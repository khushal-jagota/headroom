<script lang="ts">
  import Button from './Button.svelte';
  import MessagePieces from './conversation/MessagePieces.svelte';
  import TranscriptStatusRow from './conversation/TranscriptStatusRow.svelte';
  import { attachmentByteLabel } from './conversation/composer/attachments';
  import type { PendingAttachment } from '../lib/conversation/videoUploads/contracts';
  import type { TranscriptStatus } from '../lib/conversation/transcript';
  import type { ReviewOutgoingReply, ReviewReplyComposer } from '../lib/review/replyComposer.svelte';

  let { composer, onEdit, busy = false }: {
    composer: ReviewReplyComposer;
    onEdit: () => void;
    busy?: boolean;
  } = $props();

  function statusFor(attempt: ReviewOutgoingReply): TranscriptStatus {
    const title = attempt.note ?? (attempt.status === 'checking' ? 'Checking the send…'
      : attempt.status === 'uploading' ? 'Uploading…'
      : attempt.status === 'waiting' ? 'Waiting to send…'
      : 'Sending…');
    return {
      title, detail: null,
      tone: attempt.status === 'failed' || attempt.status === 'upload-failed' ? 'error'
        : attempt.status === 'connection-failed' || attempt.status === 'uncertain' ? 'warning' : 'neutral'
    };
  }

  function uploadProgress(upload: PendingAttachment): string {
    switch (upload.phase) {
      case 'uploading': return `Uploading ${Math.floor(upload.receivedBytes / Math.max(1, upload.byteCount) * 100)}%`;
      case 'validating': return 'Checking file…';
      case 'ready': return attachmentByteLabel(upload.byteCount);
      default: return upload.error ?? 'Select this file again.';
    }
  }

  async function edit(attempt: ReviewOutgoingReply): Promise<void> {
    if (await composer.edit(attempt)) onEdit();
  }
</script>

{#each composer.attempts as attempt (attempt.message.messageId)}
  {@const status = statusFor(attempt)}
  {@const editAllowed = attempt.status === 'failed' || attempt.status === 'upload-failed'}
  {@const retryAllowed = composer.canRetry(attempt)}
  {@const uploads = attempt.pendingUploads ?? attempt.postedUploads ?? []}
  {@const content = uploads.length === 0 ? attempt.message.content
    : attempt.message.content.filter(piece => piece.piece !== 'attachment_upload' && piece.piece !== 'video_upload')}
  <article class="chat-u review-outgoing-reply" data-review-outgoing={attempt.message.messageId} data-conversation-outgoing={attempt.message.messageId}>
    {#snippet fileStatus()}
      {#each uploads as upload (upload.id)}
        <div class="review-outgoing-attachment">{upload.fileName} <span>· {uploadProgress(upload)}</span></div>
      {/each}
    {/snippet}
    {#snippet actions()}
      {#if retryAllowed}
        <Button variant="ghost" data-review-retry disabled={busy || composer.submitting} onclick={() => void composer.retry(attempt)}>Try Again</Button>
      {/if}
      {#if editAllowed}
        <Button variant="ghost" disabled={busy || composer.submitting || composer.intakeBusy} onclick={() => void edit(attempt)}>Edit</Button>
      {/if}
    {/snippet}
    <MessagePieces {content} conversationId={attempt.conversationId ?? ''} bubble
      failed={status.tone === 'error'} footer={uploads.length > 0 ? fileStatus : undefined} />
    <TranscriptStatusRow {status} align="end" role={status.tone === 'error' ? 'alert' : 'status'}
      actions={editAllowed || retryAllowed ? actions : undefined} />
  </article>
{/each}

<style>
  .review-outgoing-reply {
    --cv-bubble: var(--surface-raised);
    --cv-ink: var(--text-default);
    --cv-system-ink: var(--text-muted);
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: var(--cv-gap-meta);
    width: fit-content;
    max-width: 80%;
    margin-block-start: var(--space-4);
    margin-inline-start: auto;
  }
  .review-outgoing-attachment { color: var(--text-default); overflow-wrap: anywhere; }
  .review-outgoing-attachment span { color: var(--text-muted); font-size: var(--type-sm); }
</style>

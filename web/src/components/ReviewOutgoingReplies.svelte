<script lang="ts">
  import { setContext } from 'svelte';
  import Button from './Button.svelte';
  import UserMessage from './conversation/UserMessage.svelte';
  import HeldPromptStack from './conversation/composer/HeldPromptStack.svelte';
  import { heldPromptRows } from '../lib/conversation/heldPrompts';
  import { heldPromptIsInLens } from '../lib/conversation/lens';
  import { belongsInHeldQueue, TRANSCRIPT_SEND_ACTIONS, transcriptOutgoing,
    type TranscriptSendActions } from '../lib/conversation/transcriptOutgoing';
  import MessagePieces from './conversation/MessagePieces.svelte';
  import TranscriptStatusRow from './conversation/TranscriptStatusRow.svelte';
  import { attachmentByteLabel } from './conversation/composer/attachments';
  import { localMediaOf } from '../lib/conversation/localSentMedia';
  import type { PendingAttachment } from '../lib/conversation/videoUploads/contracts';
  import { messageNotDelivered, type TranscriptStatus } from '../lib/conversation/transcript';
  import type { ReviewOutgoingReply, ReviewReplyComposer } from '../lib/review/replyComposer.svelte';

  let { composer, onEdit, busy = false }: {
    composer: ReviewReplyComposer;
    onEdit: () => void;
    busy?: boolean;
  } = $props();

  const view = $derived(composer.queueView);
  const held = $derived(transcriptOutgoing([], [], heldPromptRows(
    (view?.held_prompts ?? []).filter(prompt => heldPromptIsInLens(prompt, 'focus', 'owner')), []
  ), 'owner', new Set()));
  const heldIds = $derived(new Set(held.map(message => message.messageId)));
  const attempts = $derived(composer.attempts.filter(attempt => !heldIds.has(attempt.message.messageId)));
  const queue = $derived(held.filter(belongsInHeldQueue));
  const pendingQueue = $derived(attempts.filter(queueAttempt));

  function queueAttempt(attempt: ReviewOutgoingReply): boolean {
    return attempt.message.mode === 'queue' && (attempt.status === 'queued'
      || attempt.message.queueWhilePending === true && (
        attempt.status === 'preparing' || attempt.status === 'uploading' || attempt.status === 'waiting'
        || attempt.status === 'sending' || attempt.status === 'checking'));
  }

  setContext<TranscriptSendActions>(TRANSCRIPT_SEND_ACTIONS, {
    running: () => view?.is_running === true,
    canSteer: () => Boolean(view?.supports_steer && !view.pending_permission_ask && !view.pending_user_input),
    canEditHeld: () => !busy && !composer.intakeBusy && !composer.submitting,
    discard: row => row.heldPromptId ? composer.discardHeld(row.heldPromptId) : undefined,
    promote: (row, mode) => row.heldPromptId ? composer.promoteHeld(row.heldPromptId, mode) : undefined,
    editHeld: async (row, words) => {
      if (row.heldPromptId && await composer.discardHeld(row.heldPromptId, words)) onEdit();
    }
  });

  function statusFor(attempt: ReviewOutgoingReply): TranscriptStatus {
    const title = attempt.note ?? (attempt.status === 'checking' ? 'Checking the send…'
      : attempt.status === 'uploading' ? 'Uploading…'
      : attempt.status === 'waiting' ? 'Waiting to send…'
      : 'Sending…');
    const failed = attempt.status === 'failed' || attempt.status === 'upload-failed';
    return {
      title: failed ? messageNotDelivered(attempt.note ?? "") : title, detail: null,
      tone: failed ? 'error'
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

{#snippet outgoingReply(attempt: ReviewOutgoingReply)}
  {@const status = statusFor(attempt)}
  {@const editAllowed = attempt.status === 'failed' || attempt.status === 'upload-failed'}
  {@const retryAllowed = composer.canRetry(attempt)}
  {@const uploads = attempt.pendingUploads ?? attempt.postedUploads ?? []}
  {@const media = uploads.map(localMediaOf)}
  {@const content = uploads.length === 0 ? attempt.message.content
    : [...media.filter(piece => piece !== null),
      ...attempt.message.content.filter(piece => piece.piece !== 'attachment_upload' && piece.piece !== 'video_upload')]}
  {@const statusLines = uploads.filter((upload, index) => media[index] === null || upload.phase === 'failed' || upload.phase === 'interrupted')}
  <article class="chat-u review-outgoing-reply" data-review-outgoing={attempt.message.messageId} data-conversation-outgoing={attempt.message.messageId}>
    {#snippet fileStatus()}
      {#each statusLines as upload (upload.id)}
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
      failed={status.tone === 'error'} footer={statusLines.length > 0 ? fileStatus : undefined} />
    <TranscriptStatusRow {status} align="end" role={status.tone === 'error' ? 'alert' : 'status'}
      actions={editAllowed || retryAllowed ? actions : undefined} />
  </article>
{/snippet}

<div data-review-outgoing-replies>
{#each attempts.filter(attempt => !queueAttempt(attempt)) as attempt (attempt.message.messageId)}
  {@render outgoingReply(attempt)}
{/each}
{#each held.filter(message => !belongsInHeldQueue(message)) as message (message.messageId)}
  <UserMessage local={message} conversationId={view?.conversation_id ?? ''} ownSenderLabel="owner"/>
{/each}
{#if queue.length || pendingQueue.length}
  <HeldPromptStack>
    {#each queue as message (message.messageId)}
      <UserMessage local={message} conversationId={view?.conversation_id ?? ''} ownSenderLabel="owner"/>
    {/each}
    {#each pendingQueue as attempt (attempt.message.messageId)}
      {@render outgoingReply(attempt)}
    {/each}
  </HeldPromptStack>
{/if}
</div>

<style>
  [data-review-outgoing-replies] { display: contents; }
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

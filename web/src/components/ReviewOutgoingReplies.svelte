<script lang="ts">
  import Button from './Button.svelte';
  import MessagePieces from './conversation/MessagePieces.svelte';
  import type { ReviewOutgoingReply, ReviewReplyComposer } from '../lib/review/replyComposer.svelte';

  let { composer, onEdit, busy = false }: {
    composer: ReviewReplyComposer;
    onEdit: () => void;
    busy?: boolean;
  } = $props();

  async function edit(attempt: ReviewOutgoingReply): Promise<void> {
    if (await composer.edit(attempt)) onEdit();
  }
</script>

{#each composer.attempts as attempt (attempt.message.messageId)}
  {@const failed = attempt.status === 'failed' || attempt.status === 'connection-failed'}
  <article class="review-outgoing-reply" data-review-outgoing={attempt.message.messageId}>
    <MessagePieces content={attempt.message.content} conversationId={attempt.conversationId ?? ''} />
    <div class="review-outgoing-status">
      <span role="status">{attempt.note ?? (attempt.status === 'checking' ? 'Checking the send…' : 'Sending…')}</span>
      {#if failed}
        <div class="review-outgoing-actions">
          <Button variant="ghost" disabled={busy || composer.submitting} onclick={() => void composer.retry(attempt)}>Try Again</Button>
          {#if attempt.status === 'failed'}
            <Button variant="ghost" disabled={busy || composer.submitting || composer.intakeBusy} onclick={() => void edit(attempt)}>Edit</Button>
          {/if}
        </div>
      {/if}
    </div>
  </article>
{/each}

<style>
  .review-outgoing-reply {
    margin-block-start: var(--space-4);
    padding: var(--space-3) var(--space-4);
    background: var(--surface-raised);
    border-radius: var(--radius-lg);
    overflow-wrap: anywhere;
  }
  .review-outgoing-status {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2) var(--space-3);
    align-items: center;
    justify-content: space-between;
    margin-block-start: var(--space-2);
    color: var(--text-muted);
    font-size: var(--type-sm);
  }
  .review-outgoing-actions { display: flex; flex-wrap: wrap; gap: var(--space-2); }
</style>

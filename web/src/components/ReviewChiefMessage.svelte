<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import ReviewReplyInput from './ReviewReplyInput.svelte';
  import ReviewOutgoingReplies from './ReviewOutgoingReplies.svelte';
  import ErrorLine from './ErrorLine.svelte';
  import { reviewReplyComposer } from '../lib/review/replyComposer.svelte';
  import { chiefDraftKey, chiefAttachmentDraftId, chiefAttemptKey, chiefAttemptMetadataKey, readChiefPointer } from '../lib/review/chiefMessage';

  let { onBusy }: { onBusy?: (busy: boolean) => void } = $props();
  const composer = reviewReplyComposer({
    draftKey: chiefDraftKey,
    attachmentDraftId: chiefAttachmentDraftId,
    owner: { kind: 'chief', id: 'chief' },
    destination: readChiefPointer,
    attemptKey: chiefAttemptKey,
    attemptMetadataKey: chiefAttemptMetadataKey
  });
  let voiceActive = $state(false);
  let reply = $state<ReviewReplyInput>();
  const sending = $derived(composer.attempts.some(attempt => attempt.status === 'sending' || attempt.status === 'checking'));
  // The send button shows how a send ended for a moment, then goes back to the arrow:
  // a green tick when it arrived, a red cross when it was refused. A refused message returns to the field.
  let result = $state<'sent' | 'failed'>();
  let refusal = $state<string>();
  let resultTimer: ReturnType<typeof setTimeout> | undefined;
  function show(next: 'sent' | 'failed', reason?: string): void {
    result = next;
    refusal = reason;
    clearTimeout(resultTimer);
    resultTimer = setTimeout(() => { result = undefined; refusal = undefined; }, next === 'sent' ? 1500 : 4000);
  }
  $effect(() => {
    if (composer.accepted) untrack(() => show('sent'));
  });
  $effect(() => {
    const refused = composer.attempts.find(attempt => attempt.status === 'failed' || attempt.status === 'upload-failed');
    if (!refused || composer.submitting || composer.intakeBusy) return;
    untrack(async () => {
      const reason = refused.note ?? 'The message was not sent.';
      if (await composer.edit(refused)) show('failed', `Not sent: ${reason}`);
    });
  });
  $effect(() => {
    const active = composer.submitting || composer.intakeBusy || voiceActive;
    untrack(() => onBusy?.(active));
  });
  onMount(() => {
    const unmount = composer.mount();
    return () => { unmount(); onBusy?.(false); clearTimeout(resultTimer); };
  });
</script>

<div data-review-chief-composer>
  <ReviewOutgoingReplies {composer} busy={voiceActive} quiet onEdit={() => reply?.focus()} />
  <ReviewReplyInput bind:this={reply} draftKey={composer.draftKey} uploadDestination={composer.uploadDestination}
          bind:uploads={composer.uploads} bind:text={composer.text} bind:files={composer.files} bind:images={composer.images}
    bind:intakeBusy={composer.intakeBusy} bind:voiceActive attachmentDisabled={!composer.canAttach}
    sendDisabled={!composer.ready} preserveHeight {sending} {result} sendTitle={refusal}
    placeholder="Message Chief…" label="Message Chief" sendLabel="Send message to Chief"
    onSend={() => composer.send()} onError={problem => composer.error = problem} />
  {#if composer.error}<div class="review-chief-error"><ErrorLine error={composer.error} /></div>{/if}
</div>

<style>
  .review-chief-error{padding-top:var(--space-2);color:var(--text-faint);font-size:var(--type-sm);line-height:1.5}
</style>

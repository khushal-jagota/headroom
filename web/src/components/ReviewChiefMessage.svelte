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
  // The send button turns to a green tick for a moment when a send arrives, then back to the arrow.
  let arrived = $state(false);
  let arrivedTimer: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    if (!composer.accepted) return;
    arrived = true;
    clearTimeout(arrivedTimer);
    arrivedTimer = setTimeout(() => arrived = false, 1500);
  });
  $effect(() => {
    const active = composer.submitting || composer.intakeBusy || voiceActive;
    untrack(() => onBusy?.(active));
  });
  onMount(() => {
    const unmount = composer.mount();
    return () => { unmount(); onBusy?.(false); clearTimeout(arrivedTimer); };
  });
</script>

<div data-review-chief-composer>
  <ReviewOutgoingReplies {composer} busy={voiceActive} quiet onEdit={() => reply?.focus()} />
  <ReviewReplyInput bind:this={reply} draftKey={composer.draftKey} uploadDestination={composer.uploadDestination}
          bind:uploads={composer.uploads} bind:text={composer.text} bind:files={composer.files} bind:images={composer.images}
    bind:intakeBusy={composer.intakeBusy} bind:voiceActive attachmentDisabled={!composer.canAttach}
    sendDisabled={!composer.ready} preserveHeight {sending} sent={arrived}
    placeholder="Message Chief…" label="Message Chief" sendLabel="Send message to Chief"
    onSend={() => composer.send()} onError={problem => composer.error = problem} />
  {#if composer.error}<div class="review-chief-error"><ErrorLine error={composer.error} /></div>{/if}
</div>

<style>
  .review-chief-error{padding-top:var(--space-2);color:var(--text-faint);font-size:var(--type-sm);line-height:1.5}
</style>

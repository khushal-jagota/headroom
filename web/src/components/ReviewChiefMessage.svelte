<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import ReviewReplyInput from './ReviewReplyInput.svelte';
  import ErrorLine from './ErrorLine.svelte';
  import { reviewReplyComposer } from '../lib/review/replyComposer.svelte';
  import { ReviewSendOutcome } from '../lib/review/sendOutcome.svelte';
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
  // The send button shows how a send ended; see ReviewSendOutcome.
  const outcome = new ReviewSendOutcome(composer);
  $effect(() => {
    const active = composer.submitting || composer.intakeBusy || voiceActive;
    untrack(() => onBusy?.(active));
  });
  onMount(() => {
    const unmount = composer.mount();
    return () => { unmount(); onBusy?.(false); };
  });
</script>

<div data-review-chief-composer>
  <ReviewReplyInput bind:this={reply} draftKey={composer.draftKey} uploadDestination={composer.uploadDestination}
          bind:uploads={composer.uploads} bind:text={composer.text} bind:files={composer.files} bind:images={composer.images}
    bind:intakeBusy={composer.intakeBusy} bind:voiceActive attachmentDisabled={!composer.canAttach}
    sendDisabled={!composer.ready} preserveHeight sending={outcome.sending} result={outcome.result} sendTitle={outcome.note}
    placeholder="Message Chief…" label="Message Chief" sendLabel="Send message to Chief"
    onSend={() => composer.send(0)} onError={problem => composer.error = problem} />
  {#if composer.error}<div class="review-chief-error"><ErrorLine error={composer.error} /></div>{/if}
</div>

<style>
  .review-chief-error{padding-top:var(--space-2);color:var(--text-faint);font-size:var(--type-sm);line-height:1.5}
</style>

<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import ReviewReplyInput from './ReviewReplyInput.svelte';
  import ReviewOutgoingReplies from './ReviewOutgoingReplies.svelte';
  import ErrorLine from './ErrorLine.svelte';
  import { workspaceAddress } from '../lib/workspaceAddress';
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
  <ReviewOutgoingReplies {composer} busy={voiceActive} onEdit={() => reply?.focus()} />
  <ReviewReplyInput bind:this={reply} draftKey={composer.draftKey} uploadDestination={composer.uploadDestination}
          bind:uploads={composer.uploads} bind:text={composer.text} bind:files={composer.files} bind:images={composer.images}
    bind:intakeBusy={composer.intakeBusy} bind:voiceActive attachmentDisabled={!composer.canAttach}
    sendDisabled={!composer.ready} preserveHeight
    placeholder="Message Chief…" label="Message Chief" sendLabel="Send message to Chief"
    onSend={() => composer.send()} onError={problem => composer.error = problem} />
  <div class="review-chief-receipt">
    {#if composer.error}<ErrorLine error={composer.error} />
    {:else if composer.accepted}<a href={workspaceAddress({ kind: 'chief' })}>Open Chief</a>{/if}
  </div>
</div>

<style>
  .review-chief-receipt{min-height:var(--space-7);display:flex;align-items:flex-start;justify-content:space-between;gap:var(--space-3);padding-top:var(--space-2);color:var(--text-faint);font-size:var(--type-sm);line-height:1.5}
  .review-chief-receipt a{flex:none;color:var(--text-faint);text-underline-offset:var(--space-1)}
</style>

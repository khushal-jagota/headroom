<script lang="ts">
  import { onMount } from 'svelte';
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
  const note = $derived(composer.accepted?.fate === 'queued' ? 'Queued for Chief'
    : composer.accepted?.fate === 'injected' ? 'Sent to Chief’s current turn'
    : composer.accepted?.fate === 'started' ? 'Sent to Chief' : '');
  $effect(() => onBusy?.(composer.submitting || composer.intakeBusy || voiceActive));
  onMount(() => {
    const unmount = composer.mount();
    return () => { unmount(); onBusy?.(false); };
  });
</script>

<div data-review-chief-composer>
  <ReviewOutgoingReplies {composer} busy={voiceActive} onEdit={() => reply?.focus()} />
  <ReviewReplyInput bind:this={reply} bind:text={composer.text} bind:files={composer.files} bind:images={composer.images}
    bind:intakeBusy={composer.intakeBusy} bind:voiceActive attachmentDisabled={composer.intakeBusy || !composer.ready}
    sendDisabled={composer.submitting || !composer.ready} preserveHeight
    placeholder="Message Chief…" label="Message Chief" sendLabel="Send message to Chief"
    onSend={() => composer.send()} onError={problem => composer.error = problem} />
  <div class="review-chief-receipt">
    {#if composer.error}<ErrorLine error={composer.error} />
    {:else if note}<span role="status">{note}</span><a href={workspaceAddress({ kind: 'chief' })}>Open Chief</a>{/if}
  </div>
</div>

<style>
  .review-chief-receipt{min-height:var(--space-7);display:flex;align-items:flex-start;justify-content:space-between;gap:var(--space-3);padding-top:var(--space-2);color:var(--text-faint);font-size:var(--type-sm);line-height:1.5}
  .review-chief-receipt a{flex:none;color:var(--text-faint);text-underline-offset:var(--space-1)}
</style>

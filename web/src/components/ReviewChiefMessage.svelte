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
  // The send button shows how a send ended, then goes back to the arrow: a green tick when it
  // arrived, a red cross when it was refused, an amber question mark when it may not have arrived.
  // A refused or uncertain message returns to the field. Each result leaves after a moment, or sooner
  // when the words change or the next send starts.
  let result = $state<'sent' | 'failed' | 'unsure'>();
  let note = $state<string>();
  let resultTimer: ReturnType<typeof setTimeout> | undefined;
  let unsureText = '';
  function show(next: 'sent' | 'failed' | 'unsure', reason?: string): void {
    result = next;
    note = reason;
    clearTimeout(resultTimer);
    if (next === 'unsure') unsureText = composer.text;
    resultTimer = setTimeout(() => { result = undefined; note = undefined; }, next === 'sent' ? 1500 : 4000);
  }
  $effect(() => {
    if (composer.accepted) untrack(() => show('sent'));
  });
  $effect(() => {
    const ended = composer.attempts.find(attempt => attempt.status === 'failed' || attempt.status === 'upload-failed'
      || attempt.status === 'connection-failed' || attempt.status === 'uncertain');
    if (!ended || composer.submitting || composer.intakeBusy) return;
    untrack(async () => {
      const unsure = ended.status === 'connection-failed' || ended.status === 'uncertain';
      const reason = ended.note ?? 'The message was not sent.';
      if (!await composer.edit(ended)) return;
      if (unsure) show('unsure', `May not have arrived: ${reason} Sending it again can deliver it twice.`);
      else show('failed', `Not sent: ${reason}`);
    });
  });
  $effect(() => {
    if (result === 'unsure' && (sending || composer.text !== unsureText)) untrack(() => { result = undefined; note = undefined; });
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
    sendDisabled={!composer.ready} preserveHeight {sending} {result} sendTitle={note}
    placeholder="Message Chief…" label="Message Chief" sendLabel="Send message to Chief"
    onSend={() => composer.send()} onError={problem => composer.error = problem} />
  {#if composer.error}<div class="review-chief-error"><ErrorLine error={composer.error} /></div>{/if}
</div>

<style>
  .review-chief-error{padding-top:var(--space-2);color:var(--text-faint);font-size:var(--type-sm);line-height:1.5}
</style>

<script lang="ts">
  import Button from "./Button.svelte";
  import WindowPageRow from "./desktopWindow/WindowPageRow.svelte";
  import { onMount, untrack, type Snippet } from 'svelte';
  import { createQuery } from '@tanstack/svelte-query';
  import type { ReviewMessageItem } from '../lib/types';
  import { queries } from '../lib/queryCatalogue';
  import { selectedRecap } from '../lib/navigation/views/recap.svelte';
  import { readConversation, readEventsAfter, advanceOwnerRead, messageContentOf } from '../lib/conversation/wire';
  import ReviewReplyInput from './ReviewReplyInput.svelte';
  import ReviewOutgoingReplies from './ReviewOutgoingReplies.svelte';
  import { reviewReplyComposer } from '../lib/review/replyComposer.svelte';
  import { unreadOwnerMessages, reviewMessageHref, draftStorageKey, attemptStorageKey, type OwnerMessage } from '../lib/review/messages';
  import MessagePieces from './conversation/MessagePieces.svelte';
  import MarkdownBlock from './MarkdownBlock.svelte';
  import ClampedText from './ClampedText.svelte';
  import RequestRecapAction from './RequestRecapAction.svelte';
  import SkeletonRegion from './SkeletonRegion.svelte';
  import ErrorLine from './ErrorLine.svelte';

  let { item, onSkip, onResolved, onBusy, footer, decisionActions, embedded=false, replyOnly=false }: {
    /** The queue's Skip / Open ticket for the Ticket this message belongs to, in the card's action row. */
    decisionActions?:Snippet;
    embedded?:boolean;
    /** Only the reply and its row, for the bar under an artifact opened from this message. */
    replyOnly?:boolean;
    item:ReviewMessageItem; onSkip:()=>void; onResolved:(through:number)=>void;
    onBusy?:(busy:boolean,conversationId:string)=>void; footer?:Snippet;
  } = $props();
  // The route keys this component by conversation, never by changing attention facts.
  const conversationId = untrack(() => item.conversation_id);
  const composer = reviewReplyComposer({
    draftKey: draftStorageKey(conversationId),
    attachmentDraftId: conversationId,
    owner: untrack(() => ({ kind: item.owner_kind, id: item.owner_kind === 'chief' ? 'chief' : item.owner_id })),
    destination: () => conversationId,
    attemptKey: attemptStorageKey(conversationId),
    attemptMetadataKey: `panels:review-reply-attempts:${conversationId}`,
    legacyConversationId: conversationId
  });
  let loaded = $state(false);
  let events = $state.raw<OwnerMessage[]>([]);
  let error = $state<unknown>(null);
  let markingRead = $state(false);
  let leaving = $state(false);
  let destroyed = false;
  let voiceActive = $state(false);
  let root = $state<HTMLElement>();
  let reply = $state<ReviewReplyInput>();
  let busy = $derived(markingRead || composer.submitting);
  let intakeBusy = $derived(composer.intakeBusy);
  let ownerReadThrough = $state<number | null>(null);
  let through = $derived(events.at(-1)?.sequence ?? ownerReadThrough ?? 0);
  const chief = createQuery(() => ({ ...queries.chiefSettings(), enabled: item.owner_kind === 'chief' }));
  const recapBody = selectedRecap(() => item.recap_reference, () => true);
  let recap = $derived(item.owner_kind === 'chief' ? chief.data?.recap ?? '' : recapBody.body ?? item.recap);
  $effect(() => {
    const active = busy || intakeBusy || voiceActive;
    untrack(() => onBusy?.(active, conversationId));
  });
  $effect(() => {
    const settled = composer.accepted?.through ?? (events.length === 0 ? ownerReadThrough : null);
    if (settled !== null && composer.ready && !busy && !intakeBusy && !voiceActive && !composer.hasDraft && !composer.hasUnsettled) resolve(settled);
  });

  async function load(): Promise<void> {
    error = null;
    try {
      const view = await readConversation(conversationId);
      const rows = await readEventsAfter(conversationId, view.owner_read_through_sequence);
      if (destroyed) return;
      events = unreadOwnerMessages(rows, view.owner_read_through_sequence);
      loaded = true;
      ownerReadThrough = view.owner_read_through_sequence;
    } catch (problem) { if (!destroyed) error = problem; }
  }

  onMount(() => {
    const unmount = composer.mount();
    void load();
    return () => { destroyed = true; unmount(); onBusy?.(false, conversationId); };
  });

  function resolve(bound: number): void {
    // With the artifact open its bar is the active copy of this card.
    if (destroyed || leaving || root?.closest('[inert]')) return;
    leaving = true;
    onResolved(bound);
  }

  async function markRead(): Promise<void> {
    if (busy || !loaded || !through || voiceActive) return;
    markingRead = true;
    error = null;
    try {
      const bound = through;
      await advanceOwnerRead(conversationId, bound);
      resolve(bound);
    } catch (problem) { error = problem; }
    finally { markingRead = false; }
  }
</script>

<div bind:this={root} data-review-card data-review-item-type="message" data-artifact-origin={`message:${conversationId}`} data-conversation-id={conversationId} data-message-identity={`message:${conversationId}`} data-review-leaving={leaving} data-review-loaded={loaded}>
  {#snippet ownActions()}
    {#if embedded}<div class="review-ticket-decision-line review-ticket-decision-line--inline">
      <Button variant="ghost" data-open-conversation href={reviewMessageHref(item)} aria-disabled={busy} onclick={(event: MouseEvent)=>{if(busy)event.preventDefault();}}>Open conversation</Button>
    </div>{/if}
    {@render decisionActions?.()}
  {/snippet}
  {#if !embedded}
  <!-- Skip and Open conversation stand in the page's top band at the right, as staging. -->
  <WindowPageRow>
  <div class="review-eyebrow review-eyebrow--decisions">
    <div class="review-ticket-decision-line review-ticket-decision-line--top" data-review-top-decisions data-window-row-actions>
      <Button variant="ghost" data-open-conversation href={reviewMessageHref(item)} aria-disabled={busy} onclick={(event: MouseEvent)=>{if(busy)event.preventDefault();}}>Open conversation</Button>
      <Button variant="ghost" data-skip disabled={busy || intakeBusy || voiceActive} onclick={onSkip}>Skip</Button>
    </div>
  </div>
  </WindowPageRow>
  <h1 class="review-ticket-title">{item.title}</h1>
  {#if recap}<ClampedText class="review-recap" contentKey={recap}><MarkdownBlock text={recap}/></ClampedText>{/if}
  <div class="review-recap-request"><RequestRecapAction kind={item.owner_kind} id={item.owner_id}/></div>
  {/if}
  <!-- The bar under an artifact holds no placeholder: it appears when the reply can be written. -->
  <SkeletonRegion shape="block" loading={!loaded && !error && !replyOnly} loadingText="Loading messages...">
    {#if loaded}
      <div class="review-message-card" class:review-message-card--reply-only={replyOnly}>
        {#if !replyOnly}<div class="review-message-run">
          {#each events as event (event.sequence)}<div class="review-message-said" data-review-message-sequence={event.sequence}><MessagePieces content={messageContentOf(event.payload)} {conversationId}/></div>{/each}
        </div>{/if}
        <ReviewOutgoingReplies {composer} busy={voiceActive || markingRead} onEdit={() => reply?.focus()} />
        <ReviewReplyInput bind:this={reply} draftKey={composer.draftKey} uploadDestination={composer.uploadDestination}
          bind:uploads={composer.uploads} bind:text={composer.text} bind:files={composer.files} bind:images={composer.images}
          bind:intakeBusy={composer.intakeBusy} bind:voiceActive attachmentDisabled={!composer.attachmentReady || markingRead}
          sendDisabled={markingRead || !composer.ready || !through} onSend={() => composer.send(through)} onError={problem => composer.error = problem}/>
        <div class="review-read-row"><div class="review-card-utilities">{@render ownActions()}</div><Button variant="ghost" class="review-card-main" data-review-mark-read disabled={busy || intakeBusy || !through || voiceActive} onclick={()=>void markRead()}>Mark read</Button></div>
        {#if error || composer.error}<ErrorLine error={error ?? composer.error}/>{/if}
      </div>
    {:else if error}<ErrorLine {error}/><div class="review-read-row"><div class="review-card-utilities">{@render ownActions()}</div><Button variant="secondary" class="review-card-main" onclick={()=>void load()}>Retry</Button></div>{/if}
  </SkeletonRegion>
  <!-- Until the card has drawn, the actions wait below it rather than vanish. -->
  {#if !loaded && !error && !replyOnly}<div class="review-read-row"><div class="review-card-utilities">{@render ownActions()}</div></div>{/if}
</div>
{@render footer?.()}

<style>
  /* Every button row sits at the right, the most important action furthest right: the actions
     about the queue, 16px, then Mark read (or Retry). */
  .review-card-utilities{display:flex;flex-wrap:wrap;justify-content:flex-end;align-items:center;gap:var(--space-2);min-width:0}
  .review-card-utilities :global(.review-ticket-decision-line){flex-wrap:wrap;justify-content:flex-end}
  .review-read-row:has(:global(.review-card-main)) .review-card-utilities{margin-right:calc(var(--space-4) - var(--space-2))}
  /* Skip and Open conversation stand in the page's top band (as staging, owner's choice); the
     card's own row keeps Mark read at the right, and Open conversation when the card sits inside
     a Ticket's item. */
  .review-eyebrow--decisions{justify-content:flex-end}
  /* On a desktop the quiet buttons' words line up with the column's edge; on a phone the column
     has no room past its edge, so they stay inside it. */
  .review-ticket-decision-line--top{padding:0;margin-right:calc(var(--space-3) * -1)}
  @media (max-width:720px){.review-ticket-decision-line--top{margin-right:0}}
  .review-message-card{margin-top:var(--space-5);padding:var(--space-5);background:var(--surface-2);border-radius:var(--radius-lg)}
  /* Under an artifact the reply stands bare, like the composer it takes the place of. */
  .review-message-card--reply-only{margin-top:0;padding:0;background:none;border-radius:0}
  .review-message-card--reply-only :global(.review-reply){margin-top:0}
  .review-message-run{font-family:var(--font-ui);font-size:var(--type-md);line-height:1.68}
  .review-message-said+.review-message-said{position:relative;margin-top:var(--space-5);padding-top:var(--space-4)}
  .review-message-said+.review-message-said::before{content:"";position:absolute;inset-block-start:0;inset-inline-start:0;inline-size:var(--space-6);border-block-start:var(--border-hairline) solid var(--text-faintest);opacity:.55}
  .review-recap-request{display:flex;justify-content:flex-end;margin-top:var(--space-2);font-size:var(--type-sm)}
  /* Where the row does not fit, its buttons wrap at the right; none is ever cut off. Mark
     read keeps staging's 37px height. */
  .review-read-row{display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-end;gap:var(--space-2);margin-top:var(--space-3)}
  .review-read-row :global(.review-card-main.button){height:auto;min-height:37px;line-height:1.6}
</style>

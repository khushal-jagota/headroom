<script lang="ts">
  import Button from "../components/Button.svelte";
  import { tick, onDestroy, untrack } from "svelte";
  import type { ManagedFileTarget } from "../lib/filePreview";
  import { createArtifactOnThePage } from "../lib/artifactOnThePage.svelte";
  import ArtifactPreview from "../components/ArtifactPreview.svelte";
  import { navigationApplication } from "../lib/navigation/application.svelte";
  import { navigationWindow, reviewProposalIdentity } from "../lib/navigation/presentation";

  import { workspaceAddress } from "../lib/workspaceAddress";
  import type { NavigationReviewItem as ReviewItem } from "../lib/types";
  import { viewWindow } from "../lib/navigation/views/owner";
  import ResourceState from "../components/ResourceState.svelte";
  import ReviewTicketCard from "../components/ReviewTicketCard.svelte";
  import ReviewProposalCard from "../components/ReviewProposalCard.svelte";
  import ReviewMessageCard from "../components/ReviewMessageCard.svelte";
  import ReviewFinalState from "../components/ReviewFinalState.svelte";
  import { reviewMessageHref } from "../lib/review/messages";
  import {reviewHistoryKey,reviewReturnLabel,sameReviewHistory} from "../lib/review/return";
  import {shownReviewItem} from "../lib/review/shown";
  import type {ReviewHistoryKey} from "../lib/navigation/contracts";

  const navigation = (() => { const value = navigationApplication(); if (!value) throw new Error("Review requires the application navigation owner"); return value; })();
  let displayed = $state.raw<import("../lib/navigation/knowledge").EffectiveNavigationView | null>(null);
  $effect(() => { const view = navigation.selectors.effective; if (view) displayed = view; });
  let review = $derived(displayed ? navigation.presentationFor(displayed).review : null);

  let reviewAnchor=$state<string|null>(null);
  let queueWindow=$derived(navigationWindow(displayed?navigation.presentationFor(displayed):undefined,'review'));
  let reviewRequest=$state.raw(navigation.owner.getRequest());
  function updateReviewDemand():void {
    navigation.owner.setDemand('review',{
      review_skipped_keys:skippedHistory,review_priority_keys:returnCohort,
      review_conversation_ids:heldCard ? heldCard.review_item_type==='message' ? [heldCard.conversation_id] : heldCard.messages.map(message=>message.conversation_id) : [],
      windows:[viewWindow('review',null,{anchor:reviewAnchor,limit:30})]});
    reviewRequest=navigation.owner.getRequest();
  }
  $effect(()=>{try {updateReviewDemand();} catch(error) {
    // A refused continuation cannot reach another card or the terminal window.
    // Keep the complete history and expose the existing final-screen refusal.
    returnError=error;
    finalLatched=true;
  }});
  onDestroy(()=>navigation.owner.setDemand('review',undefined));
  let skipped = $state<Record<string, boolean>>({});
  let skippedHistory=$state<ReviewHistoryKey[]>([]);
  let returnCohort=$state<ReviewHistoryKey[]>([]);
  // The note and edited text of the proposal on screen. The card and the bar under its
  // artifact are one decision, so they read and write the same draft.
  let proposalDraft=$state<{identity:string;text:string;editedBody?:string}|null>(null);
  // Proposals decided on this screen. The decided card leaves at once: the queue's own
  // refresh confirms it later, and nothing waits on that read.
  let decidedProposals=$state<Record<string,boolean>>({});
  let returnLabel=$derived.by(()=>{void reviewRequest;return reviewReturnLabel(displayed,navigation.owner.getRequest(),skippedHistory);});
  // The route's own element. The approve shortcut's lookup is scoped to it, so a
  // card mounted by another screen at the same time can never be the one a Review
  // keystroke reaches.
  let screenElement = $state<HTMLElement | null>(null);

  function itemKey(item: ReviewItem): string {
    return item.review_item_type === 'message' ? `message:${item.conversation_id}` : `ticket:${item.ticket_id}`;
  }
  /** The item as this screen knows it: a proposal decided here is gone from its card, and a
   *  card with nothing left is gone from the queue. */
  function undecided(item: ReviewItem): ReviewItem | null {
    if (item.review_item_type !== 'ticket' || !item.proposal || !decidedProposals[reviewProposalIdentity(item.proposal)]) return item;
    return item.messages.length ? { ...item, proposal: null } : null;
  }

  let items = $derived((queueWindow ? queueWindow.members.flatMap(key=>(review?.items ?? []).filter(item=>key.kind===(item.review_item_type === 'message' ? 'address' : 'ticket') && key.id===(item.review_item_type === 'message' ? item.address_id : item.ticket_id))) : review?.items ?? [])
    .map(undecided).filter((item): item is ReviewItem => item !== null));
  let runningWorkerCount = $derived(displayed?.runtimeCurrent ? review?.runningConversationCount ?? null : null);
  let waitingCount = $derived(displayed?.runtimeCurrent ? navigation.presentationFor(displayed).globalCounts?.review ?? null : null);
  let finalLatched = $state(false);
  let returning = $state(false);
  let returnError = $state<unknown>(null);
  let busyConversations = $state<Record<string,boolean>>({});
  let messageGenerations = $state<Record<string,number>>({});
  // Keep a painted conversation through delivery uncertainty and its exit. Owner
  // state and canonical address still govern admission, even for a retained draft.
  let heldCard = $state<ReviewItem|null>(null);
  // The card on screen. Chosen when the previous card leaves, then kept by id.
  // A refresh that inserts older work leaves this card in place.
  let pinnedKey=$state<string|null>(null);
  function advancePast(item:ReviewItem):void {
    const key=itemKey(item);
    const next=items.find(candidate=>itemKey(candidate)!==key && !skipped[itemKey(candidate)]);
    pinnedKey=next ? itemKey(next) : null;
  }
  function messageEligible(message:import('../lib/types').ReviewMessageItem):boolean {
    if(!displayed)return false;
    const presentation=navigation.presentationFor(displayed);
    const context=displayed.publication.view?.reviewContext?.find(row=>row.conversation_id===message.conversation_id);
    // New demand hands over atomically. Until its context arrives, the admitted
    // canonical address from the complete Review card remains authoritative.
    const canonicalId=context ? context.canonical_address_id
      : [...(presentation.addressesByConversation.get(message.conversation_id) ?? [])]
        .sort((a,b)=>Number(b.value.current)-Number(a.value.current) || (a.id<b.id ? -1 : a.id>b.id ? 1 : 0))[0]?.id;
    if((context || canonicalId!==undefined) && canonicalId!==message.address_id)return false;
    if(message.owner_kind==='ticket') {
      const owner=presentation.rawTicketsById.get(message.owner_id)?.value;
      return owner ? owner.stage!=='done' && !owner.is_backlogged : context===undefined;
    }
    if(message.owner_kind==='manager') {
      const owner=displayed.publication.records.find(record=>record.kind==='manager' && record.id===message.owner_id);
      return owner?.kind==='manager' ? !owner.value.settled : context===undefined;
    }
    return true;
  }
  let reviewReady = $derived(Boolean(review));
  let currentItem = $derived.by<ReviewItem | null>(() => {
    if (!reviewReady) return null;
    if(heldCard && !skipped[itemKey(heldCard)]) {
      const current=items.find(item=>itemKey(item)===itemKey(heldCard!));
      if(heldCard.review_item_type==='message') {
        if(messageEligible(heldCard))return current ?? heldCard;
      } else {
        const held=heldCard.messages.filter(messageEligible);
        if(held.length) {
          const fresh=current?.review_item_type==='ticket' ? current : null;
          return {...(fresh ?? heldCard),proposal:fresh?.proposal ?? null,
            messages:[...(fresh?.messages ?? []),...held.filter(message=>!fresh?.messages.some(row=>row.conversation_id===message.conversation_id))]};
        }
      }
    }
    const waiting=items.filter(item=>!skipped[itemKey(item)]);
    return shownReviewItem(waiting,pinnedKey,itemKey).item;
  });
  let messageBusy = $derived(currentItem?.review_item_type==='message' ? Boolean(busyConversations[currentItem.conversation_id]) : Boolean(currentItem?.messages.some(message=>busyConversations[message.conversation_id])));
  // Only a completed last window is terminal. An exhausted intermediate window
  // keeps the existing pagination path, and loading never admits the final screen.
  let terminalWindow = $derived(Boolean(reviewReady && displayed?.runtimeCurrent && queueWindow
    && queueWindow.request.anchor === reviewAnchor && !queueWindow.next && !currentItem));
  let showFinal = $derived(finalLatched || !returning && terminalWindow);
  $effect(() => {
    if (!returning && terminalWindow) finalLatched = true;
  });
  $effect(()=>{
    if (!reviewReady || finalLatched) return;
    if(!heldCard && currentItem && (currentItem.review_item_type==='message' ? messageEligible(currentItem) : currentItem.messages.some(messageEligible)))heldCard=currentItem;
    else if(heldCard?.review_item_type==='ticket' && currentItem?.review_item_type==='ticket' && heldCard.ticket_id===currentItem.ticket_id
      && currentItem.messages.some(message=>!heldCard || heldCard.review_item_type!=='ticket' || !heldCard.messages.some(held=>held.conversation_id===message.conversation_id && held.address_id===message.address_id)))heldCard=currentItem;
    if(heldCard && !(heldCard.review_item_type==='message' ? messageEligible(heldCard) : heldCard.messages.some(messageEligible)))heldCard=null;
    const shown=currentItem;
    const key=shown ? itemKey(shown) : null;
    if(key!==pinnedKey)pinnedKey=key;
  });

  $effect(()=>{
    if(reviewReady && !showFinal && queueWindow?.next && items.length && items.every(item=>skipped[itemKey(item)])) {
      reviewAnchor=queueWindow.next;
    }
  });

  async function backToReview(): Promise<void> {
    if (returning) return;
    returning = true;
    returnError = null;
    returnCohort = [...skippedHistory];
    reviewAnchor = null;
    skipped = {};
    heldCard = null;
    pinnedKey = null;
    try {
      // Let the reset window demand reach the owner before requesting a fresh view.
      // Keep the composer mounted until that read succeeds, even for an empty result.
      await tick();
      updateReviewDemand();
      await navigation.owner.recover();
      await tick();
      const request=navigation.owner.getRequest();
      if (displayed?.runtimeCurrent && displayed.publication.view?.scopeGeneration===request.scope_generation
        && sameReviewHistory(request.review_priority_keys,returnCohort) && sameReviewHistory(request.review_skipped_keys,skippedHistory)
        && queueWindow?.request.anchor === null && currentItem) finalLatched = false;
    } catch (error) {
      returnError = error;
    } finally {
      returning = false;
    }
  }

  function skip(item: ReviewItem): void {
    if(messageBusy)return;
    advancePast(item);
    const key=reviewHistoryKey(item);
    if(!skippedHistory.some(previous=>previous.kind===key.kind && previous.id===key.id))skippedHistory=[...skippedHistory,key];
    skipped = { ...skipped, [itemKey(item)]: true };
    heldCard=null;
  }

  function openTicket(item: ReviewItem): void {
    if(messageBusy)return;
    window.location.hash = item.review_item_type === "message" ? reviewMessageHref(item) : workspaceAddress({ kind: "ticket", id: item.ticket_id });
  }

  // The card says a decision has left the queue — approved, sent back, or already
  // gone by the time its Ticket was read. Re-read the queue so the next ask comes up.
  function messageResolved(conversationId:string):void {
    // While an artifact is open only its bar can resolve a message: that reply finishes with it.
    if (heldFile) artifact.close();
    const item=currentItem;
    const leaves=item?.review_item_type==='message' ? item.conversation_id===conversationId
      : item?.review_item_type==='ticket' && !item.proposal && item.messages.every(message=>message.conversation_id===conversationId);
    if(item && leaves)advancePast(item);
    if(heldCard?.review_item_type==='message' && heldCard.conversation_id===conversationId)heldCard=null;
    else if(heldCard?.review_item_type==='ticket') {
      const messages=heldCard.messages.filter(message=>message.conversation_id!==conversationId);
      heldCard=messages.length ? {...heldCard,messages} : null;
    }
    messageGenerations={...messageGenerations,[conversationId]:(messageGenerations[conversationId] ?? 0)+1};
    busyConversations={...busyConversations,[conversationId]:false};
    void navigation.owner.recover().catch(()=>undefined);
  }
  function messageBusyChanged(conversationId:string,busy:boolean):void {
    if(Boolean(busyConversations[conversationId])!==busy)busyConversations={...busyConversations,[conversationId]:busy};
  }

  function proposalResolved(identity: string): void {
    // A decision made under the artifact finishes with it, even when the card stays for its messages.
    if (heldFile) artifact.close();
    const item=currentItem;
    if(item?.review_item_type==='ticket' && item.proposal && reviewProposalIdentity(item.proposal)===identity && item.messages.length===0)advancePast(item);
    decidedProposals = { ...decidedProposals, [identity]: true };
    void navigation.owner.recover().catch(() => undefined);
  }

  // An artifact opens over the card it was named on. The bar under it is the card's own
  // controls, chosen by what held the link: a message's reply, or else the proposal's
  // decisions. Close, Escape, or the card leaving returns to the queue.
  let heldFile = $state<ManagedFileTarget | null>(null);
  const artifact = createArtifactOnThePage({
    shown: () => heldFile,
    keep: (file) => { heldFile = file; },
    conversationState: () => "rest",
    dismissConversation: () => undefined,
    root: () => screenElement
  });
  let artifactCardKey: string | null = null;
  $effect(() => {
    const key = !showFinal && currentItem ? itemKey(currentItem) : null;
    const file = heldFile;
    untrack(() => {
      if (file && artifactCardKey !== null && key !== artifactCardKey) artifact.close();
      artifactCardKey = key;
    });
  });
  /** The message whose reply stands under the artifact, or null for the proposal's decisions. */
  let barMessage = $derived.by(() => {
    if (!artifact.shown || !currentItem) return null;
    if (currentItem.review_item_type === 'message') return currentItem;
    const origin = artifact.origin;
    const named = origin?.startsWith('message:') ? currentItem.messages.find(message => `message:${message.conversation_id}` === origin) : undefined;
    if (named) return named;
    return currentItem.proposal ? null : currentItem.messages[0] ?? null;
  });

  // Global review shortcuts (approved addition): s = skip, o = open ticket,
  // Cmd/Ctrl+Enter = approve the current ask. They fire only when the keystroke
  // did not originate in an editable control and was not already handled there —
  // InlineEdit's own Cmd/Ctrl+Enter preventDefaults and blurs to <body>, so an
  // activeElement check alone would let that approve by accident.
  function typingTarget(event: KeyboardEvent): boolean {
    const target = event.target as HTMLElement | null;
    if (!target) return false;
    if (target.isContentEditable) return true;
    return ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(target.tagName);
  }

  function onWindowKeydown(event: KeyboardEvent): void {
    if (showFinal) return;
    const item = currentItem;
    if (!item) return;
    // Ignore auto-repeat: holding a key must not skip/approve through the decisions.
    if (event.repeat || event.defaultPrevented || typingTarget(event)) return;

    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      const button = screenElement?.querySelector<HTMLButtonElement>(
        item.review_item_type === "ticket" && item.proposal ? "[data-review-card] [data-accept]" : "[data-review-card] [data-review-send]"
      );
      if (button && !button.disabled) {
        event.preventDefault();
        button.click();
      }
      return;
    }

    if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;

    if (event.key === "s" || event.key === "S") {
      event.preventDefault();
      skip(item);
    } else if (event.key === "o" || event.key === "O") {
      event.preventDefault();
      openTicket(item);
    }
  }

  $effect(() => {
    window.addEventListener("keydown", onWindowKeydown);
    return () => window.removeEventListener("keydown", onWindowKeydown);
  });
</script>

{#snippet messageKeys()}
  <div class="review-keys"><kbd>⌘↩</kbd> send &nbsp;·&nbsp; <kbd>S</kbd> skip &nbsp;·&nbsp; <kbd>O</kbd> open</div>
{/snippet}

{#snippet proposalKeys()}
  <div class="review-keys">
    <kbd>⌘↩</kbd> approve &nbsp;·&nbsp; <kbd>S</kbd> skip &nbsp;·&nbsp; <kbd>O</kbd> open ticket
  </div>
{/snippet}

{#snippet proposalCard(proposal: import("../lib/types").NavigationReviewProposalItem, composerOnly: boolean)}
  {@const identity = reviewProposalIdentity(proposal)}
  {#key identity}
    <ReviewProposalCard identity={proposal} embedded {composerOnly} ticketId={proposal.ticket_id} field={proposal.field}
      title={proposal.title} onResolved={() => proposalResolved(identity)}
      initialRevisionDraft={proposalDraft?.identity===identity ? proposalDraft.text : ""}
      initialEditedBody={proposalDraft?.identity===identity ? proposalDraft.editedBody : undefined}
      onEditedBodyChange={editedBody=>{proposalDraft={identity,text:proposalDraft?.identity===identity ? proposalDraft.text : "",editedBody};}}
      onRevisionDraftChange={text=>{proposalDraft={identity,text,editedBody:proposalDraft?.identity===identity ? proposalDraft.editedBody : undefined};}}/>
  {/key}
{/snippet}

<!-- The card's own controls, under an artifact opened from it. -->
{#snippet reviewBar()}
  {#if barMessage}
    {@const message = barMessage}
    {#key `${message.conversation_id}:${messageGenerations[message.conversation_id] ?? 0}`}
      <ReviewMessageCard item={message} embedded replyOnly onSkip={() => { if (currentItem) skip(currentItem); }}
        onResolved={()=>messageResolved(message.conversation_id)} onBusy={value=>messageBusyChanged(message.conversation_id,value)}/>
    {/key}
  {:else if currentItem?.review_item_type === "ticket" && currentItem.proposal}
    {@render proposalCard(currentItem.proposal, true)}
  {/if}
{/snippet}

<svelte:window onkeydown={artifact.closeOnEscape} />

{#if artifact.shown && currentItem && !showFinal}
  <section class="review-artifact-screen" data-screen="review" data-review-artifact bind:this={screenElement}>
    <ArtifactPreview target={artifact.shown} reloadSignal={artifact.reloadSignal} bind:element={artifact.element}
      onRefresh={artifact.refresh} onClose={artifact.close} bar={reviewBar} />
  </section>
{:else}
<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- svelte-ignore a11y_click_events_have_key_events -->
<section class="review-screen" class:review-screen-final={showFinal} data-screen="review" bind:this={screenElement} onclick={artifact.claimPress}>
  <ResourceState shape="block" loading={!reviewReady} hasData={reviewReady} loadingText="Loading review...">
    {#if showFinal}
      <ReviewFinalState {returnLabel} {waitingCount} {runningWorkerCount} {returning} {returnError} onReturn={() => void backToReview()} />
    {:else if currentItem}
      {#if currentItem.review_item_type === "message"}
        {@const message = currentItem}
        {#key `${itemKey(message)}:${messageGenerations[message.conversation_id] ?? 0}`}
          <ReviewMessageCard item={message} onSkip={()=>skip(message)} onResolved={()=>messageResolved(message.conversation_id)}
            onBusy={value=>messageBusyChanged(message.conversation_id,value)} footer={messageKeys}/>
        {/key}
      {:else}
        {@const ticket = currentItem}
        {#key ticket.ticket_id}
          <ReviewTicketCard item={ticket} onSkip={()=>skip(ticket)} busy={messageBusy} inlineDecision={Boolean(ticket.proposal) || ticket.messages.length > 0}>
            {#if ticket.proposal}
              {@const proposal = ticket.proposal}
              {@render proposalCard(proposal, false)}
            {/if}
            {#each ticket.messages as message, index (message.conversation_id)}
              {#key messageGenerations[message.conversation_id] ?? 0}
                <ReviewMessageCard item={message} embedded onSkip={()=>skip(ticket)}
                  onResolved={()=>messageResolved(message.conversation_id)} onBusy={value=>messageBusyChanged(message.conversation_id,value)}
                 />
              {/key}
            {/each}
            {#if ticket.proposal}{@render proposalKeys()}{:else}{@render messageKeys()}{/if}
          </ReviewTicketCard>
        {/key}
      {/if}
    {/if}
  </ResourceState>
</section>
{/if}


<style>
  .review-screen-final { max-width:calc(var(--measure-index) + var(--page-gutter)*2); }
  /* The artifact covers the whole Review pane, and the card's controls stand under it. */
  .review-artifact-screen { position:relative; flex:1; width:100%; height:100%; min-height:0; }
</style>

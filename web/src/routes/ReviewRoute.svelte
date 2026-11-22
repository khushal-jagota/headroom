<script lang="ts">
  import Button from "../components/Button.svelte";
  import { tick, onDestroy, untrack, setContext } from "svelte";
  import type { ManagedFileTarget } from "../lib/filePreview";
  import { createArtifactOnThePage } from "../lib/artifactOnThePage.svelte";
  import ArtifactPreview from "../components/ArtifactPreview.svelte";
  import { navigationApplication } from "../lib/navigation/application.svelte";
  import { navigationWindow, reviewProposalIdentity } from "../lib/navigation/presentation";

  import { workspaceAddress } from "../lib/workspaceAddress";
  import type { NavigationReviewItem as ReviewItem, NavigationReviewTicketItem, ReviewAskItem, ReviewMessageItem } from "../lib/types";
  import { viewWindow } from "../lib/navigation/views/owner";
  import ResourceState from "../components/ResourceState.svelte";
  import ReviewTicketCard from "../components/ReviewTicketCard.svelte";
  import ReviewProposalCard from "../components/ReviewProposalCard.svelte";
  import ReviewMessageCard from "../components/ReviewMessageCard.svelte";
  import ReviewAskCard from "../components/ReviewAskCard.svelte";
  import ReviewFinalState from "../components/ReviewFinalState.svelte";
  import ReviewDeck from "../components/ReviewDeck.svelte";
  import { Deck, DECK_CONTEXT, FINAL_VIEW, deckLayerIn, deckLayerOut, deckPartOut } from "../lib/review/deckMotion";
  import { reviewMessageHref, unreadOwnerMessages } from "../lib/review/messages";
  import { reviewAskHref } from "../lib/review/asks";
  import { readConversation, readEventsAfter } from "../lib/conversation/wire";
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
  // Asks answered on this screen: their conversation is hidden until the queue no longer holds it.
  let answeredAsks=$state<Record<string,boolean>>({});
  // Conversations answered or marked read on this screen. Like a decided proposal, the message
  // leaves at once. This holds only until the queue's refresh arrives: from then on the queue
  // is the truth, and a newer message in the same conversation is shown.
  let readMessages=$state<Record<string,boolean>>({});
  let returnLabel=$derived.by(()=>{void reviewRequest;return reviewReturnLabel(displayed,navigation.owner.getRequest(),skippedHistory);});
  // The route's own element. The approve shortcut's lookup is scoped to it, so a
  // card mounted by another screen at the same time can never be the one a Review
  // keystroke reaches.
  let screenElement = $state<HTMLElement | null>(null);
  // The deck: the one thing on screen moves out and the next moves in. See lib/review/deckMotion.
  const deck = new Deck();
  setContext(DECK_CONTEXT, deck);
  let rootElement = $state<HTMLElement | null>(null);
  let deckItemElement = $state<HTMLElement | null>(null);
  let layerElement = $state<HTMLElement | null>(null);
  // Closing an artifact draws its card again, so a note typed under the artifact is in it.
  let coverEpoch = $state(0);
  // Each opening of an artifact is its own layer, even when it comes while the last one is dropping.
  let layerOpening = $state(0);

  function itemKey(item: ReviewItem): string {
    return item.review_item_type === 'ticket' ? `ticket:${item.ticket_id}` : item.review_item_type === 'ask' ? `ask:${askKey(item)}` : `message:${item.conversation_id}`;
  }
  /** One ask, not its conversation: a second ask in the same turn is a new card, not the answered one. */
  function askKey(ask: ReviewAskItem): string { return `${ask.conversation_id}:${ask.waiting_since}`; }
  /** The messages a card holds. A Manager's or the Chief's ask is a card of its own, with none. */
  function messagesOf(item: ReviewItem): ReviewMessageItem[] {
    return item.review_item_type === 'message' ? [item] : item.review_item_type === 'ticket' ? item.messages : [];
  }
  /** The conversations a card is waiting on, for its busy state. */
  function conversationsOf(item: ReviewItem | null): string[] {
    if (!item) return [];
    return item.review_item_type === 'ask' ? [item.conversation_id]
      : [...messagesOf(item).map(message=>message.conversation_id),...(item.review_item_type==='ticket' && item.ask ? [item.ask.conversation_id] : [])];
  }
  /** The item as this screen knows it: a proposal decided here is gone from its card, and a
   *  card with nothing left is gone from the queue. */
  function undecided(item: ReviewItem): ReviewItem | null {
    if (item.review_item_type === 'message') return readMessages[item.conversation_id] ? null : item;
    if (item.review_item_type === 'ask') return answeredAsks[askKey(item)] ? null : item;
    const messages=item.messages.filter(message=>!readMessages[message.conversation_id]);
    const proposal=item.proposal && !decidedProposals[reviewProposalIdentity(item.proposal)] ? item.proposal : null;
    const ask=item.ask && !answeredAsks[askKey(item.ask)] ? item.ask : null;
    if(!proposal && !messages.length && !ask)return null;
    return messages.length===item.messages.length && proposal===item.proposal && ask===item.ask ? item : {...item,proposal,messages,ask};
  }

  let items = $derived((queueWindow ? queueWindow.members.flatMap(key=>(review?.items ?? []).filter(item=>key.kind===(item.review_item_type === 'ticket' ? 'ticket' : 'address') && key.id===(item.review_item_type === 'ticket' ? item.ticket_id : item.address_id))) : review?.items ?? [])
    .map(undecided).filter((item): item is ReviewItem => item !== null));
  let runningWorkerCount = $derived(displayed?.runtimeCurrent ? review?.runningConversationCount ?? null : null);
  let waitingCount = $derived(displayed?.runtimeCurrent ? navigation.presentationFor(displayed).globalCounts?.review ?? null : null);
  let finalLatched = $state(false);
  let returning = $state(false);
  let returnError = $state<unknown>(null);
  let busyConversations = $state<Record<string,boolean>>({});
  // Keep a painted conversation through delivery uncertainty and its exit. Owner
  // state and canonical address still govern admission, even for a retained draft.
  let heldCard = $state<NavigationReviewTicketItem|ReviewMessageItem|null>(null);
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
          return {...(fresh ?? heldCard),proposal:fresh?.proposal ?? null,ask:fresh?.ask ?? null,
            messages:[...(fresh?.messages ?? []),...held.filter(message=>!fresh?.messages.some(row=>row.conversation_id===message.conversation_id))]};
        }
      }
    }
    const waiting=items.filter(item=>!skipped[itemKey(item)]);
    return shownReviewItem(waiting,pinnedKey,itemKey).item;
  });
  let messageBusy = $derived(conversationsOf(currentItem).some(id=>busyConversations[id]));
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
    if(!heldCard && currentItem && currentItem.review_item_type!=='ask' && (currentItem.review_item_type==='message' ? messageEligible(currentItem) : currentItem.messages.some(messageEligible)))heldCard=currentItem;
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
    // A skip made under the artifact finishes the item: the artifact goes with it.
    if (heldFile) { deck.layerExit='left'; artifact.close(); }
    advancePast(item);
    const key=reviewHistoryKey(item);
    if(!skippedHistory.some(previous=>previous.kind===key.kind && previous.id===key.id))skippedHistory=[...skippedHistory,key];
    skipped = { ...skipped, [itemKey(item)]: true };
    heldCard=null;
  }

  function openTicket(item: ReviewItem): void {
    if(messageBusy)return;
    window.location.hash = item.review_item_type === "ticket" ? workspaceAddress({ kind: "ticket", id: item.ticket_id })
      : item.review_item_type === "ask" ? reviewAskHref(item) : reviewMessageHref(item);
  }

  // The card says a decision has left the queue — approved, sent back, or already
  // gone by the time its Ticket was read. Re-read the queue so the next ask comes up.
  function messageResolved(conversationId:string):void {
    const item=currentItem;
    const leaves=item?.review_item_type==='message' ? item.conversation_id===conversationId
      : item?.review_item_type==='ticket' && !item.proposal && !item.ask && item.messages.every(message=>message.conversation_id===conversationId);
    // While an artifact is open only its bar can resolve a message. If that finishes the item,
    // the artifact leaves with it. If part of the card remains, the artifact drops onto it.
    if (heldFile) { deck.layerExit=leaves ? 'left' : 'drop'; artifact.close(); }
    readMessages={...readMessages,[conversationId]:true};
    if(item && leaves)advancePast(item);
    if(heldCard?.review_item_type==='message' && heldCard.conversation_id===conversationId)heldCard=null;
    else if(heldCard?.review_item_type==='ticket') {
      const messages=heldCard.messages.filter(message=>message.conversation_id!==conversationId);
      heldCard=messages.length ? {...heldCard,messages} : null;
    }
    busyConversations={...busyConversations,[conversationId]:false};
    void navigation.owner.recover().catch(()=>undefined);
    void newerMessageWaits(conversationId).then(async waits=>{
      if(!waits)return;
      // A newer message arrived while the answer was on its way. It is new work for this
      // conversation, so it is shown, and the final screen that took the card's place gives it back.
      const {[conversationId]:_answered,...rest}=readMessages;
      readMessages=rest;
      await tick();
      if(finalLatched && !returning)finalLatched=false;
    }).catch(()=>undefined);
  }
  /** Whether the conversation holds a message that the answer did not cover. */
  async function newerMessageWaits(conversationId:string):Promise<boolean> {
    const view=await readConversation(conversationId);
    const rows=await readEventsAfter(conversationId,view.owner_read_through_sequence);
    return unreadOwnerMessages(rows,view.owner_read_through_sequence).length>0;
  }
  // An answered message is hidden until the queue itself no longer holds its conversation.
  $effect(()=>{
    const held=new Set((review?.items ?? []).flatMap(item=>messagesOf(item).map(message=>message.conversation_id)));
    untrack(()=>{
      const gone=Object.keys(readMessages).filter(id=>!held.has(id));
      if(gone.length)readMessages=Object.fromEntries(Object.entries(readMessages).filter(([id])=>held.has(id)));
    });
  });
  // An answered ask is hidden until the queue no longer holds its conversation.
  $effect(()=>{
    const held=new Set((review?.items ?? []).flatMap(item=>item.review_item_type==='ask' ? [askKey(item)] : item.review_item_type==='ticket' && item.ask ? [askKey(item.ask)] : []));
    untrack(()=>{
      const gone=Object.keys(answeredAsks).filter(id=>!held.has(id));
      if(gone.length)answeredAsks=Object.fromEntries(Object.entries(answeredAsks).filter(([id])=>held.has(id)));
    });
  });
  /** Nothing waits in the conversation any more. The ask leaves its card, and the card leaves with it if it was all there was. */
  function askResolved(ask:ReviewAskItem):void {
    const item=currentItem;
    const leaves=item?.review_item_type==='ask' ? askKey(item)===askKey(ask)
      : item?.review_item_type==='ticket' && !item.proposal && item.messages.length===0 && item.ask!==null && askKey(item.ask)===askKey(ask);
    if (heldFile) { deck.layerExit=leaves ? 'left' : 'drop'; artifact.close(); }
    if(item && leaves)advancePast(item);
    answeredAsks={...answeredAsks,[askKey(ask)]:true};
    busyConversations={...busyConversations,[ask.conversation_id]:false};
    void navigation.owner.recover().catch(()=>undefined);
  }
  function messageBusyChanged(busy:boolean,conversationId:string):void {
    if(Boolean(busyConversations[conversationId])!==busy)busyConversations={...busyConversations,[conversationId]:busy};
  }

  // While an artifact covers the card, the bar under it holds the one live copy of the note and
  // the edited text. The card underneath is drawn again when the artifact closes, so it never
  // writes over what the bar keeps.
  function covered(composerOnly: boolean): boolean {
    return !composerOnly && Boolean(artifact.shown);
  }

  function proposalResolved(identity: string): void {
    const item=currentItem;
    // A decision made under the artifact ends it. If it finished the item, the artifact leaves
    // with it. If the card stays for its messages, the artifact drops onto what remains.
    if (heldFile) { deck.layerExit=item?.review_item_type==='ticket' && item.messages.length===0 && !item.ask ? 'left' : 'drop'; artifact.close(); }
    if(item?.review_item_type==='ticket' && item.proposal && reviewProposalIdentity(item.proposal)===identity && item.messages.length===0 && !item.ask)advancePast(item);
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
  // What is on screen, named by what moves it: an item, or the final screen. A change inside the
  // same view, such as a late refresh, is not a move.
  let viewKey = $derived(showFinal ? FINAL_VIEW : currentItem ? itemKey(currentItem) : 'none');
  let partsKey = $derived(currentItem?.review_item_type==='ticket' ? JSON.stringify([Boolean(currentItem.proposal),currentItem.messages.map(message=>message.conversation_id),currentItem.ask?.conversation_id ?? null]) : '');
  // Runs before the screen is redrawn, while it still shows what is about to leave.
  $effect.pre(() => {
    const view=viewKey;
    void partsKey;
    void artifact.shown;
    untrack(() => deck.beforeChange(rootElement,view,deckItemElement));
  });
  let wasCovered=false;
  $effect.pre(() => {
    const covered=Boolean(artifact.shown);
    untrack(() => {
      if(wasCovered && !covered)coverEpoch+=1;
      if(!wasCovered && covered)layerOpening+=1;
      wasCovered=covered;
    });
  });
  /** The message whose reply stands under the artifact, or null for the proposal's decisions. */
  let barMessage = $derived.by(() => {
    if (!artifact.shown || !currentItem) return null;
    if (currentItem.review_item_type === 'message') return currentItem;
    if (currentItem.review_item_type === 'ask') return null;
    const origin = artifact.origin;
    const named = origin?.startsWith('message:') ? currentItem.messages.find(message => `message:${message.conversation_id}` === origin) : undefined;
    if (named) return named;
    return currentItem.proposal || currentItem.ask ? null : currentItem.messages[0] ?? null;
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
      // The decision is the one in front: the artifact's bar, else the item that is not leaving.
      const scope = artifact.shown && layerElement ? layerElement : screenElement?.querySelector("[data-deck-item]:not([data-deck-leaving])");
      const button = scope?.querySelector<HTMLButtonElement>(
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

{#snippet askKeys()}
  <div class="review-keys"><kbd>S</kbd> skip &nbsp;·&nbsp; <kbd>O</kbd> open</div>
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
      onEditedBodyChange={editedBody=>{if(covered(composerOnly))return;proposalDraft={identity,text:proposalDraft?.identity===identity ? proposalDraft.text : "",editedBody};}}
      onRevisionDraftChange={text=>{if(covered(composerOnly))return;proposalDraft={identity,text,editedBody:proposalDraft?.identity===identity ? proposalDraft.editedBody : undefined};}}/>
  {/key}
{/snippet}

<!-- The card's own controls, under an artifact opened from it. -->
{#snippet reviewBar()}
  {#if barMessage}
    {@const message = barMessage}
    {#key message.conversation_id}
      <ReviewMessageCard item={message} embedded replyOnly onSkip={() => { if (currentItem) skip(currentItem); }}
        onResolved={()=>messageResolved(message.conversation_id)} onBusy={messageBusyChanged}/>
    {/key}
  {:else if currentItem?.review_item_type === "ticket" && currentItem.proposal}
    {@render proposalCard(currentItem.proposal, true)}
  {/if}
{/snippet}

<svelte:window onkeydown={artifact.closeOnEscape} />

<div class="review-root" data-review-root bind:this={rootElement}>
  <div class="review-scroll" data-deck-scroll>
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- The card stays drawn under an artifact, so the artifact can drop onto it. It takes no input meanwhile. -->
    <section class="review-screen" data-screen="review" inert={Boolean(artifact.shown)} bind:this={screenElement} onclick={artifact.claimPress}>
      <ResourceState shape="block" loading={!reviewReady} hasData={reviewReady} loadingText="Loading review...">
        <ReviewDeck {deck} view={viewKey} final={showFinal} bind:element={deckItemElement}>
          {#key coverEpoch}
          {#if showFinal}
            <ReviewFinalState {returnLabel} {waitingCount} {runningWorkerCount} {returning} {returnError} onReturn={() => void backToReview()} />
          {:else if currentItem}
            {#if currentItem.review_item_type === "message"}
              {@const message = currentItem}
              {#key itemKey(message)}
                <ReviewMessageCard item={message} onSkip={()=>skip(message)} onResolved={()=>messageResolved(message.conversation_id)}
                  onBusy={messageBusyChanged} footer={messageKeys}/>
              {/key}
            {:else if currentItem.review_item_type === "ask"}
              {@const ask = currentItem}
              {#key itemKey(ask)}
                <ReviewAskCard item={ask} onSkip={()=>skip(ask)} onResolved={()=>askResolved(ask)}
                  onBusy={messageBusyChanged} footer={askKeys}/>
              {/key}
            {:else}
              {@const ticket = currentItem}
              {#key ticket.ticket_id}
                <ReviewTicketCard item={ticket} onSkip={()=>skip(ticket)} busy={messageBusy} inlineDecision={Boolean(ticket.proposal) || ticket.messages.length > 0 || Boolean(ticket.ask)}>
                  {#if ticket.ask}
                    {@const ask = ticket.ask}
                    <div data-deck-part out:deckPartOut={{ deck }}>
                      {#key askKey(ask)}
                        <ReviewAskCard item={ask} embedded onSkip={()=>skip(ticket)} onResolved={()=>askResolved(ask)} onBusy={messageBusyChanged}/>
                      {/key}
                    </div>
                  {/if}
                  {#if ticket.proposal}
                    {@const proposal = ticket.proposal}
                    <div data-deck-part out:deckPartOut={{ deck }}>{@render proposalCard(proposal, false)}</div>
                  {/if}
                  {#each ticket.messages as message, index (message.conversation_id)}
                    <div data-deck-part out:deckPartOut={{ deck }}>
                      <ReviewMessageCard item={message} embedded onSkip={()=>skip(ticket)}
                        onResolved={()=>messageResolved(message.conversation_id)} onBusy={messageBusyChanged}/>
                    </div>
                  {/each}
                  {#if ticket.proposal}{@render proposalKeys()}{:else if ticket.messages.length}{@render messageKeys()}{:else}{@render askKeys()}{/if}
                </ReviewTicketCard>
              {/key}
            {/if}
          {/if}
          {/key}
        </ReviewDeck>
      </ResourceState>
    </section>
  </div>
  {#key layerOpening}
  {#if artifact.shown && currentItem && !showFinal}
    <!-- The artifact is a layer over the card: it rises to open and drops to close. -->
    <div class="review-layer" data-review-artifact data-deck-layer bind:this={layerElement}
      in:deckLayerIn={{ deck }} out:deckLayerOut={{ deck }} onoutroend={() => { deck.covering = false; }}>
      <ArtifactPreview target={artifact.shown} reloadSignal={artifact.reloadSignal} bind:element={artifact.element}
        onRefresh={artifact.refresh} onClose={artifact.close} bar={reviewBar} />
    </div>
  {/if}
  {/key}
</div>

<style>
  /* The page is a scroll area with the artifact layer over it. The layer is not part of the
     scrolling, so it covers what is on screen however far the card is scrolled. */
  .review-root { position:relative; flex:1; min-height:0; display:flex; flex-direction:column; }
  .review-scroll { flex:1; min-height:0; display:flex; flex-direction:column; overflow-y:auto; }
  .review-layer { position:absolute; inset:0; z-index:1; }
</style>

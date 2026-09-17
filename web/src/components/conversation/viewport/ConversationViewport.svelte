<script lang="ts">
  /** The scrolling conversation and the policy that keeps a reader's place in it.
   *
   * Sending settles the message near the top and keeps room below it for the reply. The
   * room is returned as the answer fills it. While the reader is elsewhere, arriving
   * rows and folds preserve the line they were reading; following only moves forwards,
   * and Latest is the one explicit move allowed to go backwards.
   */
  import { onMount, tick, untrack, type Snippet } from "svelte";
  import ConversationTranscript from "../ConversationTranscript.svelte";
  import MessagePieces from "../MessagePieces.svelte";
  import type { ConversationState } from "../../../lib/conversation/conversationState";
  import type { ConversationLens } from "../../../lib/conversation/lens";
  import {
    outgoingMessageNote,
    type OutgoingMessage
  } from "../../../lib/conversation/outgoing";
  import type { TranscriptRow } from "../../../lib/conversation/transcript";
  import type {
    BackendModel,
    PromptDeliveryMode
  } from "../../../lib/conversation/wire";
  import { threadGeometry, type HeldView, type ThreadReading } from "./threadGeometry";
  import { READER_DRIVING_MILLISECONDS } from "./viewportConfiguration";

  let {
    conversationId,
    rows,
    visibleRows,
    lens,
    outgoingMessages,
    models,
    ownSenderLabel,
    livenessPulse,
    conversationState,
    emptyState,
    ticketId = null
  }: {
    conversationId: string;
    ticketId?: string | null;
    rows: readonly TranscriptRow[];
    visibleRows: readonly TranscriptRow[];
    lens: ConversationLens;
    outgoingMessages: readonly OutgoingMessage[];
    models: readonly BackendModel[];
    ownSenderLabel: string | null;
    livenessPulse: number;
    conversationState: ConversationState | null;
    emptyState?: Snippet;
  } = $props();

  let threadElement = $state<HTMLDivElement | null>(null);
  let reservedSpaceElement = $state<HTMLDivElement | null>(null);
  /** The room kept under a message you have just sent, for the answer to arrive into. */
  let reservedSpacePixels = $state(0);
  /** The view is keeping the newest line in sight rather than staying where it is. */
  let following = $state(true);
  let jumpVisible = $state(false);
  /** Where the thread's last piece of content ends, in the thread's scroll coordinates.
   *  Scrolling does not change it, so the hot scroll path reads this cached value. */
  let newestLineBottomPixels = 0;
  /** The thread's own size, watched because rewrapping changes its content geometry. */
  let threadWidthPixels = $state(0);
  let threadHeightPixels = $state(0);
  let settledOnOpening = false;
  let scrollRenderRequest = 0;
  let settledMessageIds = new Set<string>();
  let readerDrivingUntilMilliseconds = 0;
  let readerPointerIsDown = false;
  /** How far open the viewport was last drawn. */
  let stateOnScreen: ConversationState | null = null;
  let stateHasBeenDrawn = false;
  /** A reader's view held for the whole trip through a zero-height rest state. */
  let viewHeldAcrossTheMove: HeldView | null = null;
  /** The thread changed while nobody was looking, so it has not been measured for it. */
  let unmeasuredWhileHidden = false;

  /** Whether nobody is looking at this tab.
   *
   * Every measurement below walks the whole thread and forces the browser to lay it out
   * there and then, which is the same work whether or not there is anybody to see the
   * result. A hidden tab has nowhere for a reader to be and nothing to keep them at, so
   * arriving rows are drawn and left unmeasured until the tab comes back. */
  function nobodyIsLookingAtTheTab(): boolean {
    return document.visibilityState === "hidden";
  }

  /** What a message says about the way it was sent, in the transcript's own words. */
  function modeChip(mode: PromptDeliveryMode): string | null {
    if (mode === "send_now") return "sent now";
    if (mode === "steer") return "steered";
    return "queued";
  }

  /** Move only far enough that the newest line is in sight, and only ever forwards. */
  function keepTheNewestLineInSight(thread: HTMLDivElement): void {
    const least = threadGeometry(
      thread,
      reservedSpaceElement
    ).newestLineScrollTop(newestLineBottomPixels);
    if (least > thread.scrollTop) thread.scrollTop = least;
  }

  function takeTheReading(reading: ThreadReading): void {
    newestLineBottomPixels = reading.newestLineBottomPixels;
    if (reading.remainingReservedSpacePixels < reservedSpacePixels) {
      reservedSpacePixels = reading.remainingReservedSpacePixels;
    }
    jumpVisible = !reading.newestLineIsInSight;
  }

  /** Read the thread's full shape again after something other than scrolling changes it. */
  function readTheThreadAgain(thread: HTMLDivElement): void {
    takeTheReading(threadGeometry(thread, reservedSpaceElement).read(reservedSpacePixels));
  }

  /** The same, for a thread that has only been scrolled since it was last measured: where
   *  its last line ends has not moved, so it is not looked for again. */
  function readTheThreadAgainAfterScrollingOnly(thread: HTMLDivElement): void {
    takeTheReading(
      threadGeometry(thread, reservedSpaceElement).readFromNewestLineBottom(
        newestLineBottomPixels,
        reservedSpacePixels
      )
    );
  }

  function readerIsDrivingTheScroll(): boolean {
    return readerPointerIsDown || performance.now() <= readerDrivingUntilMilliseconds;
  }

  /** A wheel, a finger, or a key: the reader working the thread themselves. */
  function readerDroveTheThread(): void {
    readerDrivingUntilMilliseconds = performance.now() + READER_DRIVING_MILLISECONDS;
  }

  function onThreadScroll(): void {
    const thread = threadElement;
    if (thread === null) return;
    const inSight = threadGeometry(
      thread,
      reservedSpaceElement
    ).newestLineIsInSight(newestLineBottomPixels);
    // Only a person's own scrolling says anything about what they want. This code moves
    // the position too, and reading intent out of that would mistake following for intent.
    if (readerIsDrivingTheScroll()) following = inSight;
    jumpVisible = !inSight;
  }

  /** The reader asking for the newest line. The one move allowed to go backwards. */
  async function jumpToTheNewestLine(): Promise<void> {
    following = true;
    await tick();
    const thread = threadElement;
    if (thread === null) return;
    thread.scrollTop = Math.max(
      0,
      threadGeometry(
        thread,
        reservedSpaceElement
      ).newestLineScrollTop(newestLineBottomPixels)
    );
    readTheThreadAgain(thread);
  }

  /** Measure answer room against the height the thread has now. */
  function measureTheRoomAgainstTheHeightWeHaveNow(thread: HTMLDivElement): void {
    if (reservedSpacePixels === 0) return;
    const geometry = threadGeometry(thread, reservedSpaceElement);
    const asking = geometry.answerRoomMessage();
    if (asking === null) return;
    reservedSpacePixels = geometry.answerRoomPixels(asking);
  }

  /** Put a message that has just been sent near the top, with answer room beneath it. */
  async function settleTheSentMessage(messageId: string): Promise<boolean> {
    const thread = threadElement;
    if (thread === null) return false;
    const geometry = threadGeometry(thread, reservedSpaceElement);
    const sent = geometry.sentMessage(messageId);
    if (sent === null) return false;
    following = true;
    reservedSpacePixels = geometry.answerRoomPixels(sent);
    await tick();
    const currentThread = threadElement;
    if (currentThread === null) return false;
    const currentGeometry = threadGeometry(currentThread, reservedSpaceElement);
    const stillThere = sent.isConnected
      ? sent
      : currentGeometry.sentMessage(messageId);
    if (stillThere === null) return false;
    currentThread.scrollTop = currentGeometry.sentMessageScrollTop(stillThere);
    return true;
  }

  /** Restore from the first held line that survived, if there is one. */
  function keepTheReaderWhereTheyWere(
    thread: HTMLDivElement,
    held: HeldView
  ): boolean {
    const wanted = threadGeometry(
      thread,
      reservedSpaceElement
    ).restoredScrollTop(held);
    if (wanted === null) return false;
    if (wanted !== thread.scrollTop) thread.scrollTop = wanted;
    return true;
  }

  function messageToSettleOn(): string | null {
    const newest = outgoingMessages[outgoingMessages.length - 1];
    if (newest === undefined || settledMessageIds.has(newest.messageId)) return null;
    return newest.messageId;
  }

  // Keep this first: rows and optimistic messages settle before size or layer-state work.
  $effect.pre(() => {
    rows;
    outgoingMessages;
    const thread = threadElement;
    if (thread === null) return;
    if (outgoingMessages.length === 0) settledMessageIds = new Set();
    if (nobodyIsLookingAtTheTab()) {
      unmeasuredWhileHidden = true;
      return;
    }
    // A thread at rest has no view, room, or message position to measure.
    if (!untrack(() => threadGeometry(thread, reservedSpaceElement).hasShape())) return;
    const justSent = untrack(() => messageToSettleOn());
    const wasFollowing = untrack(() => following);
    // Holding the view walks the whole thread, and only one of the four ways this can end
    // restores from it. A reader following the newest line, or one whose own message is
    // about to be settled, is never put back on a line they were reading.
    const held =
      settledOnOpening && justSent === null && !wasFollowing
        ? untrack(() => threadGeometry(thread, reservedSpaceElement).holdView())
        : null;
    const request = ++scrollRenderRequest;
    void tick().then(async () => {
      const currentThread = threadElement;
      if (request !== scrollRenderRequest || currentThread === null) return;
      const currentGeometry = threadGeometry(currentThread, reservedSpaceElement);
      if (!currentGeometry.hasShape()) return;
      newestLineBottomPixels = currentGeometry.read(
        reservedSpacePixels
      ).newestLineBottomPixels;
      // Settling a sent message changes the thread's own height as well as its position.
      // Every other ending here moves the scroll and nothing else.
      let theContentMovedToo = false;
      if (!settledOnOpening) {
        // Opening a conversation puts you at the end of it, wherever that is.
        settledOnOpening = true;
        following = true;
        keepTheNewestLineInSight(currentThread);
      } else if (justSent !== null) {
        if (await settleTheSentMessage(justSent)) settledMessageIds.add(justSent);
        theContentMovedToo = true;
      } else if (wasFollowing) {
        keepTheNewestLineInSight(currentThread);
      } else if (held !== null) {
        keepTheReaderWhereTheyWere(currentThread, held);
      }
      if (request !== scrollRenderRequest || threadElement === null) return;
      if (theContentMovedToo) readTheThreadAgain(threadElement);
      else readTheThreadAgainAfterScrollingOnly(threadElement);
    });
  });

  /** The thread changed shape, or media in it finished arriving.
   *
   * There is deliberately no held-line correction here. A reader who was following keeps
   * the newest line visible; a reader who was not following receives no deliberate move.
   */
  function theThreadIsADifferentShapeNow(): void {
    const thread = threadElement;
    if (thread === null) return;
    if (nobodyIsLookingAtTheTab()) {
      unmeasuredWhileHidden = true;
      return;
    }
    if (!threadGeometry(thread, reservedSpaceElement).hasShape()) return;
    settleTheViewAfterAChangeOfShape(thread, null);
  }

  /** A changed viewport gets the same opening, restoration, and following policy. */
  function settleTheViewAfterAChangeOfShape(
    thread: HTMLDivElement,
    held: HeldView | null
  ): void {
    newestLineBottomPixels = threadGeometry(
      thread,
      reservedSpaceElement
    ).read(reservedSpacePixels).newestLineBottomPixels;
    if (!settledOnOpening) {
      settledOnOpening = true;
      following = true;
      keepTheNewestLineInSight(thread);
    } else {
      if (held !== null && !keepTheReaderWhereTheyWere(thread, held)) following = true;
      if (following) keepTheNewestLineInSight(thread);
    }
    readTheThreadAgain(thread);
  }

  // Keep this second: generic resize/media behavior follows row settlement.
  $effect(() => {
    threadWidthPixels;
    threadHeightPixels;
    if (threadElement === null) return;
    // After the rewrapping the new size caused, not before it.
    void tick().then(theThreadIsADifferentShapeNow);
  });

  /** Hold and restore the reader's place across rest, peeked, and opened heights. */
  // Keep this third: a layer move landing with a row gets the final placement decision.
  $effect.pre(() => {
    const wanted = conversationState;
    const thread = threadElement;
    if (thread === null) return;
    untrack(() => {
      const previous = stateOnScreen;
      const isAMove = stateHasBeenDrawn && previous !== wanted;
      stateOnScreen = wanted;
      stateHasBeenDrawn = true;
      if (!isAMove) return;
      if (threadGeometry(thread, reservedSpaceElement).hasShape()) {
        viewHeldAcrossTheMove = threadGeometry(
          thread,
          reservedSpaceElement
        ).holdView();
      }
      const held = viewHeldAcrossTheMove;
      if (wanted === "rest") return;
      viewHeldAcrossTheMove = null;
      const request = ++scrollRenderRequest;
      void tick().then(async () => {
        const currentThread = threadElement;
        if (request !== scrollRenderRequest || currentThread === null) return;
        if (!threadGeometry(currentThread, reservedSpaceElement).hasShape()) return;
        measureTheRoomAgainstTheHeightWeHaveNow(currentThread);
        await tick();
        if (request !== scrollRenderRequest || threadElement === null) return;
        if (!threadGeometry(threadElement, reservedSpaceElement).hasShape()) return;
        settleTheViewAfterAChangeOfShape(threadElement, held);
      });
    });
  });

  /** A fold changes height without a row arriving, so hold before the click takes effect. */
  // Keep this after all effects: it preserves their existing source and scheduling order.
  function onThreadClick(): void {
    const thread = threadElement;
    if (thread === null) return;
    const held = threadGeometry(thread, reservedSpaceElement).holdView();
    const wasFollowing = following;
    void tick().then(() => {
      const currentThread = threadElement;
      if (currentThread === null) return;
      newestLineBottomPixels = threadGeometry(
        currentThread,
        reservedSpaceElement
      ).read(reservedSpacePixels).newestLineBottomPixels;
      keepTheReaderWhereTheyWere(currentThread, held);
      if (wasFollowing) keepTheNewestLineInSight(currentThread);
      readTheThreadAgain(currentThread);
    });
  }

  // Everything that arrived while the tab was away is measured once, on the way back, and
  // it is the same settlement a change of shape gets: a reader who was following keeps the
  // newest line, and a reader who was not is not moved.
  onMount(() => {
    const onVisible = (): void => {
      if (document.visibilityState !== "visible" || !unmeasuredWhileHidden) return;
      unmeasuredWhileHidden = false;
      void tick().then(theThreadIsADifferentShapeNow);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  });
</script>

<svelte:window
  onpointerup={() => (readerPointerIsDown = false)}
  onpointercancel={() => (readerPointerIsDown = false)}
/>

<div class="chat-thread-shell">
  <!-- A reachable reading region: keyboard scrolling is reader intent just like a wheel. -->
  <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div
    class="chat-thread"
    data-conversation-thread
    role="region"
    aria-label="Conversation"
    tabindex="0"
    bind:this={threadElement}
    bind:clientWidth={threadWidthPixels}
    bind:clientHeight={threadHeightPixels}
    onloadcapture={theThreadIsADifferentShapeNow}
    onscroll={onThreadScroll}
    onwheel={readerDroveTheThread}
    ontouchstart={readerDroveTheThread}
    ontouchmove={readerDroveTheThread}
    onkeydown={readerDroveTheThread}
    onpointerdown={() => (readerPointerIsDown = true)}
    onclick={onThreadClick}
  >
    {#if emptyState && rows.length === 0 && outgoingMessages.length === 0}
      {@render emptyState()}
    {/if}
    <ConversationTranscript
      {rows}
      {visibleRows}
      {lens}
      {models}
      {ownSenderLabel}
      {livenessPulse}
      {conversationId}
      {ticketId}
    />
    {#each outgoingMessages as message (message.messageId)}
      {@const chip = modeChip(message.mode)}
      {@const note = outgoingMessageNote(message)}
      <article class="chat-u" data-conversation-outgoing={message.messageId}>
        {#if note || chip}
          <div class="c2-label" data-conversation-outgoing-label>
            {#if note}{note}{/if}
            {#if chip}<span class="c2-chip">{chip}</span>{/if}
          </div>
        {/if}
        <MessagePieces content={message.content} {conversationId} {ticketId} />
      </article>
    {/each}
    {#if reservedSpacePixels > 0}
      <div
        class="c2-reserved"
        data-conversation-reserved-space
        bind:this={reservedSpaceElement}
        style:height={`${reservedSpacePixels}px`}
        aria-hidden="true"
      ></div>
    {/if}
  </div>
  {#if jumpVisible}
    <button
      type="button"
      class="chat-jump"
      aria-label="Jump to latest message"
      onclick={() => void jumpToTheNewestLine()}
    ><span>Latest</span><span aria-hidden="true">↓</span></button>
  {/if}
</div>

<style>
  /* This component corrects position itself when content above the reader changes. */
  :global([data-conversation-thread]) { overflow-anchor: none; }
  /* Answer room is not content and never shrinks to make room for content. */
  .c2-reserved { flex: none; }
  /* An optimistic message uses the transcript's own label and chip vocabulary. */
  .c2-label {
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
    margin-block-end: var(--space-1);
  }
  .c2-chip {
    margin-inline-start: var(--space-2);
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-pill);
    color: var(--accent-bright);
    padding: 0 var(--space-2);
  }
</style>

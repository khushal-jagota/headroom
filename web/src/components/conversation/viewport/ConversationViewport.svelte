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
    emptyState
  }: {
    conversationId: string;
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
  let contentElement = $state<HTMLDivElement | null>(null);
  let reservedSpaceElement = $state<HTMLDivElement | null>(null);
  let reservedSpacePixels = $state(0);
  let following = $state(true);
  let jumpVisible = $state(false);
  let newestLineBottomPixels = 0;
  let settledOnOpening = false;
  let settledMessageIds = new Set<string>();
  let conversationOnScreen: string | null = null;
  let stateOnScreen: ConversationState | null = null;
  let stateHasBeenDrawn = false;
  let savedView: HeldView | null = null;
  let savedShape: string | null = null;
  let pendingLayerMove = false;
  let layerWasFollowing = true;
  let pendingOpen = false;
  let pendingLatest = false;
  let sentMessageToPlace: string | null = null;
  let answerRoomMessageId: string | null = null;
  let dirty = false;
  let queued = false;
  let disposed = false;
  let generation = 0;
  let readerInputRevision = 0;
  let readerDrivingUntilMilliseconds = 0;
  let readerPointerIsDown = false;
  let nativePositionWins = false;
  let ownScrollTarget: number | null = null;

  function nobodyIsLookingAtTheTab(): boolean {
    return document.visibilityState === "hidden";
  }

  /** Rest and hidden tabs keep dirty work, but never measure an invisible thread. */
  function canMeasure(): boolean {
    return !disposed && !nobodyIsLookingAtTheTab() && conversationState !== "rest";
  }

  function modeChip(mode: PromptDeliveryMode): string | null {
    if (mode === "send_now") return "sent now";
    if (mode === "steer") return "steered";
    return "queued";
  }

  /** Size checks do not walk the transcript on the ordinary scroll path. */
  function currentShape(thread: HTMLDivElement): string {
    return `${thread.clientWidth}/${thread.clientHeight}/${contentElement?.offsetHeight ?? 0}/${thread.scrollHeight}`;
  }

  function moveTheThread(thread: HTMLDivElement, wanted: number): void {
    const clamped = Math.max(0, Math.min(wanted, thread.scrollHeight - thread.clientHeight));
    if (Math.abs(thread.scrollTop - clamped) >= 0.5) thread.scrollTop = clamped;
    // Record the position the browser accepted, including its own integer rounding.
    ownScrollTarget = thread.scrollTop;
  }

  function keepTheNewestLineInSight(thread: HTMLDivElement): void {
    const least = threadGeometry(thread, reservedSpaceElement)
      .newestLineScrollTop(newestLineBottomPixels);
    if (least > thread.scrollTop) moveTheThread(thread, least);
  }

  function takeTheReading(reading: ThreadReading): void {
    newestLineBottomPixels = reading.newestLineBottomPixels;
    jumpVisible = !reading.newestLineIsInSight;
  }

  function saveTheSettledView(thread: HTMLDivElement): void {
    const geometry = threadGeometry(thread, reservedSpaceElement);
    takeTheReading(geometry.read(reservedSpacePixels));
    savedView = geometry.holdView();
    savedShape = currentShape(thread);
  }

  /** A new action during pending layout supersedes the older placement decision. */
  function readerDroveTheThread(): void {
    ownScrollTarget = null;
    readerInputRevision += 1;
    readerDrivingUntilMilliseconds = performance.now() + READER_DRIVING_MILLISECONDS;
    const thread = threadElement;
    // Native input can precede ResizeObserver delivery for an already changed DOM.
    // Its position then owns the new geometry, rather than the previous anchors.
    if (dirty || queued || (thread !== null && canMeasure() && savedShape !== currentShape(thread))) {
      nativePositionWins = true;
    }
  }

  function onThreadKeydown(event: KeyboardEvent): void {
    if (event.defaultPrevented || event.altKey || event.metaKey) return;
    if (event.ctrlKey && event.key !== "Home" && event.key !== "End") return;
    const target = event.target;
    if (!(target instanceof Element)) return;
    // Editing and selection controls own their navigation keys. Plain transcript
    // links and buttons still let PageUp/PageDown/Home/End scroll their container.
    if ((target instanceof HTMLElement && target.isContentEditable) || target.closest(
      'input, textarea, select, [role="textbox"], [role="combobox"], [role="listbox"], [role="menu"], [role="slider"], [role="spinbutton"], [role="tree"], [role="grid"], [role="tablist"]'
    )) return;
    if (target.closest('button, [role="button"]') && event.key === " ") return;
    if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) {
      readerDroveTheThread();
    }
  }

  function onThreadPointermove(event: PointerEvent): void {
    if (readerPointerIsDown && (event.movementX !== 0 || event.movementY !== 0)) {
      readerDroveTheThread();
    }
  }

  function onThreadScroll(): void {
    const thread = threadElement;
    if (thread === null || !canMeasure()) return;
    if (ownScrollTarget !== null && Math.abs(thread.scrollTop - ownScrollTarget) < 0.5) {
      ownScrollTarget = null;
      return;
    }
    ownScrollTarget = null;
    // Native layout clamps can arrive during a recent wheel gesture. Preserve the
    // old anchors until both the content and viewport match a settled reading.
    if (dirty || queued || savedShape !== currentShape(thread)) {
      requestReconciliation();
      return;
    }
    const geometry = threadGeometry(thread, reservedSpaceElement);
    const inSight = geometry.newestLineIsInSight(newestLineBottomPixels);
    jumpVisible = !inSight;
    if (performance.now() > readerDrivingUntilMilliseconds) return;
    following = inSight;
    savedView = geometry.holdView();
    savedShape = currentShape(thread);
  }

  function jumpToTheNewestLine(): void {
    pendingLatest = true;
    nativePositionWins = false;
    requestReconciliation();
  }

  function requestWaitingOnThePerson(thread: HTMLDivElement): HTMLElement | null {
    const waiting = thread.querySelectorAll<HTMLElement>(
      '[data-conversation-ask-state="live"], [data-conversation-user-input-state="live"]'
    );
    return waiting[waiting.length - 1] ?? null;
  }

  function landTheOpen(thread: HTMLDivElement): void {
    const geometry = threadGeometry(thread, reservedSpaceElement);
    const end = Math.max(0, geometry.newestLineScrollTop(newestLineBottomPixels));
    const request = requestWaitingOnThePerson(thread);
    moveTheThread(thread, request === null ? end : Math.min(end, geometry.elementAtTheTopScrollTop(request)));
    following = request === null || geometry.newestLineIsInSight(newestLineBottomPixels);
  }

  function restoreTheReader(thread: HTMLDivElement, held: HeldView): void {
    const wanted = threadGeometry(thread, reservedSpaceElement).restoredScrollTop(held);
    moveTheThread(thread, wanted ?? held.scrollTop);
    if (wanted === null) following = false;
  }

  /** All causes retain their intent until one pass settles the latest DOM. */
  function requestReconciliation(): void {
    dirty = true;
    if (queued || !canMeasure()) return;
    queued = true;
    const startedIn = generation;
    void reconcileTheThread(startedIn);
  }

  async function reconcileTheThread(startedIn: number): Promise<void> {
    try {
      while (dirty && startedIn === generation && canMeasure()) {
        // Svelte and managed Markdown finish their DOM writes before the reading.
        await tick();
        if (startedIn !== generation || !canMeasure()) return;
        const thread = threadElement;
        if (thread === null || !threadGeometry(thread, reservedSpaceElement).hasShape()) {
          // The first nonzero observation resumes this work without a microtask loop.
          dirty = false;
          return;
        }
        dirty = false;
        const inputAtStart = readerInputRevision;
        let geometry = threadGeometry(thread, reservedSpaceElement);
        takeTheReading(geometry.read(reservedSpacePixels));

        const newest = outgoingMessages[outgoingMessages.length - 1];
        if (newest !== undefined && !settledMessageIds.has(newest.messageId)) {
          const sent = geometry.sentMessage(newest.messageId);
          if (sent !== null) {
            settledMessageIds.add(newest.messageId);
            answerRoomMessageId = newest.messageId;
            sentMessageToPlace = newest.messageId;
            reservedSpacePixels = geometry.answerRoomPixels(sent);
            following = true;
          }
        } else if (reservedSpacePixels > 0 && answerRoomMessageId !== null) {
          const asking = geometry.sentMessage(answerRoomMessageId);
          if (asking !== null) reservedSpacePixels = geometry.answerRoomPixels(asking);
        }

        // Space is part of layout. Place only after its final DOM height is present.
        await tick();
        if (startedIn !== generation || !canMeasure() || thread !== threadElement) return;
        geometry = threadGeometry(thread, reservedSpaceElement);
        if (!geometry.hasShape()) return;
        takeTheReading(geometry.read(reservedSpacePixels));
        const readerTookOver = nativePositionWins || readerInputRevision !== inputAtStart;
        if (readerTookOver) {
          // A newer gesture owns its native position against the fresh geometry.
          following = geometry.newestLineIsInSight(newestLineBottomPixels);
        } else if (!settledOnOpening || pendingOpen) {
          landTheOpen(thread);
        } else if (pendingLatest) {
          following = true;
          moveTheThread(thread, geometry.newestLineScrollTop(newestLineBottomPixels));
        } else if (pendingLayerMove) {
          following = layerWasFollowing;
          if (savedView !== null) restoreTheReader(thread, savedView);
          if (following) keepTheNewestLineInSight(thread);
        } else if (sentMessageToPlace !== null) {
          const sent = geometry.sentMessage(sentMessageToPlace);
          if (sent !== null) moveTheThread(thread, geometry.sentMessageScrollTop(sent));
        } else if (following) {
          keepTheNewestLineInSight(thread);
        } else if (savedView !== null) {
          restoreTheReader(thread, savedView);
        }
        settledOnOpening = true;
        pendingOpen = false;
        pendingLayerMove = false;
        pendingLatest = false;
        sentMessageToPlace = null;
        nativePositionWins = false;
        // A native clamp can queue its scroll event before settlement, even when our
        // final placement needs no write. Its acknowledgment is still layout work.
        ownScrollTarget = readerTookOver ? null : thread.scrollTop;
        saveTheSettledView(thread);
      }
    } finally {
      queued = false;
      if (dirty && canMeasure()) requestReconciliation();
    }
  }

  $effect.pre(() => {
    const wanted = conversationId;
    untrack(() => {
      if (conversationOnScreen === wanted) return;
      generation += 1;
      conversationOnScreen = wanted;
      settledOnOpening = false;
      settledMessageIds = new Set();
      reservedSpacePixels = 0;
      answerRoomMessageId = null;
      sentMessageToPlace = null;
      savedView = null;
      savedShape = null;
      pendingLayerMove = false;
      pendingOpen = true;
      pendingLatest = false;
      nativePositionWins = false;
      ownScrollTarget = null;
      following = true;
      requestReconciliation();
    });
  });

  $effect.pre(() => {
    rows;
    visibleRows;
    lens;
    outgoingMessages;
    threadElement;
    untrack(requestReconciliation);
  });

  $effect.pre(() => {
    const wanted = conversationState;
    untrack(() => {
      const previous = stateOnScreen;
      const moved = stateHasBeenDrawn && previous !== wanted;
      stateHasBeenDrawn = true;
      stateOnScreen = wanted;
      if (!moved) return;
      if (!pendingLayerMove) layerWasFollowing = following;
      pendingLayerMove = true;
      if (previous === "rest" && wanted !== "rest") pendingOpen = true;
      requestReconciliation();
    });
  });

  function onThreadClick(): void {
    requestReconciliation();
  }

  $effect(() => {
    const thread = threadElement;
    const content = contentElement;
    if (thread === null || content === null) return;
    const observer = new ResizeObserver(() => {
      if (!canMeasure()) {
        dirty = true;
        return;
      }
      if (savedShape !== currentShape(thread)) requestReconciliation();
    });
    observer.observe(thread);
    observer.observe(content);
    return () => observer.disconnect();
  });

  onMount(() => {
    const onVisible = (): void => {
      if (document.visibilityState === "visible" && dirty) requestReconciliation();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      disposed = true;
      generation += 1;
      document.removeEventListener("visibilitychange", onVisible);
    };
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
    onloadcapture={requestReconciliation}
    onscroll={onThreadScroll}
    onwheel={readerDroveTheThread}
    ontouchstart={readerDroveTheThread}
    ontouchmove={readerDroveTheThread}
    onkeydown={onThreadKeydown}
    onpointermove={onThreadPointermove}
    onpointerdown={() => (readerPointerIsDown = true)}
    onclick={onThreadClick}
  >
    <div class="chat-thread-content" data-conversation-content bind:this={contentElement}>
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
        <MessagePieces content={message.content} {conversationId} />
      </article>
    {/each}
    </div>
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

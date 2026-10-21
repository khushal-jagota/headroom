<script lang="ts">
  /** The scrolling conversation and the policy that keeps a reader's place in it.
   *
   * The thread is an ordinary scrolling element and the reader owns it. Nothing here
   * arbitrates against a gesture, because nothing here competes with one.
   *
   * Rows are as tall as their content from the moment they are admitted, so the only
   * thing that ever changes size above the reader is a page of older history being
   * admitted. The browser already keeps a reader's place through that, for all the
   * content above them rather than one chosen element, and it does it between frames
   * where no measurement of ours can stand. So this component lets it: the thread
   * does not turn scroll anchoring off, and nothing here corrects for a prepend.
   *
   * Three things ask for a position: opening the conversation, sending a message, and
   * Latest. Each is a move to one place, applied once. Between them the only standing
   * rule is that a reader who was with the newest line stays with it when more arrives.
   */
  import { onMount, tick, untrack, type Snippet } from "svelte";
  import ProjectedTranscript from '../view/ProjectedTranscript.svelte';
  import type {ConversationViewReader,ViewReaderAnchor} from '../../../lib/conversation/view/reader.svelte';
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
  import { threadGeometry, type ThreadReading } from "./threadGeometry";

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
    reader = null,
    viewAnchor = $bindable(null)
  }: {
    reader?: ConversationViewReader | null;
    viewAnchor?: ViewReaderAnchor | null;
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
  let jumpVisible = $state(false);
  let newestLineBottomPixels = 0;
  let settledOnOpening = false;
  let settledMessageIds = new Set<string>();
  let conversationOnScreen: string | null = null;
  let stateOnScreen: ConversationState | null = null;
  let stateHasBeenDrawn = false;
  let pendingLayerMove = false;
  let pendingOpen = false;
  let pendingLatest = false;
  let sentMessageToPlace: string | null = null;
  let answerRoomMessageId: string | null = null;
  let dirty = false;
  let queued = false;
  let disposed = false;
  let generation = 0;

  /** Was the reader with the newest line? Read before the write, while it is still true. */
  let withTheNewestLineBeforeTheWrite = true;

  function nobodyIsLookingAtTheTab(): boolean {
    return document.visibilityState === "hidden";
  }

  /** Rest and hidden tabs keep dirty work, but never measure an invisible thread. */
  function canMeasure(): boolean {
    return !disposed && !reader?.restoring && !nobodyIsLookingAtTheTab() && conversationState !== "rest";
  }

  function modeChip(mode: PromptDeliveryMode): string | null {
    if (mode === "send_now") return "sent now";
    if (mode === "steer") return "steered";
    return "queued";
  }

  function moveTheThread(thread: HTMLDivElement, wanted: number): void {
    const clamped = Math.max(0, Math.min(wanted, thread.scrollHeight - thread.clientHeight));
    if (Math.abs(thread.scrollTop - clamped) >= 0.5) thread.scrollTop = clamped;
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

  /** Is the reader with the newest line right now? Measured, never remembered. */
  function withTheNewestLine(thread: HTMLDivElement): boolean {
    const geometry = threadGeometry(thread, reservedSpaceElement);
    if (!geometry.hasShape()) return withTheNewestLineBeforeTheWrite;
    return geometry.newestLineIsInSight(geometry.read(reservedSpacePixels).newestLineBottomPixels);
  }

  function saveLogicalAnchor(thread:HTMLDivElement):void{
    if(reader===null||reader.restoring)return;
    const top=thread.getBoundingClientRect().top;
    const element=[...thread.querySelectorAll<HTMLElement>('[data-conversation-view-key]')].find(node=>node.getBoundingClientRect().bottom>top);
    const key=element?.dataset.conversationViewKey;
    const item=reader.pages.flatMap(page=>page.items).find(item=>item.key===key);
    if(item&&element)viewAnchor={key:item.key,order:item.order,offset:element.getBoundingClientRect().top-top,following:jumpVisible===false};
  }

  function onThreadScroll(): void {
    const thread = threadElement;
    if (thread === null || !canMeasure()) return;
    const geometry = threadGeometry(thread, reservedSpaceElement);
    if (!geometry.hasShape()) return;
    jumpVisible = !geometry.newestLineIsInSight(newestLineBottomPixels);
    saveLogicalAnchor(thread);
  }

  async function jumpToTheNewestLine(): Promise<void> {
    if(reader!==null)await reader.load({kind:"timeline",key:null},null,"older",true);
    pendingLatest = true;
    requestReconciliation();
  }

  function requestWaitingOnThePerson(thread: HTMLDivElement): HTMLElement | null {
    const waiting = thread.querySelectorAll<HTMLElement>(
      '[data-conversation-ask-state="live"], [data-conversation-user-input-state="live"]'
    );
    return waiting[waiting.length - 1] ?? null;
  }

  function landTheOpen(thread: HTMLDivElement): void {
    if(viewAnchor!==null&&!viewAnchor.following){
      const element=[...thread.querySelectorAll<HTMLElement>('[data-conversation-view-key]')].find(node=>node.dataset.conversationViewKey===viewAnchor?.key);
      if(element){moveTheThread(thread,thread.scrollTop+element.getBoundingClientRect().top-thread.getBoundingClientRect().top-viewAnchor.offset);return;}
    }
    const geometry = threadGeometry(thread, reservedSpaceElement);
    const end = Math.max(0, geometry.newestLineScrollTop(newestLineBottomPixels));
    const request = requestWaitingOnThePerson(thread);
    moveTheThread(thread, request === null ? end : Math.min(end, geometry.elementAtTheTopScrollTop(request)));
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

        if (!settledOnOpening || pendingOpen) {
          landTheOpen(thread);
        } else if (pendingLatest) {
          moveTheThread(thread, geometry.newestLineScrollTop(newestLineBottomPixels));
        } else if (sentMessageToPlace !== null) {
          const sent = geometry.sentMessage(sentMessageToPlace);
          if (sent !== null) moveTheThread(thread, geometry.sentMessageScrollTop(sent));
        } else if (withTheNewestLineBeforeTheWrite) {
          keepTheNewestLineInSight(thread);
        }
        settledOnOpening = true;
        pendingOpen = false;
        pendingLayerMove = false;
        pendingLatest = false;
        sentMessageToPlace = null;
        takeTheReading(geometry.read(reservedSpacePixels));
        jumpVisible = !geometry.newestLineIsInSight(newestLineBottomPixels);
        withTheNewestLineBeforeTheWrite = !jumpVisible;
        saveLogicalAnchor(thread);
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
      pendingLayerMove = false;
      pendingOpen = true;
      pendingLatest = false;
      withTheNewestLineBeforeTheWrite = true;
      requestReconciliation();
    });
  });

  /** Read the thread as it is now, before this update is written into it. */
  $effect.pre(() => {
    rows;
    reader?.revision;
    reader?.restoring;
    visibleRows;
    lens;
    outgoingMessages;
    threadElement;
    untrack(() => {
      const thread = threadElement;
      if (thread !== null && canMeasure()) withTheNewestLineBeforeTheWrite = withTheNewestLine(thread);
      requestReconciliation();
    });
  });

  $effect.pre(() => {
    const wanted = conversationState;
    untrack(() => {
      const previous = stateOnScreen;
      const moved = stateHasBeenDrawn && previous !== wanted;
      stateHasBeenDrawn = true;
      stateOnScreen = wanted;
      if (!moved) return;
      pendingLayerMove = true;
      if (previous === "rest" && wanted !== "rest") pendingOpen = true;
      requestReconciliation();
    });
  });

  $effect(() => {
    const thread = threadElement;
    const content = contentElement;
    if (thread === null || content === null) return;
    const observer = new ResizeObserver(() => {
      if (!canMeasure()) {
        dirty = true;
        return;
      }
      requestReconciliation();
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

<div class="chat-thread-shell">
  <!-- A reachable reading region, so the keyboard can scroll it. -->
  <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
  <div
    class="chat-thread"
    data-conversation-thread
    role="region"
    aria-label="Conversation"
    tabindex="0"
    bind:this={threadElement}
    onloadcapture={requestReconciliation}
    onscroll={onThreadScroll}
  >
    <div class="chat-thread-content" data-conversation-content bind:this={contentElement}>
    {#if emptyState && rows.length === 0 && outgoingMessages.length === 0}
      {@render emptyState()}
    {/if}
    {#if reader!==null}
      <ProjectedTranscript {reader} {models} {ownSenderLabel} {livenessPulse}/>
    {:else}
    <ConversationTranscript
      {rows}
      {visibleRows}
      {lens}
      {models}
      {ownSenderLabel}
      {livenessPulse}
      {conversationId}
    />
    {/if}
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
  /* The browser keeps the reader's place when history arrives above them. Nothing
     here competes with it, so it is left on. */
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

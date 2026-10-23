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
  import { conversationMotion, CONVERSATION_MOVE_MS } from "../../../lib/conversation/surfaceMotion";
  import type { ConversationState } from "../../../lib/conversation/conversationState";
  import type { ConversationLens } from "../../../lib/conversation/lens";
  import {
    type OutgoingMessage
  } from "../../../lib/conversation/outgoing";
  import type { TranscriptRow } from "../../../lib/conversation/transcript";
  import type {
    BackendModel,
  } from "../../../lib/conversation/wire";
  import { REPLY_LINE_MOVE_MS, SENT_MESSAGE_TOP_GAP_PIXELS } from "./viewportConfiguration";
  import { threadGeometry, type ThreadReading } from "./threadGeometry";

  let {
    conversationId,
    rows,
    visibleRows,
    lens,
    outgoingMessages,
    acknowledgedMessageIds = new Set<string>(),
    onStopDrawingMessage,
    onSendAgain,
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
    acknowledgedMessageIds?: ReadonlySet<string>;
    onStopDrawingMessage?: (messageId: string) => Promise<void> | void;
    onSendAgain?: (messageId: string) => Promise<void> | void;
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
  let belowCount = $state(0);
  const newerHistory = $derived(reader?.pages.some(page => page.scope.kind === "timeline" && page.newer !== null) ?? false);
  let newestLineBottomPixels = 0;
  let settledOnOpening = false;
  let settledMessageIds = new Set<string>();
  let acceptedMessageIds = new Set<string>();
  let newestPlacementMessageId: string | null = null;
  let displayIds = new Map<string, string>();
  let liftedDisplayIds = new Set<string>();
  let sentMessagePlacement: "newest" | "top" = "newest";
  let conversationOnScreen: string | null = null;
  let stateOnScreen: ConversationState | null = null;
  let stateHasBeenDrawn = false;
  let pendingOpen = false;
  let latestPending = $state(false);
  let latestIntent: {
    reader: ConversationViewReader | null;
    generation: number;
    admitted: boolean;
    focusButton: HTMLButtonElement | null;
  } | null = null;
  let readerOnScreen: ConversationViewReader | null = null;
  let sentMessageToPlace: string | null = null;
  let answerRoomMessageId = $state<string | null>(null);
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
    return !disposed && reader?.entryPhase !== "loading" && reader?.entryPhase !== "error" && !reader?.restoring && !nobodyIsLookingAtTheTab() && conversationState !== "rest";
  }

  const positionMotion = {};
  let gestureGeneration = 0;
  let heldUnreadKey = $state<string | null>(null);
  function stopAutomaticTravel(): void {
    gestureGeneration += 1;
    heldUnreadKey = null;
    reader?.releaseEntry();
    sentMessageToPlace = null;
    newestPlacementMessageId = null;
    latestIntent = null;
    latestPending = false;
    if (threadElement) conversationMotion(threadElement).cancel(positionMotion);
    withTheNewestLineBeforeTheWrite = false;
  }
  async function travel(thread: HTMLDivElement, wanted: number, duration = CONVERSATION_MOVE_MS): Promise<boolean> {
    const from = thread.scrollTop;
    if (Math.abs(wanted - from) < .5) return true;
    return conversationMotion(thread).move(positionMotion, fraction => {
      moveTheThread(thread, from + (wanted - from) * fraction);
    }, duration, undefined, true);
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

  function countBelow(): void {
    const thread = threadElement;
    if (!thread) return;
    const bottom = thread.getBoundingClientRect().bottom;
    belowCount = [...thread.querySelectorAll<HTMLElement>('.chat-a, .chat-u, .c2-ask-row')]
      .filter(node => node.getBoundingClientRect().top > bottom).length;
  }
  function takeTheReading(reading: ThreadReading): void {
    countBelow();
    newestLineBottomPixels = reading.newestLineBottomPixels;
    jumpVisible = !reading.newestLineIsInSight;
  }

  /** Is the reader with the newest line right now? Measured, never remembered. */
  function withTheNewestLine(thread: HTMLDivElement): boolean {
    if (heldUnreadKey !== null) return false;
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
    if(item&&element)viewAnchor={key:item.key,order:item.order,offset:element.getBoundingClientRect().top-top,following:jumpVisible===false&&!latestPending&&heldUnreadKey===null};
  }

  function onThreadScroll(): void {
    const thread = threadElement;
    if (thread === null || !canMeasure() || conversationMotion(thread).active(positionMotion)) return;
    const geometry = threadGeometry(thread, reservedSpaceElement);
    if (!geometry.hasShape()) return;
    jumpVisible = !geometry.newestLineIsInSight(newestLineBottomPixels);
    countBelow();
    withTheNewestLineBeforeTheWrite = !jumpVisible && heldUnreadKey === null;
    saveLogicalAnchor(thread);
  }

  function isCurrentLatest(intent: typeof latestIntent): boolean {
    return intent !== null && latestIntent === intent && !disposed &&
      intent.generation === generation && intent.reader === reader;
  }

  async function jumpToTheNewestLine(button: HTMLButtonElement): Promise<void> {
    heldUnreadKey = null;
    reader?.releaseEntry();
    if (threadElement) conversationMotion(threadElement).cancel(positionMotion);
    const intent = {
      reader,
      generation,
      admitted: reader === null,
      focusButton: button as HTMLButtonElement | null
    };
    latestIntent = intent;
    latestPending = true;
    if (intent.reader !== null) {
      const admitted = await intent.reader.load({kind:"timeline",key:null},null,"older",true);
      if (!isCurrentLatest(intent)) return;
      if (!admitted) {
        latestIntent = null;
        latestPending = false;
        requestReconciliation();
        return;
      }
      intent.admitted = true;
    }
    if (!isCurrentLatest(intent)) return;
    requestReconciliation();
  }

  function onFocusIn(event: FocusEvent): void {
    const intent = latestIntent;
    if (intent !== null && event.target !== intent.focusButton && event.target !== document.body) {
      intent.focusButton = null;
    }
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
        // Keep this pass associated with the click that requested it, including
        // both layout waits and the later focus handoff.
        const intent = latestIntent;
        const gesture = gestureGeneration;
        let followingMore = false;
        // Svelte and managed Markdown finish their DOM writes before the reading.
        await tick();
        if (startedIn !== generation || !canMeasure()) return;
        if (intent !== latestIntent) {
          dirty = true;
          continue;
        }
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
        if (newest?.restored) settledMessageIds.add(newest.messageId);
        if (newest !== undefined && !settledMessageIds.has(newest.messageId)) {
          newestPlacementMessageId = null;
          const sent = geometry.sentMessage(newest.messageId);
          if (sent !== null) {
            heldUnreadKey = null;
            reader?.releaseEntry();
            settledMessageIds.add(newest.messageId);
            newestPlacementMessageId = newest.messageId;
            const displayId = newest.displayMessageId ?? newest.messageId;
            displayIds.set(newest.messageId, displayId);
            if (liftedDisplayIds.has(displayId)) {
              answerRoomMessageId = newest.messageId;
              reservedSpacePixels = geometry.answerRoomPixels(sent);
            } else {
              // Before acceptance there is no answer to make room for. The outcome is
              // unknowable at this point, unlike the artifact's preselected demo fate.
              answerRoomMessageId = null;
              sentMessageToPlace = newest.messageId;
              sentMessagePlacement = "newest";
              reservedSpacePixels = 0;
              withTheNewestLineBeforeTheWrite = true;
            }
          }
        } else if (reservedSpacePixels > 0 && answerRoomMessageId !== null) {
          const asking = geometry.sentMessage(answerRoomMessageId);
          if (asking !== null) reservedSpacePixels = geometry.answerRoomPixels(asking);
        }

        const newlyAccepted = [...thread.querySelectorAll<HTMLElement>('[data-conversation-outgoing][data-conversation-accepted="true"]')]
          .filter(element => settledMessageIds.has(element.dataset.conversationOutgoing!) && !acceptedMessageIds.has(element.dataset.conversationOutgoing!));
        // Consume older acknowledgements too: none may steal placement on a later pass.
        for (const element of newlyAccepted) acceptedMessageIds.add(element.dataset.conversationOutgoing!);
        const accepted = newlyAccepted.find(element => element.dataset.conversationOutgoing === newestPlacementMessageId);
        if (accepted) {
          const id = accepted.dataset.conversationOutgoing!;
          acceptedMessageIds.add(id);
          liftedDisplayIds.add(displayIds.get(id) ?? id);
          answerRoomMessageId = id;
          sentMessageToPlace = id;
          sentMessagePlacement = "top";
          reservedSpacePixels = geometry.answerRoomPixels(accepted);
        }

        if (heldUnreadKey !== null) {
          const held = [...thread.querySelectorAll<HTMLElement>('[data-conversation-view-key]')]
            .find(node => node.dataset.conversationViewKey === heldUnreadKey);
          if (held) reservedSpacePixels = geometry.answerRoomPixels(held);
        }
        // Space is part of layout. Place only after its final DOM height is present.
        await tick();
        if (startedIn !== generation || !canMeasure() || thread !== threadElement) return;
        if (intent !== latestIntent) {
          dirty = true;
          continue;
        }
        geometry = threadGeometry(thread, reservedSpaceElement);
        if (!geometry.hasShape()) return;
        takeTheReading(geometry.read(reservedSpacePixels));

        if (reader?.entryPhase === 'placing') {
          if (!reader.entryTargetReady) return;
          const entryReader = reader;
          const target = reader.entryTarget;
          const element = target === null ? null : [...thread.querySelectorAll<HTMLElement>('[data-conversation-view-key]')]
            .find(node => node.dataset.conversationViewKey === target.key);
          if (target !== null && !element) return;
          if (element) {
            heldUnreadKey = target!.key;
            reservedSpacePixels = Math.max(reservedSpacePixels, geometry.answerRoomPixels(element));
            await tick();
            if (startedIn !== generation || thread !== threadElement || reader !== entryReader || reader.entryPhase !== 'placing' || reader.entryTarget?.key !== target?.key || !reader.entryTargetReady) return;
            moveTheThread(thread, threadGeometry(thread, reservedSpaceElement).elementAtTheTopScrollTop(element));
          } else landTheOpen(thread);
          reader.finishEntry();
        } else if (!settledOnOpening || pendingOpen) {
          landTheOpen(thread);
        } else if (latestPending && isCurrentLatest(intent) && intent?.admitted) {
          await travel(thread, geometry.newestLineScrollTop(newestLineBottomPixels));
        } else if (sentMessageToPlace !== null) {
          const sent = geometry.sentMessage(sentMessageToPlace);
          if (sent !== null) await travel(thread, sentMessagePlacement === "top" ? geometry.sentMessageScrollTop(sent) : geometry.newestLineScrollTop(newestLineBottomPixels));
        } else if (heldUnreadKey !== null) {
          const held = [...thread.querySelectorAll<HTMLElement>('[data-conversation-view-key]')]
            .find(node => node.dataset.conversationViewKey === heldUnreadKey);
          if (held) moveTheThread(thread, geometry.elementAtTheTopScrollTop(held));
        } else if (withTheNewestLineBeforeTheWrite && !latestPending) {
          const wanted = geometry.newestLineScrollTop(newestLineBottomPixels);
          if (wanted > thread.scrollTop) {
            const last = contentElement?.lastElementChild;
            const line = last ? Number.parseFloat(getComputedStyle(last).lineHeight) : 24;
            const step = Number.isFinite(line) && line > 0 ? line : 24;
            await travel(thread, Math.min(wanted, thread.scrollTop + step), REPLY_LINE_MOVE_MS);
            if (wanted > thread.scrollTop + .5) { dirty = true; followingMore = true; }
          }
        }
        if (startedIn !== generation || gesture !== gestureGeneration || thread !== threadElement) return;
        settledOnOpening = true;
        pendingOpen = false;
        sentMessageToPlace = null;
        takeTheReading(geometry.read(reservedSpacePixels));
        jumpVisible = !geometry.newestLineIsInSight(newestLineBottomPixels) && !followingMore;
        const latestPlaced = latestPending && isCurrentLatest(intent) && intent?.admitted && !jumpVisible;
        if (latestPlaced) latestPending = false;
        withTheNewestLineBeforeTheWrite = !jumpVisible && !latestPending && heldUnreadKey === null;
        saveLogicalAnchor(thread);
        if (latestPlaced && intent !== null) {
          // Wait for Latest to leave the DOM before returning the keyboard to the thread.
          await tick();
          if (startedIn !== generation || !isCurrentLatest(intent) || thread !== threadElement) return;
          const focusButton = intent.focusButton;
          if (focusButton !== null &&
              (document.activeElement === focusButton || document.activeElement === document.body)) {
            thread.focus({ preventScroll: true });
          }
          latestIntent = null;
        }
      }
    } finally {
      queued = false;
      if (dirty && canMeasure()) requestReconciliation();
    }
  }

  $effect.pre(() => {
    const wanted = conversationId;
    const wantedReader = reader;
    untrack(() => {
      if (conversationOnScreen === wanted && readerOnScreen === wantedReader) return;
      generation += 1;
      if (threadElement) conversationMotion(threadElement).cancel(positionMotion);
      readerOnScreen = wantedReader;
      latestPending = false;
      latestIntent = null;
      if (conversationOnScreen !== wanted) {
        conversationOnScreen = wanted;
        heldUnreadKey = null;
        settledOnOpening = false;
        settledMessageIds = new Set();
        acceptedMessageIds = new Set();
        newestPlacementMessageId = null;
        displayIds = new Map();
        liftedDisplayIds = new Set();
        reservedSpacePixels = 0;
        answerRoomMessageId = null;
        sentMessageToPlace = null;
        pendingOpen = true;
        withTheNewestLineBeforeTheWrite = true;
      }
      requestReconciliation();
    });
  });

  /** Read the thread as it is now, before this update is written into it. */
  $effect.pre(() => {
    rows;
    reader?.revision;
    reader?.entryPhase;
    reader?.entryTargetReady;
    reader?.restoring;
    visibleRows;
    lens;
    outgoingMessages;
    threadElement;
    untrack(() => {
      const thread = threadElement;
      if (thread !== null && canMeasure() && !conversationMotion(thread).active(positionMotion)) withTheNewestLineBeforeTheWrite = withTheNewestLine(thread);
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
      // Changing height is not a reason to move the thread. Only coming back from
      // rest is, because the thread was not there to be read a moment ago.
      if (previous === "rest" && wanted !== "rest") pendingOpen = true;
      requestReconciliation();
    });
  });

  $effect(() => {
    const thread = threadElement;
    const content = contentElement;
    if (thread === null || content === null) return;
    const stopFollowing = conversationMotion(thread).following(keys => {
      if (keys.includes(positionMotion)) return;
      if (!canMeasure() || conversationMotion(thread).active(positionMotion)) return;
      if (withTheNewestLineBeforeTheWrite && !latestPending && reader?.entryPhase !== 'placing') {
        const geometry = threadGeometry(thread, reservedSpaceElement);
        takeTheReading(geometry.read(reservedSpacePixels));
        keepTheNewestLineInSight(thread);
      }
    });
    const observer = new ResizeObserver(() => {
      if (!canMeasure()) {
        dirty = true;
        return;
      }
      requestReconciliation();
    });
    const acceptanceObserver = new MutationObserver(requestReconciliation);
    acceptanceObserver.observe(content, {subtree:true, attributes:true, attributeFilter:["data-conversation-accepted"]});
    observer.observe(thread);
    observer.observe(content);
    return () => { observer.disconnect(); acceptanceObserver.disconnect(); stopFollowing(); conversationMotion(thread).cancel(positionMotion); };
  });

  onMount(() => {
    const onVisible = (): void => {
      if (document.visibilityState === "visible" && dirty) requestReconciliation();
    };
    document.addEventListener("visibilitychange", onVisible);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      disposed = true;
      generation += 1;
      document.removeEventListener("visibilitychange", onVisible);
      document.removeEventListener("focusin", onFocusIn);
    };
  });
</script>

<div class="chat-thread-shell">
  <!-- A reachable reading region, so the keyboard can scroll it. -->
  <!-- The keyboard region keeps native scrolling and only cancels automatic travel. -->
  <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
  <div
    class="chat-thread"
    style:padding-top={heldUnreadKey !== null || answerRoomMessageId !== null ? `${SENT_MESSAGE_TOP_GAP_PIXELS}px` : undefined}
    data-conversation-thread
    role="region"
    aria-label="Conversation"
    tabindex="0"
    bind:this={threadElement}
    onloadcapture={requestReconciliation}
    onscroll={onThreadScroll}
    onwheel={stopAutomaticTravel}
    ontouchmove={stopAutomaticTravel}
    onpointerdown={(event) => { if (event.target === event.currentTarget) stopAutomaticTravel(); }}
    onkeydown={(event) => { if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) stopAutomaticTravel(); }}
    data-more-below={jumpVisible || newerHistory ? "true" : "false"}
  >
    <div class="chat-thread-content" data-conversation-content bind:this={contentElement}>
    {#if emptyState && rows.length === 0 && outgoingMessages.length === 0}
      {@render emptyState()}
    {/if}
    {#if reader!==null}
      <ProjectedTranscript {reader} {models} {ownSenderLabel} {livenessPulse} {outgoingMessages} {acknowledgedMessageIds} {onStopDrawingMessage} {onSendAgain}/>
    {:else}
    <ConversationTranscript
      {rows}
      {visibleRows}
      {lens}
      {models}
      {ownSenderLabel}
      {livenessPulse}
      {conversationId}
      {outgoingMessages}
      {acknowledgedMessageIds}
      {onStopDrawingMessage}
      {onSendAgain}
    />
    {/if}

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
  {#if jumpVisible || newerHistory || latestPending}
    <button
      type="button"
      class="chat-jump"
      aria-label="Jump to latest message"
      onclick={(event) => void jumpToTheNewestLine(event.currentTarget)}
    >{belowCount > 0 ? `${belowCount} new ${belowCount === 1 ? "message" : "messages"}` : "Latest"}</button>
  {/if}
</div>

<style>
  /* The browser keeps the reader's place when history arrives above them. Nothing
     here competes with it, so it is left on. */
  /* Answer room is not content and never shrinks to make room for content. */
  .c2-reserved { flex: none; }
</style>

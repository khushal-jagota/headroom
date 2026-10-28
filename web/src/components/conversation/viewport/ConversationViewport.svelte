<script lang="ts">
  /** Native anchoring owns ordinary scrollback and history prepends.
   * Entry and accepted sends retain their destination until presentation is ready.
   * Layout preserves the newest line or a held target without travel. Only actual
   * arriving stream text funds line travel, so a relayout cannot replay a backlog.
   */
  import { onMount, tick, untrack, setContext, type Snippet } from "svelte";
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
  import { REPLY_LINE_MOVE_MS, SENT_MESSAGE_TOP_GAP_PIXELS, TEXT_RESTORATION_WAIT_MS } from "./viewportConfiguration";
  import { restorationWait } from './restorationWait';
  import { threadGeometry, retirementGeometry, type ThreadReading } from "./threadGeometry";
  import { copyViewReaderAnchor } from '../../../lib/conversation/view/copyAnchor';
  import { conversationTextPresentations, TextPresentationRegistry } from '../../../lib/conversation/view/textPresentation';
  import { releaseTextRestoration, type TextRestorationPlacement, type LatestPlacementIntent } from './placement';
  import { threadPlacement, currentPlacement, presentationReady, streamArrivalBudget, streamTravelFits, type ThreadPlacement } from "./placement";

  let {
    conversationId,
    ownerReadThroughSequence = 0,
    placementReady = $bindable(false),
    entryPresentationPending = $bindable(true),
    rows,
    visibleRows,
    lens,
    outgoingMessages,
    withdrawnDisplayIds = new Set<string>(),
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
    ownerReadThroughSequence?: number;
    placementReady?: boolean;
    entryPresentationPending?: boolean;
    rows: readonly TranscriptRow[];
    visibleRows: readonly TranscriptRow[];
    lens: ConversationLens;
    outgoingMessages: readonly OutgoingMessage[];
    withdrawnDisplayIds?: ReadonlySet<string>;
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
  // This is the entire tail, including the former external flex gap.
  let reservedSpacePixels = $state(0);
  let retainedTopInset = $state<number | null>(null);
  let tailGap = $state(0);
  let manualReader = $state(false);
  let retirementOwner: object | null = null;
  let correctingRetirement = false;
  let navigationPending = false;
  let navigationTop = 0;
  let navigationHeight = 0;
  let navigationBase = 0;
  function rememberNavigationPosition(thread: HTMLDivElement): void {
    navigationTop = thread.scrollTop;
    navigationHeight = thread.clientHeight;
    navigationBase = threadGeometry(thread, reservedSpaceElement).baseHeight();
  }

  function actualNewestIsHeld(): boolean {
    return reader === null || reader.pages.some(page => page.scope.kind === 'timeline' && page.newer === null);
  }

  /** One synchronous geometry transaction, before the browser can paint a clamp. */
  function retireReplySpace(thread: HTMLDivElement, normalizeInset = true, latestFrame = false): void {
    if ((!manualReader && !latestFrame) || latestPending && !latestFrame || retirementOwner === null || !canMeasure() || pendingOpen) return;
    if (reservedSpacePixels === 0 && retainedTopInset === null) return;
    const owner = retirementOwner;
    const geometry = threadGeometry(thread, reservedSpaceElement);
    const viewport = thread.getBoundingClientRect();
    const row = [...thread.querySelectorAll<HTMLElement>('[data-conversation-view-key], [data-conversation-source-key]')]
      .find(node => node.getBoundingClientRect().bottom > viewport.top && node.getBoundingClientRect().top < viewport.bottom);
    const key = row?.dataset.conversationViewKey ?? row?.dataset.conversationSourceKey;
    const text = key ? textPresentations.capture(key, viewport) : undefined;
    const before = text && key ? textPresentations.measure(key, text) : null;
    const anchorTop = before?.kind === 'ready' ? before.rect.top : row?.getBoundingClientRect().top;
    const style = getComputedStyle(thread);
    const topInset = Number.parseFloat(style.paddingTop) || 0;
    const ordinaryInset = geometry.gap();
    const plan = retirementGeometry(thread.scrollTop, thread.clientHeight, geometry.baseHeight(), reservedSpacePixels,
      normalizeInset && retainedTopInset !== null ? ordinaryInset - topInset : 0);
    if (!plan || owner !== retirementOwner || thread !== threadElement) return;
    correctingRetirement = true;
    try {
      if (plan.insetDelta !== 0) {
        retainedTopInset = null;
        thread.style.paddingTop = `${topInset + plan.insetDelta}px`;
      }
      if (reservedSpaceElement) {
        reservedSpaceElement.style.height = `${plan.tail}px`;
        reservedSpaceElement.style.marginTop = `${-geometry.gap()}px`;
      }
      reservedSpacePixels = plan.tail;
      // Force layout first: native anchoring can already pay all of this correction.
      thread.getBoundingClientRect();
      const after = text && key ? textPresentations.measure(key, text) : null;
      const afterTop = after?.kind === 'ready' ? after.rect.top : row?.getBoundingClientRect().top;
      const wanted = anchorTop !== undefined && afterTop !== undefined
        ? thread.scrollTop + afterTop - anchorTop : plan.top;
      if (owner === retirementOwner && wanted >= 0 && wanted <= thread.scrollHeight - thread.clientHeight + .01) moveTheThread(thread, wanted);
    } finally { correctingRetirement = false; rememberNavigationPosition(thread); }
  }
  let jumpVisible = $state(false);
  let belowCount = $state(0);
  const newerHistory = $derived(reader?.pages.some(page => page.scope.kind === "timeline" && page.newer !== null) ?? false);
  const newerThanUnplacedAnchor = $derived.by(() => {
    const anchor = viewAnchor;
    return !placementReady && anchor !== null && !anchor.following && (reader?.pages.some(page =>
      page.scope.kind === 'timeline' && page.items.some(item =>
        item.order.sequence > anchor.order.sequence || item.order.sequence === anchor.order.sequence && item.order.slot > anchor.order.slot)) ?? false);
  });
  let newestLineBottomPixels = 0;
  let settledOnOpening = false;
  let settledMessageIds = new Set<string>();
  let newestPlacementMessageId: string | null = null;
  let conversationOnScreen: string | null = null;
  let stateOnScreen: ConversationState | null = null;
  let stateHasBeenDrawn = false;
  let pendingOpen = false;
  let latestPending = $state(false);
  let latestIntent: LatestPlacementIntent<ConversationViewReader> | null = null;
  let readerOnScreen: ConversationViewReader | null = null;
  let sentMessageToPlace: string | null = null;
  let answerRoomMessageId = $state<string | null>(null);
  let dirty = false;
  let queued = false;
  let disposed = false;
  let generation = 0;
  let placement: ThreadPlacement | null = null;
  const textPresentations = new TextPresentationRegistry(() => requestReconciliation());
  setContext(conversationTextPresentations, textPresentations);
  let textRestoration: TextRestorationPlacement | null = null;
  let restorationTimer: ReturnType<typeof restorationWait> | null = null;
  let timedRestoration: TextRestorationPlacement | null = null;
  function stopRestorationWait(): void {
    restorationTimer?.stop();
    restorationTimer = null;
    timedRestoration = null;
  }
  function fallBackRestoration(): void {
    const operation = textRestoration;
    if (!operation || operation.reader !== reader) return;
    // Retire the saved target before any asynchronous newest admission.
    // Clearing this operation makes each saved attempt terminal exactly once.
    void jumpToTheNewestLine(null, 'restoration-fallback', operation.attempt);
  }
  function syncRestorationWait(): void {
    const operation = textRestoration;
    if (!operation || reader?.restorationOutcome !== 'pending') {
      stopRestorationWait();
      return;
    }
    if (timedRestoration !== operation) {
      stopRestorationWait();
      timedRestoration = operation;
      restorationTimer = restorationWait(TEXT_RESTORATION_WAIT_MS, () => {
        if (textRestoration !== operation || operation.reader !== reader || operation.attempt !== reader?.restorationAttempt) return;
        reader?.failTextRestoration(operation.anchor.key);
        fallBackRestoration();
      });
    }
    restorationTimer?.active(document.visibilityState !== 'hidden' && conversationState !== 'rest');
  }
  function cancelTextRestoration(): void {
    stopRestorationWait();
    releaseTextRestoration(textRestoration);
    textRestoration = null;
    reader?.cancelRestoration();
  }
  function alignSavedText(thread: HTMLDivElement): boolean {
    const operation = textRestoration;
    if (!operation || operation.reader !== reader || operation.attempt !== (reader?.restorationAttempt ?? 0) || !operation.anchor.content) return false;
    if (reader?.restorationOutcome === 'failed' || reader?.restorationOutcome === 'unavailable') {
      fallBackRestoration();
      return false;
    }
    const location = operation.anchor.content;
    if (!operation.demand && reader?.restorationOutcome === 'pending') {
      operation.demand = textPresentations.demand(operation.anchor.key, location);
      operation.unsubscribe = operation.demand?.subscribe(requestReconciliation) ?? null;
    }
    let state = operation.demand?.read() ?? textPresentations.measure(operation.anchor.key, location);
    if (state.kind === 'pending' && !operation.demand) {
      operation.demand = textPresentations.demand(operation.anchor.key, location);
      operation.unsubscribe = operation.demand?.subscribe(requestReconciliation) ?? null;
      state = operation.demand?.read() ?? state;
    }
    if (state.kind === 'failed' || state.kind === 'unavailable') {
      reader?.failTextRestoration(operation.anchor.key, state.kind === 'unavailable');
      fallBackRestoration();
      return false;
    }
    if (state.kind !== 'ready') return false;
    const delta = state.rect.top - thread.getBoundingClientRect().top - location.lineOffset;
    const wantedTop = thread.scrollTop + delta;
    moveTheThread(thread, wantedTop);
    const measured = operation.demand?.read() ?? textPresentations.measure(operation.anchor.key, location);
    const aligned = measured.kind === 'ready' && Math.abs(measured.rect.top - thread.getBoundingClientRect().top - location.lineOffset) <= 1;
    if (!aligned && measured.kind === 'ready' && restorationPresentationReady(thread)
      && (wantedTop < -1 || wantedTop > thread.scrollHeight - thread.clientHeight + 1)) {
      // A lens or padding change can put the old line outside the scroll range.
      // Ready text cannot become reachable through another presentation callback.
      reader?.failTextRestoration(operation.anchor.key);
      fallBackRestoration();
      return false;
    }
    if (aligned && reader?.restorationOutcome === 'idle') releaseTextRestoration(operation);
    return aligned;
  }
  let pendingPresentationTarget: Element | null = null;
  let heldSent = $state<{ messageId: string; displayKey: string | null; rowKey: string | null } | null>(null);
  let liveArrivalPixels = 0;
  const streamWrites = new Map<object, { following: boolean; generation: number; messageKey: string | null; streaming: boolean; top: number; motionPixels: number }>();
  const arrivingMessages = new Map<string, boolean>();
  let newestMessageSequence = 0;
  let pendingFullMessageKey: string | null = null;
  let recoveryToPlace: string | null = null;
  const recoverySeen = new Set<string>();
  function noteMessageArrivals(): void {
    const messages = reader === null
      ? rows.filter(row => row.kind === 'agent_message').map(row => ({key:row.key, sequence:row.sequence}))
      : reader.pages.flatMap(page => page.items).filter(item => item.kind === 'row' && item.row_kind === 'agent_message').map(item => ({key:item.key, sequence:item.kind === 'row' ? item.source_sequence : 0}));
    const retained = new Set(messages.map(message => message.key));
    for (const key of arrivingMessages.keys()) if (!retained.has(key)) arrivingMessages.delete(key);
    for (const message of messages) {
      if (message.sequence > newestMessageSequence && settledOnOpening && !pendingOpen) {
        if (withTheNewestLineBeforeTheWrite || heldSent !== null) arrivingMessages.set(message.key, true);
      }
      newestMessageSequence = Math.max(newestMessageSequence, message.sequence);
    }
    newestMessageSequence = Math.max(newestMessageSequence, reader?.coverage ?? 0);
  }
  let previousThreadHeight = 0;
  let positionWritesPixels = 0;

  function onTextPresentation(event: Event): void {
    const { token, phase, before, after } = (event as CustomEvent<{token:object;phase:'before'|'after';before:number;after:number}>).detail;
    if (phase === 'before') {
      navigationPending = false;
      const target = event.target as HTMLElement;
      const messageKey = target.closest<HTMLElement>('[data-conversation-view-key]')?.dataset.conversationViewKey ?? target.closest<HTMLElement>('[data-conversation-source-key]')?.dataset.conversationSourceKey ?? null;
      const stream = target.closest<HTMLElement>('.c2-streaming');
      const streaming = stream !== null && stream.dataset.conversationStreamArrival !== 'false';
      const following = streaming
        ? settledOnOpening && !pendingOpen && (heldSent === null ? withTheNewestLineBeforeTheWrite : threadElement !== null && withTheNewestLine(threadElement))
        : messageKey !== null && arrivingMessages.get(messageKey) === true;
      if (streaming && following && heldSent === null && liveArrivalPixels === 0 && threadElement !== null && !conversationMotion(threadElement).active(positionMotion)) {
        // A newly admitted stream row can add a flex gap before its text writes.
        // Settle that layout now. Only the text surface growth funds the later line moves.
        const geometry = threadGeometry(threadElement, reservedSpaceElement);
        const wanted = geometry.newestLineScrollTop(geometry.read(reservedSpacePixels).newestLineBottomPixels);
        if (wanted > threadElement.scrollTop) moveTheThread(threadElement, wanted);
      }
      streamWrites.set(token, {following, generation, messageKey, streaming, top:threadElement?.scrollTop ?? 0, motionPixels:positionWritesPixels});
    } else {
      const write = streamWrites.get(token);
      streamWrites.delete(token);
      if (write?.generation === generation) {
        if (write.streaming) {
          liveArrivalPixels += streamArrivalBudget(true, write.following, before, after);
          if (write.following && threadElement !== null && heldSent === null) {
            // Native anchoring can apply the text growth during the forced layout read.
            // Undo only that native shift. Preserve position writes that occurred during parsing.
            moveTheThread(threadElement, write.top + positionWritesPixels - write.motionPixels);
          }
        }
        else if (write.following && write.messageKey !== null) pendingFullMessageKey = write.messageKey;
      }
      requestReconciliation();
    }
  }

  function heldSentElement(thread: HTMLDivElement): Element | null {
    if (heldSent === null) return null;
    const local = threadGeometry(thread, reservedSpaceElement).sentMessage(heldSent.messageId);
    if (local) return local;
    const wrapper = [...thread.querySelectorAll<HTMLElement>('[data-conversation-view-key]')].find(node =>
      heldSent!.displayKey !== null && node.dataset.conversationDisplayKey === heldSent!.displayKey ||
      heldSent!.rowKey !== null && node.dataset.conversationViewKey === heldSent!.rowKey);
    return wrapper?.querySelector('.chat-u, .chat-system') ?? wrapper ?? null;
  }

  function repairLayout(thread: HTMLDivElement): void {
    if (!canMeasure() || pendingOpen || !placementReady) return;
    const geometry = threadGeometry(thread, reservedSpaceElement);
    if (!geometry.hasShape()) return;
    if (manualReader) retireReplySpace(thread);
    takeTheReading(geometry.read(reservedSpacePixels));
    if (conversationMotion(thread).active(positionMotion)) {
      return;
    }
    if (textRestoration !== null && textRestoration.anchor.content) {
      alignSavedText(thread);
    } else if (heldSent !== null) {
      const sent = heldSentElement(thread);
      if (sent) moveTheThread(thread, geometry.sentMessageScrollTop(sent));
    } else if (withTheNewestLineBeforeTheWrite && streamWrites.size === 0 && liveArrivalPixels === 0 && pendingFullMessageKey === null && arrivingMessages.size === 0) {
      moveTheThread(thread, geometry.newestLineScrollTop(newestLineBottomPixels));
    }
  }

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
  function cancelLatestRecovery(): void {
    if (latestIntent?.cause !== 'restoration-fallback') return;
    latestIntent.controller?.abort();
    latestIntent = null;
    latestPending = false;
    if (threadElement) conversationMotion(threadElement).cancel(positionMotion);
  }
  function supersedeRestorationWithAcceptedSend(): void {
    if (latestIntent !== null) {
      latestIntent.controller?.abort();
      latestIntent = null;
      latestPending = false;
      if (threadElement) conversationMotion(threadElement).cancel(positionMotion);
    }
    retirementOwner = null;
    manualReader = false;
    if (textRestoration === null) return;
    cancelTextRestoration();
    viewAnchor = null;
    pendingOpen = false;
    settledOnOpening = true;
    placementReady = true;
  }
  function stopAutomaticTravel(): void {
    if (threadElement && answerRoomMessageId !== null) retainedTopInset = Number.parseFloat(getComputedStyle(threadElement).paddingTop) || SENT_MESSAGE_TOP_GAP_PIXELS;
    answerRoomMessageId = null;
    manualReader = true;
    navigationPending = true;
    retirementOwner = {};
    latestIntent?.controller?.abort();
    gestureGeneration += 1;
    cancelLatestRecovery();
    cancelTextRestoration();
    pendingOpen = false;
    settledOnOpening = true;
    heldSent = null;
    liveArrivalPixels = 0;
    streamWrites.clear();
    arrivingMessages.clear();
    pendingFullMessageKey = null;
    recoveryToPlace = null;
    if (placement) placement.cancelled = true;
    placement = null;
    if (placementReady) { pendingOpen = false; settledOnOpening = true; }
    placementReady = true;
    pendingPresentationTarget = null;
    if (newestPlacementMessageId !== null) settledMessageIds.add(newestPlacementMessageId);
    sentMessageToPlace = null;
    newestPlacementMessageId = null;
    latestIntent = null;
    latestPending = false;
    if (threadElement) conversationMotion(threadElement).cancel(positionMotion);
    withTheNewestLineBeforeTheWrite = false;
    if (threadElement) { retireReplySpace(threadElement); rememberNavigationPosition(threadElement); }
  }
  async function travel(thread: HTMLDivElement, wanted: number, duration = CONVERSATION_MOVE_MS, current: () => boolean = () => true): Promise<boolean> {
    const from = thread.scrollTop;
    if (Math.abs(wanted - from) < .5) return true;
    return await conversationMotion(thread).move(positionMotion, fraction => {
        if (current()) moveTheThread(thread, from + (wanted - from) * fraction);
      }, duration, undefined, true);
  }

  function moveTheThread(thread: HTMLDivElement, wanted: number): void {
    const clamped = Math.max(0, Math.min(wanted, thread.scrollHeight - thread.clientHeight));
    if (Math.abs(thread.scrollTop - clamped) >= 0.5) {
      const before = thread.scrollTop;
      if (!correctingRetirement) navigationPending = false;
      thread.scrollTop = clamped;
      positionWritesPixels += thread.scrollTop - before;
      // A tab can disappear between motion frames. Remember the line actually
      // on screen without changing the motion's follow or gesture intent.
      if (canMeasure()) saveLogicalAnchor(thread);
    }
  }

  function countBelow(): void {
    const thread = threadElement;
    if (!thread) return;
    const bottom = thread.getBoundingClientRect().bottom;
    const messages = reader?.pages.flatMap(page => page.items).filter(item => item.kind === 'row' &&
      ['prompt', 'agent_message', 'prompt_refused', 'prompt_uncertain', 'prompt_discarded', 'permission_ask', 'user_input'].includes(item.row_kind) &&
      item.source_sequence > ownerReadThroughSequence) ?? [];
    const unread = new Set(messages.map(item => item.key));
    if (reader === null) {
      const messageRows = visibleRows.filter(row => ['prompt','agent_message','prompt_refused','prompt_uncertain','prompt_discarded','permission_ask','user_input'].includes(row.kind));
      const admitted = [...thread.querySelectorAll<HTMLElement>('[data-conversation-row]')].filter(node => ['prompt','agent_message','prompt_refused','prompt_uncertain','prompt_discarded','permission_ask','user_input'].includes(node.dataset.conversationRow ?? ''));
      const keys = new Set<string>();
      for (let index = 0; index < Math.min(messageRows.length, admitted.length); index++) {
        const row = messageRows[index];
        if (row.sequence > ownerReadThroughSequence && admitted[index].getBoundingClientRect().top > bottom) keys.add(row.key);
      }
      belowCount = keys.size;
      return;
    }
    belowCount = new Set([...thread.querySelectorAll<HTMLElement>('[data-conversation-view-key]')]
      .filter(node => unread.has(node.dataset.conversationViewKey!) && node.getBoundingClientRect().top > bottom)
      .map(node => node.dataset.conversationViewKey)).size;
  }
  function takeTheReading(reading: ThreadReading): void {
    countBelow();
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
    if(reader===null||reader.restoring||latestPending&&latestIntent?.cause==='restoration-fallback'||pendingOpen||!settledOnOpening||!placementReady||textRestoration?.anchor.content)return;
    const top=thread.getBoundingClientRect().top;
    const element=[...thread.querySelectorAll<HTMLElement>('[data-conversation-view-key]')].find(node=>node.getBoundingClientRect().bottom>top);
    const key=element?.dataset.conversationViewKey;
    const item=reader.pages.flatMap(page=>page.items).find(item=>item.key===key);
    if(item&&element)viewAnchor={key:item.key,order:item.order,offset:element.getBoundingClientRect().top-top,following:!manualReader&&withTheNewestLineBeforeTheWrite&&!latestPending,content:textPresentations.capture(item.key,thread.getBoundingClientRect())};
  }

  function onThreadScroll(): void {
    const thread = threadElement;
    if (thread === null || correctingRetirement || !canMeasure() || pendingOpen || !placementReady || latestIntent?.cause === 'restoration-fallback' || conversationMotion(thread).active(positionMotion)) return;
    const geometry = threadGeometry(thread, reservedSpaceElement);
    if (!geometry.hasShape()) return;
    // Native anchoring can scroll after content grows, before ResizeObserver updates
    // the cached last-line position. Read current geometry before deciding to follow.
    // A native resize can dispatch scroll before ResizeObserver. A changed shape
    // cannot spend the preceding gesture's admission evidence.
    if (thread.clientHeight !== navigationHeight || Math.abs(geometry.baseHeight() - navigationBase) > .01) navigationPending = false;
    const readerMoved = navigationPending && Math.abs(thread.scrollTop - navigationTop) >= .5;
    if (manualReader) retireReplySpace(thread);
    takeTheReading(geometry.read(reservedSpacePixels));
    if (readerMoved && manualReader && reservedSpacePixels === 0 && actualNewestIsHeld()
      && Math.abs(thread.scrollTop - Math.max(0, geometry.newestLineScrollTop(newestLineBottomPixels))) <= 1) {
      manualReader = false;
      retirementOwner = null;
      withTheNewestLineBeforeTheWrite = true;
    } else if (!manualReader) withTheNewestLineBeforeTheWrite = !jumpVisible;
    rememberNavigationPosition(thread);
    saveLogicalAnchor(thread);
  }

  function isCurrentLatest(intent: typeof latestIntent): boolean {
    return intent !== null && latestIntent === intent && !disposed &&
      intent.generation === generation && intent.reader === reader &&
      !intent.controller?.signal.aborted && intent.gesture === gestureGeneration && !hasUnplacedAcceptedSend() &&
      (intent.cause !== 'restoration-fallback' || intent.attempt === (reader?.restorationAttempt ?? 0));
  }

  function hasUnplacedAcceptedSend(): boolean {
    if (outgoingMessages.some(message => message.accepted && !message.restored && !settledMessageIds.has(message.messageId))) return true;
    return [...(contentElement?.querySelectorAll<HTMLElement>('[data-conversation-outgoing][data-conversation-accepted="true"]') ?? [])]
      .some(element => !settledMessageIds.has(element.dataset.conversationOutgoing!) &&
        !outgoingMessages.some(message => message.messageId === element.dataset.conversationOutgoing && message.restored));
  }

  async function jumpToTheNewestLine(button: HTMLButtonElement | null, cause: LatestPlacementIntent<ConversationViewReader>['cause'] = 'latest', attempt: number | null = null): Promise<void> {
    latestIntent?.controller?.abort();
    retirementOwner = null;
    cancelLatestRecovery();
    // Keep the last visible address until this Latest operation actually lands.
    // A reader replacement cancels an unfinished click and restores that address.
    // An unfulfilled restoration destination is explicitly abandoned by Latest.
    const abandonUnfulfilledAnchor = reader !== null && reader.restorationOutcome !== 'idle';
    cancelTextRestoration();
    if (abandonUnfulfilledAnchor) viewAnchor = null;
    pendingOpen = false;
    settledOnOpening = true;
    placementReady = true;
    entryPresentationPending = false;
    heldSent = null;
    liveArrivalPixels = 0;
    arrivingMessages.clear();
    pendingFullMessageKey = null;
    streamWrites.clear();
    if (newestPlacementMessageId !== null) settledMessageIds.add(newestPlacementMessageId);
    if (sentMessageToPlace !== null) settledMessageIds.add(sentMessageToPlace);
    if (placement) placement.cancelled = true;
    placement = null;
    sentMessageToPlace = null;
    newestPlacementMessageId = null;
    recoveryToPlace = null;
    if (threadElement) conversationMotion(threadElement).cancel(positionMotion);
    if (cause === 'restoration-fallback') {
      viewAnchor = null;
      pendingPresentationTarget = null;
      answerRoomMessageId = null;
      reservedSpacePixels = 0;
      withTheNewestLineBeforeTheWrite = false;
    }
    const intent: LatestPlacementIntent<ConversationViewReader> = {
      reader, generation, cause, attempt, gesture: gestureGeneration,
      controller: new AbortController(),
      admitted: reader === null,
      focusButton: button
    };
    latestIntent = intent;
    latestPending = true;
    if (intent.reader !== null) {
      const admitted = cause === 'restoration-fallback' && intent.controller !== null
        ? await intent.reader.loadLatest({kind:'restoration-fallback', signal:intent.controller.signal, isCurrent:() => isCurrentLatest(intent)})
        : await intent.reader.load({kind:"timeline",key:null},null,"older",true, {kind:'restoration-fallback',signal:intent.controller!.signal,isCurrent:() => isCurrentLatest(intent)});
      if (!isCurrentLatest(intent)) return;
      if (!admitted) {
        latestIntent = null;
        latestPending = false;
        if (cause === 'restoration-fallback') {
          placementReady = true;
          entryPresentationPending = false;
        }
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

  function restoredAnchorElement(thread: HTMLDivElement): HTMLElement | null {
    if (viewAnchor === null || viewAnchor.following) return null;
    const nodes = [...thread.querySelectorAll<HTMLElement>('[data-conversation-view-key]')];
    const exact = nodes.find(node => node.dataset.conversationViewKey === viewAnchor?.key);
    if (exact) return exact;
    if (viewAnchor.content) return null;
    const items = reader?.pages.flatMap(page => page.items) ?? [];
    const candidates = nodes.map(node => ({node,item:items.find(item => item.key === node.dataset.conversationViewKey)}))
      .filter(candidate => candidate.item !== undefined)
      .sort((a,b) => a.item!.order.sequence-b.item!.order.sequence || a.item!.order.slot-b.item!.order.slot);
    const next = candidates.find(candidate => candidate.item!.order.sequence >= viewAnchor!.order.sequence);
    return next?.node ?? candidates.at(-1)?.node ?? null;
  }

  /** Released previews keep their height but do not load until they approach the reader. */
  function restorationPresentationReady(thread: HTMLDivElement): boolean {
    if (contentElement === null) return false;
    const viewport = thread.getBoundingClientRect();
    for (const released of contentElement.querySelectorAll<HTMLElement>('[data-visible-content-state="released"]')) {
      const bounds = released.getBoundingClientRect();
      if (bounds.bottom > viewport.top && bounds.top < viewport.bottom) return false;
    }
    return [...contentElement.querySelectorAll('[aria-busy="true"], [data-markdown-rendering]')].every(node =>
      node.closest('[data-visible-content-state="released"], [data-visible-content-state="retained"]') !== null);
  }

  function landTheOpen(thread: HTMLDivElement): void {
    if(textRestoration?.anchor.content && alignSavedText(thread)) return;
    if(viewAnchor!==null&&!viewAnchor.following){
      const element=restoredAnchorElement(thread);
      if(element){moveTheThread(thread,thread.scrollTop+element.getBoundingClientRect().top-thread.getBoundingClientRect().top-viewAnchor.offset);return;}
    }
    const geometry = threadGeometry(thread, reservedSpaceElement);
    const end = Math.max(0, geometry.newestLineScrollTop(newestLineBottomPixels));
    moveTheThread(thread, end);
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
        let provisionalNewest = false;
        // Svelte and managed Markdown finish their DOM writes before the reading.
        await tick();
        if (startedIn !== generation || gesture !== gestureGeneration || !canMeasure()) return;
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
        tailGap = geometry.gap();
        takeTheReading(geometry.read(reservedSpacePixels));

        for (const key of arrivingMessages.keys()) {
          const message = [...thread.querySelectorAll<HTMLElement>('[data-conversation-view-key], [data-conversation-source-key]')].find(node => node.dataset.conversationViewKey === key || node.dataset.conversationSourceKey === key);
          if (message === undefined) arrivingMessages.delete(key);
          else if (presentationReady(message)) pendingFullMessageKey = key;
        }
        // Only confirmed acceptance creates a send placement. A recovery bubble is not acceptance.
        for (const message of outgoingMessages) {
          if (message.restored) settledMessageIds.add(message.messageId);
        }
        const recovery = [...thread.querySelectorAll<HTMLElement>('[data-conversation-outgoing]:not([data-conversation-accepted="true"])')]
          .filter(element => !settledMessageIds.has(element.dataset.conversationOutgoing!) && !recoverySeen.has(element.dataset.conversationOutgoing!)).at(-1);
        if (recovery && presentationReady(recovery)) {
          recoveryToPlace = recovery.dataset.conversationOutgoing!;
          recoverySeen.add(recoveryToPlace);
        }
        const accepted = [...thread.querySelectorAll<HTMLElement>('[data-conversation-outgoing][data-conversation-accepted="true"]')]
          .filter(element => !settledMessageIds.has(element.dataset.conversationOutgoing!)).at(-1);
        if (accepted !== undefined) supersedeRestorationWithAcceptedSend();
        if (accepted !== undefined && presentationReady(accepted)) {
          const id = accepted.dataset.conversationOutgoing!;
          if (placement?.destination !== 'sent' || placement.messageId !== id) {
            if (placement) placement.cancelled = true;
            placement = threadPlacement(reader, 'sent', id);
          }
          sentMessageToPlace = id;
          recoveryToPlace = null;
          newestPlacementMessageId = id;
          answerRoomMessageId = id;
          retainedTopInset = SENT_MESSAGE_TOP_GAP_PIXELS;
          tailGap = geometry.gap();
          reservedSpacePixels = geometry.answerRoomPixels(accepted);
          const wrapper = accepted.closest<HTMLElement>('[data-conversation-view-key]');
          heldSent = { messageId: id, displayKey: wrapper?.dataset.conversationDisplayKey ?? null, rowKey: wrapper?.dataset.conversationViewKey ?? null };
          liveArrivalPixels = 0;
        } else if (answerRoomMessageId !== null) {
          const asking = heldSentElement(thread) ?? geometry.sentMessage(answerRoomMessageId);
          if (asking !== null) {
            reservedSpacePixels = geometry.answerRoomPixels(asking);
            if (reservedSpacePixels > 0 && heldSent !== null) { liveArrivalPixels = 0; pendingFullMessageKey = null; arrivingMessages.clear(); }
            if (reservedSpacePixels === 0 && heldSent !== null && sentMessageToPlace === null) {
              heldSent = null;
              withTheNewestLineBeforeTheWrite = true;
            }
          }
        }

        if (manualReader) retireReplySpace(thread);
        // Space is part of layout. Place only after its final DOM height is present.
        await tick();
        if (startedIn !== generation || gesture !== gestureGeneration || !canMeasure() || thread !== threadElement) return;
        if (intent !== latestIntent) {
          dirty = true;
          continue;
        }
        geometry = threadGeometry(thread, reservedSpaceElement);
        if (!geometry.hasShape()) return;
        takeTheReading(geometry.read(reservedSpacePixels));

        // Recovery owns an explicit newest destination. It never uses opening's
        // old row offset, even while newest content is pending.
        if (intent?.cause === 'restoration-fallback' && isCurrentLatest(intent) && !intent.admitted) return;
        if (pendingOpen || !settledOnOpening || !manualReader && reader?.entryPhase === 'placing') {
          if (reader !== null && reader.pages.length === 0 && (reader.status === null || reader.loading)) return;
          const targetKey = viewAnchor && !viewAnchor.following ? viewAnchor.key : null;
          if (viewAnchor !== null && !viewAnchor.following) {
            const held = restoredAnchorElement(thread);
            if (held) {
              // This provisional hidden position gives lazy content near the held
              // line a chance to hydrate before the final placement is revealed.
              landTheOpen(thread);
            }
            if (textRestoration?.anchor.content) {
              if (!alignSavedText(thread)) return;
            } else if (!restorationPresentationReady(thread)) return;
          }
          if (targetKey === null) landTheOpen(thread);
          const destination = [...thread.querySelectorAll<HTMLElement>('[data-conversation-view-key]')].filter(node => node.getBoundingClientRect().height > 0).at(-1) ?? contentElement ?? thread;
          const target = (targetKey === null ? destination : restoredAnchorElement(thread) ?? undefined);
          if (target === undefined) return;
          const restoringAnchor = viewAnchor !== null && !viewAnchor.following;
          if (!restoringAnchor && !presentationReady(target)) {
            if (targetKey !== null) return;
            const viewport = thread.getBoundingClientRect();
            // A held lazy body cannot hide an available tool or message beside its placeholder.
            // The provisional newest position is already applied, so this reveals no top flash.
            provisionalNewest = [...thread.querySelectorAll<HTMLElement>('[data-conversation-view-key]')].some(node => {
              if (node === target || !node.querySelector('[data-conversation-row], [data-conversation-tool]') || !presentationReady(node)) return false;
              const bounds = node.getBoundingClientRect();
              return bounds.height > 0 && bounds.bottom > viewport.top && bounds.top < viewport.bottom;
            });
            if (!provisionalNewest) return;
            pendingPresentationTarget = target;
          } else pendingPresentationTarget = null;
          // Position while hidden, then let active lazy blocks finish their own presentation.
          await tick();
          if (startedIn !== generation || !canMeasure() || thread !== threadElement || !provisionalNewest && !restoringAnchor && !presentationReady(target)) return;
          geometry = threadGeometry(thread, reservedSpaceElement);
          takeTheReading(geometry.read(reservedSpacePixels));
        }

        if (reader?.entryPhase === 'placing') {
          // Reader input can end automatic entry before presentation settles.
          if (!manualReader) landTheOpen(thread);
          reader.finishEntry();
        } else if (!settledOnOpening || pendingOpen) {
          landTheOpen(thread);
        } else if (latestPending && isCurrentLatest(intent) && intent?.admitted) {
          retirementOwner = intent;
          const from = thread.scrollTop;
          const initialInset = Number.parseFloat(getComputedStyle(thread).paddingTop) || 0;
          const destination = Math.max(0, Math.min(geometry.newestLineScrollTop(newestLineBottomPixels), Math.round(geometry.baseHeight()) - thread.clientHeight));
          const completed = await conversationMotion(thread).move(positionMotion, fraction => {
            if (!isCurrentLatest(intent) || retirementOwner !== intent) return;
            const insetChange = (Number.parseFloat(getComputedStyle(thread).paddingTop) || 0) - initialInset;
            moveTheThread(thread, from + (destination - from) * fraction + insetChange);
            retireReplySpace(thread, true, true);
          }, CONVERSATION_MOVE_MS, undefined, true);
          if (!completed || !isCurrentLatest(intent) || thread !== threadElement) return;
          // Each frame drained only range that its intended position no longer needs.
          // The arrival position fits the real bottom without an intermediate clamp.
          await tick();
          if (!isCurrentLatest(intent) || thread !== threadElement) return;
          geometry = threadGeometry(thread, reservedSpaceElement);
          takeTheReading(geometry.read(reservedSpacePixels));
          if (intent.cause === 'restoration-fallback') {
            await tick();
            if (!isCurrentLatest(intent) || thread !== threadElement || !canMeasure()) return;
            // Reveal readable newest content without reviving the saved text
            // demand. Retain recovery until the final row finishes presentation.
            // Existing layout notifications resume placement after body release.
            geometry = threadGeometry(thread, reservedSpaceElement);
            takeTheReading(geometry.read(reservedSpacePixels));
            if (!isCurrentLatest(intent)) return;
            moveTheThread(thread, geometry.newestLineScrollTop(newestLineBottomPixels));
          }
        } else if (recoveryToPlace !== null) {
          if (textRestoration !== null) { cancelTextRestoration(); viewAnchor = null; }
          recoveryToPlace = null;
          heldSent = null;
          answerRoomMessageId = null;
          reservedSpacePixels = 0;
          await tick();
          if (startedIn !== generation || gesture !== gestureGeneration) return;
          geometry = threadGeometry(thread, reservedSpaceElement);
          takeTheReading(geometry.read(reservedSpacePixels));
          await travel(thread, geometry.newestLineScrollTop(newestLineBottomPixels));
        } else if (sentMessageToPlace !== null && currentPlacement(placement, reader)) {
          const sent = heldSentElement(thread);
          if (sent === null || !presentationReady(sent)) return;
          const completed = await travel(thread, geometry.sentMessageScrollTop(sent));
          if (!completed || startedIn !== generation || gesture !== gestureGeneration) return;
          settledMessageIds.add(sentMessageToPlace);
          placement = null;
          repairLayout(thread);
        } else if (heldSent !== null) {
          const sent = heldSentElement(thread);
          if (sent) moveTheThread(thread, geometry.sentMessageScrollTop(sent));
        } else if (pendingFullMessageKey !== null && withTheNewestLineBeforeTheWrite && !latestPending) {
          const key = pendingFullMessageKey;
          const message = [...thread.querySelectorAll<HTMLElement>('[data-conversation-view-key], [data-conversation-source-key]')].find(node => node.dataset.conversationViewKey === key || node.dataset.conversationSourceKey === key);
          if (message === undefined || !presentationReady(message)) return;
          pendingFullMessageKey = null;
          arrivingMessages.delete(key);
          await travel(thread, geometry.newestLineScrollTop(newestLineBottomPixels));
        } else if (arrivingMessages.size > 0 && pendingFullMessageKey === null) {
          // An admitted new message still owns its presentation transaction.
          return;
        } else if (withTheNewestLineBeforeTheWrite && !latestPending) {
          const wanted = geometry.newestLineScrollTop(newestLineBottomPixels);
          const distance = wanted - thread.scrollTop;
          if (streamTravelFits(distance, liveArrivalPixels)) {
            const last = contentElement?.lastElementChild;
            const line = last ? Number.parseFloat(getComputedStyle(last).lineHeight) : 24;
            const step = Math.min(distance, Number.isFinite(line) && line > 0 ? line : 24);
            const before = thread.scrollTop;
            await travel(thread, Math.min(wanted, before + step), REPLY_LINE_MOVE_MS);
            const moved = Math.max(0, thread.scrollTop - before);
            liveArrivalPixels = moved < 0.5 ? 0 : Math.max(0, liveArrivalPixels - moved);
            const remaining = threadGeometry(thread, reservedSpaceElement).read(reservedSpacePixels).newestLineBottomPixels;
            if (streamTravelFits(geometry.newestLineScrollTop(remaining) - thread.scrollTop, liveArrivalPixels)) {
              dirty = true;
              followingMore = true;
            } else liveArrivalPixels = 0;
          } else {
            liveArrivalPixels = 0;
            moveTheThread(thread, wanted);
          }
        }
        if (startedIn !== generation || gesture !== gestureGeneration || thread !== threadElement) return;
        if (!provisionalNewest && reader?.restorationOutcome === 'pending') {
          // Settle the attempt before its final exact-address proof. Keep the
          // transcript hidden until the original address fits final geometry.
          const entryReader = reader;
          const operation = textRestoration;
          reader.settleTextRestoration();
          await tick();
          if (startedIn !== generation || gesture !== gestureGeneration || thread !== threadElement || reader !== entryReader || operation !== textRestoration || intent !== latestIntent) return;
          let finalReady: boolean;
          if (operation?.anchor.content) finalReady = alignSavedText(thread);
          else {
            landTheOpen(thread);
            finalReady = restorationPresentationReady(thread);
          }
          if (!finalReady) {
            const anchor = entryReader.restorationAnchor;
            if (anchor && entryReader.restorationOutcome === 'idle') entryReader.failTextRestoration(anchor.key);
            releaseTextRestoration(operation);
            fallBackRestoration();
            return;
          }
          geometry = threadGeometry(thread, reservedSpaceElement);
          takeTheReading(geometry.read(reservedSpacePixels));
        }
        if (textRestoration?.anchor.content && pendingOpen && !alignSavedText(thread)) {
          if (reader?.restorationOutcome === 'idle') reader.failTextRestoration(textRestoration.anchor.key);
          releaseTextRestoration(textRestoration);
          fallBackRestoration();
          return;
        }
        settledOnOpening = !provisionalNewest;
        pendingOpen = provisionalNewest;
        placementReady = true;
        entryPresentationPending = provisionalNewest || pendingPresentationTarget !== null && thread.contains(pendingPresentationTarget) && !presentationReady(pendingPresentationTarget);
        if (!entryPresentationPending) pendingPresentationTarget = null;
        if (!provisionalNewest && placement?.destination === 'entry') placement = null;
        if (!provisionalNewest) {
          reader?.settleTextRestoration();
          releaseTextRestoration(textRestoration);
          stopRestorationWait();
        }
        if (sentMessageToPlace !== null && settledMessageIds.has(sentMessageToPlace)) sentMessageToPlace = null;
        else if (sentMessageToPlace !== null) dirty = true;
        takeTheReading(geometry.read(reservedSpacePixels));
        jumpVisible = !geometry.newestLineIsInSight(newestLineBottomPixels) && !followingMore;
        const finalRow = [...thread.querySelectorAll<HTMLElement>('[data-conversation-view-key]')]
          .filter(node => node.getBoundingClientRect().height > 0).at(-1) ?? contentElement ?? thread;
        const latestPresented = intent?.cause !== 'restoration-fallback' || presentationReady(finalRow);
        const latestPlaced = latestPending && isCurrentLatest(intent) && intent?.admitted && actualNewestIsHeld()
          && reservedSpacePixels === 0 && Math.abs(thread.scrollTop - Math.max(0, geometry.newestLineScrollTop(newestLineBottomPixels))) <= 1
          && !jumpVisible && latestPresented;
        if (latestPlaced) { latestPending = false; manualReader = false; retirementOwner = null; }
        withTheNewestLineBeforeTheWrite = !manualReader && (followingMore || !jumpVisible) && !latestPending && heldSent === null;
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
      cancelLatestRecovery();
      generation += 1;
      stopRestorationWait();
      releaseTextRestoration(textRestoration);
      const anchor = wantedReader?.restorationAnchor ?? viewAnchor;
      textRestoration = anchor && !anchor.following ? {reader:wantedReader, anchor:copyViewReaderAnchor(anchor), attempt:wantedReader?.restorationAttempt ?? 0, demand:null, unsubscribe:null} : null;
      if (threadElement) conversationMotion(threadElement).cancel(positionMotion);
      if (placement) placement.cancelled = true;
      placement = threadPlacement(wantedReader, 'entry');
      readerOnScreen = wantedReader;
      settledOnOpening = false;
      pendingOpen = true;
      placementReady = false;
      entryPresentationPending = true;
      pendingPresentationTarget = null;
      liveArrivalPixels = 0;
      streamWrites.clear();
      arrivingMessages.clear();
      pendingFullMessageKey = null;
      newestMessageSequence = 0;
      noteMessageArrivals();
      heldSent = null;
      manualReader = anchor !== null && !anchor.following;
      retirementOwner = null;
      navigationPending = false;
      retainedTopInset = null;
      reservedSpacePixels = 0;
      answerRoomMessageId = null;
      sentMessageToPlace = null;
      newestPlacementMessageId = null;
      recoveryToPlace = null;
      withTheNewestLineBeforeTheWrite = viewAnchor === null || viewAnchor.following;
      latestPending = false;
      latestIntent = null;
      if (conversationOnScreen !== wanted) {
        conversationOnScreen = wanted;
        settledOnOpening = false;
        settledMessageIds = new Set();
        recoverySeen.clear();
        newestPlacementMessageId = null;
        reservedSpacePixels = 0;
        answerRoomMessageId = null;
        sentMessageToPlace = null;
        pendingOpen = true;
        withTheNewestLineBeforeTheWrite = true;
      }
      syncRestorationWait();
      requestReconciliation();
    });
  });

  /** Read the thread as it is now, before this update is written into it. */
  $effect.pre(() => {
    rows;
    reader?.revision;
    reader?.entryPhase;
    reader?.restoring;
    reader?.restorationOutcome;
    const attempt = reader?.restorationAttempt ?? 0;
    untrack(() => {
      if (textRestoration && textRestoration.attempt !== attempt) {
        generation += 1;
        releaseTextRestoration(textRestoration);
        const anchor = reader?.restorationAnchor ?? textRestoration.anchor;
        textRestoration = {...textRestoration, anchor:copyViewReaderAnchor(anchor), attempt, demand:null, unsubscribe:null};
        viewAnchor = copyViewReaderAnchor(anchor);
        pendingOpen = true;
        settledOnOpening = false;
        placementReady = false;
      }
      if (reader?.restorationOutcome === 'failed' || reader?.restorationOutcome === 'unavailable') fallBackRestoration();
      syncRestorationWait();
    });
    visibleRows;
    ownerReadThroughSequence;
    lens;
    outgoingMessages;
    threadElement;
    untrack(() => {
      const thread = threadElement;
      navigationPending = false;
      if (hasUnplacedAcceptedSend()) supersedeRestorationWithAcceptedSend();
      if (!manualReader && thread !== null && canMeasure() && !pendingOpen && !(latestPending && latestIntent?.cause === 'restoration-fallback') && heldSent === null && streamWrites.size === 0 && liveArrivalPixels === 0 && !conversationMotion(thread).active(positionMotion)) withTheNewestLineBeforeTheWrite = withTheNewestLine(thread);
      noteMessageArrivals();
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
      syncRestorationWait();
      if (!moved) return;
      // Changing height is not a reason to move the thread. Only coming back from
      // rest is, because the thread was not there to be read a moment ago.
      if (previous === "rest" && wanted !== "rest") {
        pendingOpen = true;
        // A plain reopen owns a fresh destination. Saved-address restoration keeps
        // its distinct manual ownership, including a reader replacement in flight.
        if (textRestoration === null && (viewAnchor === null || viewAnchor.following)) {
          manualReader = false;
          retirementOwner = null;
          navigationPending = false;
          if (answerRoomMessageId === null && heldSent === null) {
            reservedSpacePixels = 0;
            retainedTopInset = null;
          }
        }
      }
      requestReconciliation();
    });
  });

  $effect(() => {
    const thread = threadElement;
    const content = contentElement;
    if (thread === null || content === null) return;
    previousThreadHeight = thread.clientHeight;
    thread.addEventListener('conversation-text-presentation', onTextPresentation);
    const stopFollowing = conversationMotion(thread).following(keys => {
      if (!canMeasure()) return;
      const height = thread.clientHeight;
      const difference = previousThreadHeight - height;
      if (difference !== 0) navigationPending = false;
      previousThreadHeight = height;
      if (keys.includes(positionMotion)) return;
      if (difference !== 0 && heldSent === null && !pendingOpen) {
        // Size writes run first in the shared frame. Keep content fixed to the card bottom.
        moveTheThread(thread, thread.scrollTop + difference);
      }
      repairLayout(thread);
    });
    const observer = new ResizeObserver(() => {
      navigationPending = false;
      if (!canMeasure()) { dirty = true; return; }
      repairLayout(thread);
      requestReconciliation();
    });
    const acceptanceObserver = new MutationObserver(() => {
      navigationPending = false;
      if (hasUnplacedAcceptedSend()) supersedeRestorationWithAcceptedSend();
      repairLayout(thread);
      requestReconciliation();
    });
    acceptanceObserver.observe(content, {subtree:true, childList:true, attributes:true, attributeFilter:["data-conversation-accepted", "aria-busy", "data-markdown-rendering", "data-visible-content-state"]});
    observer.observe(thread);
    observer.observe(content);
    return () => { thread.removeEventListener('conversation-text-presentation', onTextPresentation); observer.disconnect(); acceptanceObserver.disconnect(); stopFollowing(); conversationMotion(thread).cancel(positionMotion); };
  });

  onMount(() => {
    const onVisible = (): void => {
      syncRestorationWait();
      if (document.visibilityState === "visible" && dirty) requestReconciliation();
    };
    document.addEventListener("visibilitychange", onVisible);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      disposed = true;
      cancelLatestRecovery();
      stopRestorationWait();
      releaseTextRestoration(textRestoration);
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
    style:padding-top={retainedTopInset !== null ? `${retainedTopInset}px` : undefined}
    data-conversation-thread
    data-conversation-placement-ready={placementReady}
    data-conversation-position-owner={latestPending ? "latest" : manualReader ? "manual" : heldSent !== null ? "sent" : "following"}
    role="region"
    aria-label="Conversation"
    tabindex="0"
    bind:this={threadElement}
    onloadcapture={() => { navigationPending = false; requestReconciliation(); }}
    onscroll={onThreadScroll}
    onscrollend={() => { navigationPending = false; }}
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
      <ProjectedTranscript {reader} {models} {ownSenderLabel} {livenessPulse} {outgoingMessages} {withdrawnDisplayIds} {acknowledgedMessageIds} {onStopDrawingMessage} {onSendAgain}/>
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
        style:margin-top={`${-tailGap}px`}
        aria-hidden="true"
      ></div>
    {/if}
  </div>
  {#if jumpVisible || newerHistory || newerThanUnplacedAnchor || latestPending || manualReader && reservedSpacePixels > 0}
    <button
      type="button"
      class="chat-jump"
      aria-label="Jump to latest message"
      onclick={(event) => void jumpToTheNewestLine(event.currentTarget)}
    >{belowCount > 0 ? `${belowCount} new ${belowCount === 1 ? "message" : "messages"}` : "Latest"}</button>
  {/if}
</div>

<style>
  /* Ordinary navigation stays reachable while exact transcript placement waits. */
  .chat-jump { visibility: visible; }
  /* The browser keeps the reader's place when history arrives above them. Nothing
     here competes with it, so it is left on. */
  /* Answer room is not content and never shrinks to make room for content. */
  .c2-reserved { flex: none; overflow-anchor: none; }
</style>

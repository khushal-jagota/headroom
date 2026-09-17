<script lang="ts">
  /** A conversation, bound to the system that runs it.
   *
   * The pane above this draws; this is what makes it live. It opens a conversation by id,
   * replays the rows after the one it holds and then keeps going, keeps the messages this
   * browser has sent that the record has not caught up with yet, and turns the pane's four
   * gestures — send, stop, answer, New — into the calls that do them.
   *
   * Opening, reloading, and opening on a second device are the same act: say which row you
   * hold and take everything after it. That is why nothing here is special-cased for a
   * reload, and why coming back to the tab is the same call as arriving.
   *
   * A caller with no conversation yet passes null. Nothing here starts one: the message is
   * what brings a conversation into being, and ``sendMessage`` says which one it went into.
   * So a Ticket nobody has run and a Ticket mid-turn are the same box, and there is no state
   * a person has to get themselves out of before they can type.
   */
  import { onMount, untrack, type Snippet } from "svelte";
  import ConversationPane from "./ConversationPane.svelte";
  import {
    conversationLiveness,
    createConversationStream,
    currentRunValues,
    emptyConversationFeed,
    type ConversationFeed,
    type ConversationStream
  } from "../../lib/conversation/feed";
  import { fateSentence, sendBodyFor, type RunValues } from "../../lib/conversation/composer";
  import { heldPromptRows } from "../../lib/conversation/heldPrompts";
  import {
    conversationFeedForLens,
    conversationRowsForLens,
    heldPromptIsInLens,
    type ConversationLens
  } from "../../lib/conversation/lens";
  import type { ConversationState } from "../../lib/conversation/conversationState";
  import {
    eligibleOwnerReadSequence,
    watchOwnerReadAttention
  } from "../../lib/conversation/ownerRead";
  import {
    afterTheRecordHasBeenRead,
    mintOutgoingMessage,
    moveRememberedOutgoingMessages,
    outgoingPersistenceConversationId,
    outgoingMessagesTheRecordHasNot,
    recallOutgoingMessages,
    releaseOutgoingMessageFiles,
    releaseOutgoingMessageImages,
    rememberOutgoingMessages,
    reserveOutgoingMessageFiles,
    reserveOutgoingMessageImages,
    reserveRecalledOutgoingMessages,
    type OutgoingMessage,
    type OutgoingMessageKnownFate
  } from "../../lib/conversation/outgoing";
  import {
    liveAskFrom,
    liveUserInputFrom,
    transcriptRows
  } from "../../lib/conversation/transcript";
  import {
    answerPermissionAsk,
    answerUserInput,
    advanceOwnerRead,
    discardHeldPrompt,
    interruptConversation,
    openConversationTail,
    promoteHeldPrompt,
    readConversation,
    readEventsAfter,
    ConversationWireError,
    type BackendSnapshot,
    type ConversationBackendKey,
    type ConversationStartValues,
    type ConversationView,
    type DeliveredMessage,
    type OwnerSendBody,
    type PromptDeliveryMode,
    type SentMessagePiece,
    type UserInputAnswers
  } from "../../lib/conversation/wire";

  let {
    conversationId = null,
    persistenceKey,
    label,
    backends = $bindable([]),
    senderLabel = "owner",
    startValues = null,
    runningBackendKey = $bindable(),
    conversationState = $bindable(null),
    composerPlaceholder,
    emptyState,
    sendMessage,
    onNewConversation,
    readOnly = false,
    ticketId = null
  }: {
    /** The conversation to show, or null for a caller that has not started one. */
    conversationId?: string | null;
    /** Stable owner identity used before the server assigns a Conversation id. */
    persistenceKey: string;
    /** Present only when this is the conversation owned by a Ticket. */
    ticketId?: string | null;
    label: string;
    backends?: readonly BackendSnapshot[];
    /** Who the messages sent from here are from. Recorded on the row, display-only. */
    senderLabel?: string;
    /** What a conversation started here right now would run on, as the owner that would
     *  start it answers. It is what the rail and the pickers show while there is no
     *  conversation, and it is only ever shown: a message still carries nothing but what
     *  somebody picked. Null is nobody having answered yet — the caller has not asked, or
     *  the read has not come back — and then there is nothing to show rather than a guess. */
    startValues?: ConversationStartValues | null;
    /** The backend in force, read back out for a caller that says so in its own words:
     *  what this conversation is running on, or what one started here would be. Null
     *  until one of those two can answer. */
    runningBackendKey?: ConversationBackendKey | null;
    /** How far open the conversation is, for a page that wants it as a layer over itself;
     *  null for one that does not, which is the conversation filling its container as it
     *  always has. The page says what it opens in by initialising what it binds; the
     *  conversation writes back when the person moves it. Nothing here reads it — it goes
     *  straight through to the pane, which is where the state is drawn. */
    conversationState?: ConversationState | null;
    composerPlaceholder?: string;
    emptyState?: Snippet;
    /** Hand a message to whoever owns this conversation, and say what became of it. A
     *  sender with no conversation is making one, and the answer names it. */
    sendMessage: (body: OwnerSendBody) => Promise<DeliveredMessage>;
    onNewConversation?: () => Promise<void>;
    /** One display boundary for historical transcripts. The pane removes every action. */
    readOnly?: boolean;
  } = $props();

  let view = $state<ConversationView | null>(null);
  let feed = $state<ConversationFeed>(emptyConversationFeed());
  /** How many frames have said the conversation is alive. Only its movement is read, and
   *  it is held apart from the feed so that saying "still working" costs the thread
   *  nothing: a frame with no words in it moves this and touches no row. */
  let livenessPulse = $state(0);
  /** What this browser has sent that the record does not have yet, oldest first. */
  let sentMessages = $state<readonly OutgoingMessage[]>([]);
  /** Local sends aimed at the queue before the server snapshot can name them. */
  let composerStackMessageIds = $state<readonly string[]>([]);
  let connectionTrouble = $state(false);
  let fateNote = $state<string | null>(null);
  let fateNoteIsTerminal = $state(false);
  let errorNote = $state<string | null>(null);
  let askNote = $state<string | null>(null);
  let busy = $state(false);
  let opening = $state(false);
  let lens = $state<ConversationLens>("focus");
  /** The conversation this component currently has open. It follows the prop, and a start
   *  sets it directly, because the message that caused the start has to go somewhere now
   *  rather than after the parent's own state has come back round. */
  let openedId = $state<string | null>(null);
  /** The identity under which this tab's optimistic rows are currently durable. It can
   *  remain the stable owner key when session storage refuses a canonical-id migration;
   *  record reconciliation then empties that exact key instead of orphaning it. */
  let activePersistenceId = $state<string | null>(null);
  let documentIsVisible = $state(false);
  let windowIsFocused = $state(false);
  /** Every browser attention event moves this value. Focus can return without changing
   *  the cached booleans, and that event must still retry rows that arrived while away. */
  let attentionPulse = $state(0);

  let stream: ConversationStream | null = null;

  /** Whether there is a conversation here at all. Holding an id is not the same as one
   *  existing: a Ticket names its conversation before a person opens the page, and this
   *  page puts an id in the address before anything has been started under it. Only the
   *  record answering is a conversation. */
  let started = $derived(view !== null);
  let liveness = $derived(
    conversationLiveness(
      feed,
      view === null ? null : { latestSequence: view.latest_sequence, isRunning: view.is_running }
    )
  );
  let visibleFeed = $derived(conversationFeedForLens(feed, lens, senderLabel));
  let rows = $derived(transcriptRows(feed, {
    turnStoppedWithoutAnEnding: liveness.turnStoppedWithoutAnEnding
  }));
  let visibleRows = $derived(conversationRowsForLens(
    transcriptRows(visibleFeed, {
      turnStoppedWithoutAnEnding: liveness.turnStoppedWithoutAnEnding
    }),
    lens
  ));
  let ask = $derived(liveAskFrom(visibleRows));
  let userInput = $derived(liveUserInputFrom(visibleRows));
  let advancingRead: { conversationId: string; sequence: number } | null = null;
  $effect(() => {
    void attentionPulse;
    if (view !== null) {
      const eligibleSequence = eligibleOwnerReadSequence({
        conversationState,
        documentIsVisible,
        // Focus can leave the top document through a preview iframe without a window
        // blur event. Ask the document again when a new row is about to be credited.
        windowIsFocused: windowIsFocused && document.hasFocus(),
        deliveredLatestSequence: lens === "focus" ? feed.latestSequence : 0,
        snapshot: {
          latestSequence: view.latest_sequence,
          ownerReadThroughSequence: view.owner_read_through_sequence
        }
      });
      if (
        eligibleSequence === null
        || (advancingRead?.conversationId === view.conversation_id
          && eligibleSequence <= advancingRead.sequence)
      ) return;
      const reading = view;
      advancingRead = {
        conversationId: reading.conversation_id,
        sequence: eligibleSequence
      };
      void advanceOwnerRead(reading.conversation_id, eligibleSequence)
        .then((advanced) => {
          if (view?.conversation_id === reading.conversation_id) {
            view = { ...view, owner_read_through_sequence: advanced };
          }
        })
        .catch(() => {
          if (advancingRead?.conversationId === reading.conversation_id) {
            advancingRead = null;
          }
        });
    }
  });

  onMount(() => watchOwnerReadAttention(document, window, (attention) => {
    documentIsVisible = attention.documentIsVisible;
    windowIsFocused = attention.windowIsFocused;
    attentionPulse += 1;
  }));
  let running = $derived(liveness.isRunning);
  // The conversation's own backend, and before there is one what starting it would use.
  // Those are the only two answers there are: a backend nobody has said is not shown.
  let backendKey = $derived<ConversationBackendKey | null>(
    view?.backend_key ?? startValues?.backend_key ?? null
  );
  $effect(() => {
    runningBackendKey = backendKey;
  });
  let backendSnapshot = $derived(
    backends.find((snapshot) => snapshot.backend_key === backendKey) ?? null
  );
  let current = $derived<RunValues>(
    currentRunValues(
      { model: view?.model ?? null, reasoningEffort: view?.reasoning_effort ?? null },
      feed
    )
  );
  let serverHeldSenderIds = $derived(
    new Set((view?.held_prompts ?? []).filter((held) =>
      heldPromptIsInLens(held, lens, senderLabel)
    ).flatMap((held) =>
      held.sender_message_id == null ? [] : [held.sender_message_id]
    ))
  );
  let stackOutgoingMessages = $derived(
    sentMessages.filter((message) =>
      composerStackMessageIds.includes(message.messageId)
      || message.knownFate === "waiting_for_the_agent"
      || serverHeldSenderIds.has(message.messageId)
    )
  );
  let transcriptOutgoingMessages = $derived(
    sentMessages.filter((message) =>
      !stackOutgoingMessages.some((stackMessage) => stackMessage.messageId === message.messageId)
    )
  );
  let visibleHeldRows = $derived(heldPromptRows(
    (view?.held_prompts ?? []).filter((held) => heldPromptIsInLens(held, lens, senderLabel)),
    stackOutgoingMessages
  ));

  // A queued or accepted-steer note describes traffic that a turn ending settles. A
  // refusal or uncertainty outlives endings: it is cleared by the next send, not by a
  // turn that never conclusively admitted it.
  $effect(() => {
    if (!running && fateNote !== null && !fateNoteIsTerminal) fateNote = null;
  });

  /** What the system said about itself last time it was asked, as far as turns go.
   *
   * A turn stopping is when it has something to say that the rows do not carry. The
   * commands an agent reports arrive moments after its session starts, which is during its
   * first turn — so a conversation opened before that turn was told none, and only asking
   * again puts that right. It is the same read a reconnect does, on an occasion that has
   * already happened rather than on a clock.
   */
  let wasRunning = false;
  $effect(() => {
    const nowRunning = running;
    if (wasRunning && !nowRunning) void refreshView();
    wasRunning = nowRunning;
  });

  // The caller changed which conversation this is. That happens when a Ticket's link is
  // written, when New clears it, and on the first render.
  $effect(() => {
    const wanted = conversationId;
    if (wanted === untrack(() => openedId)) return;
    if (wanted === null) {
      closeStream();
      openedId = null;
      lens = "focus";
      opening = false;
      view = null;
      feed = emptyConversationFeed();
      composerStackMessageIds = [];
      void adoptUnopenedPersistence(persistenceKey);
      return;
    }
    void adopt(wanted);
  });

  async function adopt(id: string): Promise<void> {
    closeStream();
    openedId = id;
    lens = "focus";
    view = null;
    feed = emptyConversationFeed();
    // A reload can happen after the first request reached the server but before its
    // response moved this tab's recovery pointer. Reconcile the owner's stable identity
    // into the canonical Conversation before looking for remembered messages there.
    const persistenceMoved = (
      persistenceKey !== id
        ? await moveRememberedOutgoingMessages(persistenceKey, id)
        : true
    );
    if (openedId !== id) return;
    activePersistenceId = persistenceMoved ? id : persistenceKey;
    if (!persistenceMoved) {
      errorNote = "The browser could not restore a pending file into this conversation.";
    }
    // Whatever this tab was still holding for this conversation when it was last here.
    const recalled = await recallOutgoingMessages(activePersistenceId);
    if (openedId !== id) return;
    sentMessages = reserveRecalledOutgoingMessages(recalled);
    composerStackMessageIds = sentMessages
      .filter((message) => message.knownFate === "waiting_for_the_agent")
      .map((message) => message.messageId);
    await openConversation(id);
  }

  async function adoptUnopenedPersistence(storageKey: string): Promise<void> {
    const recalled = reserveRecalledOutgoingMessages(await recallOutgoingMessages(storageKey));
    if (openedId !== null || conversationId !== null || persistenceKey !== storageKey) return;
    activePersistenceId = storageKey;
    sentMessages = recalled;
    composerStackMessageIds = sentMessages
      .filter((message) => message.knownFate === "waiting_for_the_agent")
      .map((message) => message.messageId);
  }

  function closeStream(): void {
    stream?.close();
    stream = null;
  }

  /** Open, reload, second tab, tab return — one path for all of them. */
  async function openConversation(id: string): Promise<void> {
    if (openedId !== id) return;
    opening = true;
    let snapshot: ConversationView;
    try {
      snapshot = await readConversation(id);
    } catch (error) {
      if (openedId !== id) return;
      if (error instanceof ConversationWireError && error.status === 404) {
        // A 404 is a conversation named but never started: the empty state, not a failure.
        // It is also an answer — there is no record and there never was — so anything this
        // tab brought back for it has been told as much as it is ever going to be.
        theRecordHasBeenRead();
      } else {
        errorNote = sentenceFor(error);
      }
      opening = false;
      return;
    }
    if (openedId !== id) return;
    view = snapshot;
    connectionTrouble = false;
    let localStream: ConversationStream;
    localStream = createConversationStream(
      id,
      {
        readEventsAfter,
        openTail: (tailId, after, handlers) =>
          openConversationTail(tailId, after, {
            onCommittedEvent: (event) => {
              if (openedId === id && stream === localStream) handlers.onCommittedEvent(event);
            },
            onLiveFrame: (frame) => {
              if (openedId === id && stream === localStream) handlers.onLiveFrame(frame);
            },
            onTrouble: () => {
              if (openedId !== id || stream !== localStream) return;
              connectionTrouble = true;
              handlers.onTrouble();
            }
          })
      },
      (next) => {
        if (openedId !== id || stream !== localStream) return;
        feed = next;
        // The record draws a message once it has it. A copy it now holds is not redrawn
        // somewhere else — it simply stops being drawn.
        const stillOutgoing = outgoingMessagesTheRecordHasNot(sentMessages, next.events);
        if (stillOutgoing !== sentMessages) void holdOnTo(stillOutgoing);
        connectionTrouble = false;
      },
      // Every connect asks the system about itself again, after the rows are in. This is
      // the after-a-restart path: the rows still leave a turn open, and only the system
      // can say nothing is running behind it any more.
      () => {
        if (openedId === id && stream === localStream) void refreshView(id);
      },
      () => {
        if (openedId === id && stream === localStream) void refreshView(id);
      },
      () => {
        if (openedId === id && stream === localStream) livenessPulse += 1;
      }
    );
    if (openedId !== id) {
      localStream.close();
      return;
    }
    stream = localStream;
    try {
      await localStream.connect();
      if (openedId !== id || stream !== localStream) return;
      theRecordHasBeenRead();
    } catch (error) {
      if (openedId !== id || stream !== localStream) return;
      connectionTrouble = true;
      errorNote = sentenceFor(error);
    } finally {
      if (openedId === id && stream === localStream) opening = false;
    }
  }

  /** The record has answered, so the messages brought back from before the page reloaded
   *  can be told what became of them.
   *
   * Everything the record turned out to have has already stopped being drawn — the rows
   * take those away themselves — so whatever is still here is something the record does
   * not have, and that is the point at which it is true to say nobody ever said whether it
   * arrived. Not one moment before: a browser that announces the uncertainty on its way to
   * looking puts a frightening sentence on a message that is about to turn out fine.
   */
  function theRecordHasBeenRead(): void {
    const told = afterTheRecordHasBeenRead(sentMessages);
    if (told !== sentMessages) void holdOnTo(told);
  }

  async function refreshView(requestedId: string | null = openedId): Promise<void> {
    if (requestedId === null || openedId !== requestedId) return;
    try {
      const refreshed = await readConversation(requestedId);
      if (openedId === requestedId) view = refreshed;
    } catch {
      // The rows are the record; a snapshot that did not come back changes nothing here.
    }
  }

  function sentenceFor(error: unknown): string {
    if (error instanceof ConversationWireError) return error.message;
    return error instanceof Error ? error.message : String(error);
  }

  /** Send, having already drawn the message.
   *
   * The message is this browser's before it is anybody else's: its words and pictures are
   * here, so it is given its id and the instant it was sent and put in the thread straight
   * away.
   *
   * How it ends decides what happens to the copy, and there are three endings rather than
   * two. The server saying no means this message reached nothing: the copy goes and its
   * content goes back to the person who composed it. The server saying yes means the copy waits for
   * its row. No answer at all is neither — the message may have arrived and may not, so
   * the copy stays saying exactly that and the words are not put back.
   */
  async function send(
    content: SentMessagePiece[],
    mode: PromptDeliveryMode,
    picked: RunValues
  ): Promise<boolean> {
    if (readOnly) return false;
    errorNote = null;
    fateNote = null;
    // Drawn before anything is asked of the network, including the start: the person has
    // written it and pressed Enter, so it is in the thread from that moment.
    const message = mintOutgoingMessage({
      content,
      senderLabel,
      mode
    });
    if (running) composerStackMessageIds = [...composerStackMessageIds, message.messageId];
    if (!reserveOutgoingMessageImages(message)) {
      errorNote = "Wait for an outstanding image message to reach the conversation.";
      return false;
    }
    if (!reserveOutgoingMessageFiles(message)) {
      releaseOutgoingMessageImages(message.messageId);
      errorNote = "Wait for an outstanding file message to reach the conversation.";
      return false;
    }
    const persistenceConversationId = outgoingPersistenceConversationId(
      openedId,
      conversationId ?? persistenceKey,
      message.messageId
    );
    if (!await holdOnTo([...sentMessages, message], persistenceConversationId)) {
      forgetUndurableMessage(message.messageId);
      errorNote = "The file could not be saved in this browser. It was returned to the composer.";
      return false;
    }
    activePersistenceId = persistenceConversationId;
    try {
      // The conversation this message is for is one the record has answered for. Holding
      // an id is not the same as one existing — a Ticket names its conversation before
      // this pane has read it, and the dev page puts a name in the address before anything
      // has been started under it — so an id nothing has answered for says none.
      const delivered = await sendMessage(
        sendBodyFor({ message, current, picked, conversationId: started ? openedId : null })
      );
      // The answer is the fate, with the conversation it happened in alongside it.
      const fate = delivered;
      if (
        delivered.conversation_id !== null
        && delivered.conversation_id !== persistenceConversationId
      ) {
        if (await moveRememberedOutgoingMessages(
          persistenceConversationId,
          delivered.conversation_id
        )) {
          activePersistenceId = delivered.conversation_id;
        } else {
          activePersistenceId = persistenceConversationId;
          errorNote = "The browser could not carry the pending file into this conversation.";
        }
      }
      if (delivered.conversation_id !== null && delivered.conversation_id !== openedId) {
        // The message made a conversation. Pointed at it and opened, without recalling:
        // what this browser is holding is the message being sent right now, which is newer
        // than anything remembered.
        openedId = delivered.conversation_id;
        lens = "focus";
        await openConversation(delivered.conversation_id);
      }
      const terminalFate = fate.fate === "refused" || fate.fate === "uncertain";
      fateNote = terminalFate ? fateSentence(fate) : null;
      fateNoteIsTerminal = terminalFate;
      if (terminalFate) {
        await stopDrawing(message.messageId);
        await refreshView();
        return false;
      }
      // Held for a busy agent: it has reached nothing yet, and it says so rather than
      // sitting there looking like a message something is answering.
      if (fate.fate === "queued") {
        await whatIsKnownAbout(message.messageId, "waiting_for_the_agent");
        if (!composerStackMessageIds.includes(message.messageId)) {
          composerStackMessageIds = [...composerStackMessageIds, message.messageId];
        }
      }
      await refreshView();
      return true;
    } catch (error) {
      errorNote = sentenceFor(error);
      if (theServerTurnedItAway(error)) {
        await stopDrawing(message.messageId);
        return false;
      }
      await whatIsKnownAbout(message.messageId, "answer_never_came_back");
      return true;
    }
  }

  /** Whether the server answered, and answered by rejecting the request itself.
   *
   * That is the only failure that says this text got nowhere. A request that never reached
   * the server, and one the server fell over part-way through, both leave the question
   * open — the send may have landed, and its row may be on its way.
   */
  function theServerTurnedItAway(error: unknown): boolean {
    return error instanceof ConversationWireError && error.status >= 400 && error.status < 500;
  }

  async function holdOnTo(
    messages: readonly OutgoingMessage[],
    persistenceConversationId: string | null = (
      activePersistenceId ?? openedId ?? conversationId ?? persistenceKey
    )
  ): Promise<boolean> {
    const stillHeld = new Set(messages.map((message) => message.messageId));
    for (const message of sentMessages) {
      if (!stillHeld.has(message.messageId)) {
        releaseOutgoingMessageImages(message.messageId);
        releaseOutgoingMessageFiles(message.messageId);
      }
    }
    sentMessages = messages;
    composerStackMessageIds = composerStackMessageIds.filter((messageId) => stillHeld.has(messageId));
    if (persistenceConversationId !== null) {
      return rememberOutgoingMessages(persistenceConversationId, messages);
    }
    return true;
  }

  function forgetUndurableMessage(messageId: string): void {
    sentMessages = sentMessages.filter((message) => message.messageId !== messageId);
    composerStackMessageIds = composerStackMessageIds.filter((candidate) => candidate !== messageId);
    releaseOutgoingMessageImages(messageId);
    releaseOutgoingMessageFiles(messageId);
  }

  function stopDrawing(messageId: string): Promise<boolean> {
    return holdOnTo(sentMessages.filter((message) => message.messageId !== messageId));
  }

  function whatIsKnownAbout(
    messageId: string,
    knownFate: OutgoingMessageKnownFate
  ): Promise<boolean> {
    return holdOnTo(
      sentMessages.map((message) =>
        message.messageId === messageId ? { ...message, knownFate } : message
      )
    );
  }

  async function stop(): Promise<void> {
    if (readOnly || openedId === null) return;
    try {
      await interruptConversation(openedId);
      await refreshView();
    } catch (error) {
      errorNote = sentenceFor(error);
    }
  }

  /** Throw away a message that is still waiting for the agent.
   *
   * The record is what takes the copy off the screen: a discarded message gets its own
   * row, and this browser stops drawing anything the record has a row for. So there is
   * nothing to undo here if the answer is no — the message has already run, and its row
   * is on its way.
   */
  async function discard(heldPromptId: string): Promise<void> {
    if (readOnly || openedId === null) return;
    errorNote = null;
    try {
      await discardHeldPrompt(openedId, heldPromptId);
      await refreshView();
    } catch (error) {
      errorNote = sentenceFor(error);
    }
  }

  async function promote(heldPromptId: string, mode: "send_now" | "steer"): Promise<void> {
    if (readOnly || openedId === null) return;
    errorNote = null;
    try {
      const result = await promoteHeldPrompt(openedId, heldPromptId, mode);
      if (result.promoted && (result.fate === "refused" || result.fate === "uncertain")) {
        fateNote = fateSentence(result);
        fateNoteIsTerminal = true;
      }
      await refreshView();
    } catch (error) {
      errorNote = sentenceFor(error);
    }
  }

  async function answer(optionId: string): Promise<void> {
    const askId = ask?.askId;
    if (readOnly || askId === undefined || openedId === null) return;
    askNote = null;
    busy = true;
    try {
      const { landed } = await answerPermissionAsk(openedId, askId, optionId);
      askNote = landed
        ? null
        : "The backend did not take that answer. Cancelling the turn always works.";
    } catch (error) {
      askNote = sentenceFor(error);
    } finally {
      busy = false;
    }
  }

  async function submitUserInput(answers: UserInputAnswers): Promise<void> {
    const requestId = userInput?.requestId;
    if (readOnly || requestId === undefined || openedId === null) return;
    askNote = null;
    busy = true;
    try {
      const { landed } = await answerUserInput(openedId, requestId, answers);
      askNote = landed
        ? null
        : "The backend did not take those answers. You can try again or stop the turn.";
    } catch (error) {
      askNote = sentenceFor(error);
    } finally {
      busy = false;
    }
  }

  /** New is the caller's to define, because what it means depends on what owns the
   *  conversation. All this does is let go of the one on screen once they have. */
  async function newConversation(): Promise<void> {
    if (readOnly || onNewConversation === undefined) return;
    try {
      await onNewConversation();
    } catch (error) {
      errorNote = sentenceFor(error);
      return;
    }
    closeStream();
    // The old conversation's activity has been killed, so nothing this tab was holding for
    // it is going anywhere. It is let go before the id changes, or it would be left behind
    // under a name nothing here answers to any more.
    void holdOnTo([]);
    composerStackMessageIds = [];
    openedId = null;
    lens = "focus";
    view = null;
    feed = emptyConversationFeed();
    fateNote = null;
    errorNote = null;
    askNote = null;
  }

  onMount(() => {
    const onVisible = (): void => {
      if (document.visibilityState !== "visible") return;
      // Coming back to the tab is the same read as opening it: what has happened since
      // the row this reader holds?
      const reconnectingStream = stream;
      const reconnectingId = openedId;
      if (reconnectingStream === null || reconnectingId === null) return;
      reconnectingStream.connect().catch(() => {
        if (stream === reconnectingStream && openedId === reconnectingId) {
          connectionTrouble = true;
        }
      });
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      closeStream();
    };
  });
</script>

<ConversationPane
  conversationId={openedId ?? ""}
  {ticketId}
  {label}
  {backendKey}
  conversationExists={started}
  bind:backends
  workspaceFolder={view?.workspace_folder ?? null}
  {rows}
  {visibleRows}
  outgoingMessages={transcriptOutgoingMessages}
  heldPromptRows={visibleHeldRows}
  supportsSteer={view?.supports_steer ?? false}
  ownSenderLabel={senderLabel}
  {livenessPulse}
  {running}
  {ask}
  {userInput}
  {askNote}
  {current}
  models={backendSnapshot?.available_models ?? []}
  effortOptions={backendSnapshot?.reasoning_effort_options ?? []}
  composerCatalog={view?.composer_catalog ?? []}
  startsOnModel={startValues?.model ?? null}
  startsOnReasoningEffort={startValues?.reasoning_effort ?? null}
  bind:conversationState
  {fateNote}
  {errorNote}
  {connectionTrouble}
  {readOnly}
  bind:lens
  composerPlaceholder={composerPlaceholder
    ?? (started ? `Message ${label}...` : "Send the first message to start it...")}
  composerDisabled={busy || opening}
  onSend={send}
  onStop={() => void stop()}
  onAnswer={(optionId) => void answer(optionId)}
  onSubmitUserInput={(answers) => void submitUserInput(answers)}
  onCancelTurn={() => void stop()}
  onDiscardHeldPrompt={(heldPromptId) => discard(heldPromptId)}
  onPromoteHeldPrompt={(heldPromptId, mode) => promote(heldPromptId, mode)}
  onNewConversation={() => void newConversation()}
  emptyState={emptyState === undefined ? undefined : beforeThereIsAConversation}
  showRunPicker={started || emptyState === undefined}
/>

<!-- Named apart from the prop it renders: a snippet takes the name it is declared with,
     so calling this one emptyState too would shadow the prop and render itself. -->
{#snippet beforeThereIsAConversation()}
  {#if !started && emptyState}
    {@render emptyState()}
  {/if}
{/snippet}

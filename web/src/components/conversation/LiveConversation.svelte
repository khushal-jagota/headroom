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
    conversationLensPreference,
    conversationRowsForLens,
    heldPromptIsInLens,
    rememberConversationLensPreference,
    type ConversationLens
  } from "../../lib/conversation/lens";
  import type { ConversationState } from "../../lib/conversation/conversationState";
  import {
    eligibleOwnerReadSequence,
    watchOwnerReadAttention
  } from "../../lib/conversation/ownerRead";
  import { connectionStatus } from "../../lib/changeStream";
  import {
    afterTheRecordHasBeenRead,
    anOutgoingMessageIsWaitingOnTheRecord,
    mintOutgoingMessage,
    moveRememberedOutgoingMessages,
    outgoingPersistenceConversationId,
    outgoingMessagesByPlace,
    outgoingMessagesTheRecordHasNot,
    recallOutgoingMessages,
    releaseOutgoingMessageFiles,
    releaseOutgoingMessageImages,
    rememberOutgoingMessages,
    reserveOutgoingMessageFiles,
    reserveOutgoingMessageImages,
    reserveRecalledOutgoingMessages,
    tellWhenAWaitRunsOut,
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
    type PastConversation,
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
    pastConversations = [],
    selectedPastConversationId = $bindable(null)
  }: {
    /** The conversation to show, or null for a caller that has not started one. */
    conversationId?: string | null;
    /** Stable owner identity used before the server assigns a Conversation id. */
    persistenceKey: string;
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
    /** The owner's earlier conversations, handed straight to the pane. The caller says
     *  which ones are past, because only it knows which conversation is the current one. */
    pastConversations?: readonly PastConversation[];
    /** Which earlier conversation the owner picked, or null for the current one. The
     *  caller opens what this names by passing it back as ``conversationId``. */
    selectedPastConversationId?: string | null;
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
  let lens = $state<ConversationLens>(conversationLensPreference());
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

  $effect(() => {
    rememberConversationLensPreference(lens);
  });

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
        deliveredLatestSequence: feed.latestSequence,
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
  let placedOutgoingMessages = $derived(outgoingMessagesByPlace(sentMessages, {
    composerStackMessageIds,
    serverHeldSenderMessageIds: serverHeldSenderIds
  }));
  let stackOutgoingMessages = $derived(placedOutgoingMessages.composerStack);
  let transcriptOutgoingMessages = $derived(placedOutgoingMessages.thread);
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
    let theOpenAlreadyReadTheView = true;
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
      // can say nothing is running behind it any more. The first connect of an open is
      // the exception: the view it would ask for was read a moment ago, by the open.
      () => {
        if (openedId !== id || stream !== localStream) return;
        if (theOpenAlreadyReadTheView) {
          theOpenAlreadyReadTheView = false;
          return;
        }
        void refreshView(id);
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

  /** Reads of the view, in a queue of one, so a burst of them is one request.
   *
   * Opening a conversation reads the view, and then the tail immediately says it is
   * connected and hands over its held prompts. Each of those asks for the view again,
   * about the same conversation at the same moment. A caller that arrives while a read
   * is in flight waits behind it rather than starting its own — but it does start its
   * own once that one lands, because it knows something the read in flight did not, and
   * an answer fetched before its fact existed is not an answer to it.
   */
  let viewBeingRead: { conversationId: string; done: Promise<void> } | null = null;

  function refreshView(requestedId: string | null = openedId): Promise<void> {
    if (requestedId === null || openedId !== requestedId) return Promise.resolve();
    const inFlight = viewBeingRead;
    const read = async (): Promise<void> => {
      if (openedId !== requestedId) return;
      try {
        const refreshed = await readConversation(requestedId);
        if (openedId === requestedId) view = refreshed;
      } catch {
        // The rows are the record; a snapshot that did not come back changes nothing.
      }
    };
    const reading =
      inFlight?.conversationId === requestedId ? inFlight.done.then(read) : read();
    const entry = { conversationId: requestedId, done: reading };
    viewBeingRead = entry;
    void reading.finally(() => {
      if (viewBeingRead === entry) viewBeingRead = null;
    });
    return reading;
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
      mode,
      pickedModel: picked.model,
      pickedReasoningEffort: picked.reasoningEffort
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

  /** Send the same words again, as a new message.
   *
   * A new name and a new instant, because that is what this is. The first one may have
   * arrived, and nothing here retries a message behind a person's back — so if it did
   * arrive, the conversation ends up with two, which is the person's choice to make and
   * not this browser's guess.
   *
   * The uncertain copy goes first and comes back if the send does not get away. That order
   * is what lets a message carrying pictures or files be sent again at all: the tab's byte
   * budget counts every copy it is holding, and two copies of the same attachment would
   * not fit through it.
   */
  async function sendTheseWordsAgain(senderMessageId: string): Promise<void> {
    const uncertain = sentMessages.find((message) => message.messageId === senderMessageId);
    if (uncertain === undefined) return;
    await stopDrawing(senderMessageId);
    const away = await send(uncertain.content, uncertain.mode, {
      model: uncertain.pickedModel ?? null,
      reasoningEffort: uncertain.pickedReasoningEffort ?? null
    });
    if (away) return;
    // The server turned it away, so the words are nowhere. Put the copy back rather than
    // let a person lose what they wrote to a button they pressed.
    if (!reserveOutgoingMessageImages(uncertain)) return;
    if (!reserveOutgoingMessageFiles(uncertain)) {
      releaseOutgoingMessageImages(senderMessageId);
      return;
    }
    await holdOnTo([...sentMessages, uncertain]);
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
    view = null;
    feed = emptyConversationFeed();
    fateNote = null;
    errorNote = null;
    askNote = null;
  }

  /** Read the record again, and tell whatever is waiting on it what the record said.
   *
   * Reading and being told are one thing rather than two. A copy brought back from before
   * the page reloaded says nothing while it waits for the record, so a read that stops
   * short leaves it waiting for a read that has already happened.
   *
   * A conversation switched away from part-way through loses its say: the trouble mark and
   * the telling both belong to the reader that is still the current one.
   */
  function readTheRecordAgain(): void {
    const reconnectingStream = stream;
    const reconnectingId = openedId;
    if (reconnectingId === null) return;
    if (reconnectingStream === null) {
      // The first read never got through, so there is no reader here to reconnect. Opening
      // is that same read from the start, and it is the one path for coming back to a
      // conversation — including a tab that came back before the server did.
      if (!opening) void openConversation(reconnectingId);
      return;
    }
    const stillTheCurrentReader = (): boolean =>
      stream === reconnectingStream && openedId === reconnectingId;
    reconnectingStream.connect().then(
      () => {
        if (stillTheCurrentReader()) theRecordHasBeenRead();
      },
      () => {
        if (stillTheCurrentReader()) connectionTrouble = true;
      }
    );
  }

  // A send whose answer never came must stop looking like one that is still on its way.
  // Both the rule and its wake-up belong to the module: this only says which list is
  // waiting and what to do once the waiting is over.
  $effect(() => tellWhenAWaitRunsOut(sentMessages, (told) => void holdOnTo(told)));

  onMount(() => {
    // The record is the only thing that can settle a send this tab never heard an answer
    // for, and most of the time it already has the answer: the row was written and this
    // browser was not listening. So when the server comes back, read again. Coming back is
    // not something this pane has to work out — the app's own connection says it, and a
    // reconnect is what everything else on screen reacts to as well.
    let reachable = false;
    const stopWatchingTheConnection = connectionStatus.subscribe((status) => {
      const nowReachable = status === "connected";
      const cameBack = nowReachable && !reachable;
      reachable = nowReachable;
      if (!cameBack) return;
      if (!anOutgoingMessageIsWaitingOnTheRecord(sentMessages)) return;
      readTheRecordAgain();
    });
    const onVisible = (): void => {
      if (document.visibilityState !== "visible") return;
      // Coming back to the tab is the same read as opening it: what has happened since
      // the row this reader holds?
      readTheRecordAgain();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopWatchingTheConnection();
      document.removeEventListener("visibilitychange", onVisible);
      closeStream();
    };
  });
</script>

<ConversationPane
  conversationId={openedId ?? ""}
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
  {pastConversations}
  pastConversationsLabel={`${label} conversation`}
  bind:selectedPastConversationId
  ownerReadThroughSequence={view?.owner_read_through_sequence ?? 0}
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
  onStopDrawingHeldPrompt={(senderMessageId) => void stopDrawing(senderMessageId)}
  onSendHeldPromptAgain={(senderMessageId) => sendTheseWordsAgain(senderMessageId)}
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

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
  import type { ConversationState } from "../../lib/conversation/conversationState";
  import {
    mintOutgoingMessage,
    outgoingMessagesTheRecordHasNot,
    recallOutgoingMessages,
    rememberOutgoingMessages,
    type OutgoingMessage,
    type OutgoingMessageKnownFate
  } from "../../lib/conversation/outgoing";
  import { liveAskFrom, transcriptRows } from "../../lib/conversation/transcript";
  import { writeReplyWatermark } from "../../lib/replyWatermark";
  import {
    answerPermissionAsk,
    discardHeldPrompt,
    interruptConversation,
    openConversationTail,
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
    type SentMessagePiece
  } from "../../lib/conversation/wire";

  let {
    conversationId = null,
    label,
    backends = [],
    senderLabel = "owner",
    startValues = null,
    runningBackendKey = $bindable(),
    conversationState = $bindable(null),
    composerPlaceholder,
    emptyState,
    sendMessage,
    onNewConversation,
    onMessageAccepted
  }: {
    /** The conversation to show, or null for a caller that has not started one. */
    conversationId?: string | null;
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
    /** A message typed here reached the conversation — started, held, or steered into the
     *  running turn. Not called for a refusal, which reached nothing. What that means is
     *  the caller's business; this only says it happened. */
    onMessageAccepted?: () => Promise<void>;
  } = $props();

  let view = $state<ConversationView | null>(null);
  let feed = $state<ConversationFeed>(emptyConversationFeed());
  /** What this browser has sent that the record does not have yet, oldest first. */
  let sentMessages = $state<readonly OutgoingMessage[]>([]);
  let connectionTrouble = $state(false);
  let fateNote = $state<string | null>(null);
  let fateNoteIsRefusal = $state(false);
  let errorNote = $state<string | null>(null);
  let askNote = $state<string | null>(null);
  let busy = $state(false);
  let opening = $state(false);
  /** The conversation this component currently has open. It follows the prop, and a start
   *  sets it directly, because the message that caused the start has to go somewhere now
   *  rather than after the parent's own state has come back round. */
  let openedId = $state<string | null>(null);

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
  let rows = $derived(
    transcriptRows(feed, {
      turnStoppedWithoutAnEnding: liveness.turnStoppedWithoutAnEnding
    })
  );
  let ask = $derived(liveAskFrom(rows));
  // Looking at a conversation is what reading it means. While this pane is showing one,
  // the reader has seen it as far as the record goes — including mid-turn, because a
  // turn that has not ended yet is not a reply waiting for anybody. The board's reply
  // mark is drawn from this and from nothing else.
  $effect(() => {
    if (view !== null) writeReplyWatermark(view.conversation_id, view.latest_sequence);
  });
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

  // A queued or steered note describes traffic that a turn ending settles. A refusal
  // outlives endings: it is cleared by the next send, not by a turn it never touched.
  $effect(() => {
    if (!running && fateNote !== null && !fateNoteIsRefusal) fateNote = null;
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
      view = null;
      feed = emptyConversationFeed();
      holdOnTo([]);
      return;
    }
    void adopt(wanted);
  });

  async function adopt(id: string): Promise<void> {
    closeStream();
    openedId = id;
    view = null;
    feed = emptyConversationFeed();
    // Whatever this tab was still holding for this conversation when it was last here.
    sentMessages = recallOutgoingMessages(id);
    await openConversation(id);
  }

  function closeStream(): void {
    stream?.close();
    stream = null;
  }

  /** Open, reload, second tab, tab return — one path for all of them. */
  async function openConversation(id: string): Promise<void> {
    opening = true;
    try {
      view = await readConversation(id);
    } catch (error) {
      if (!(error instanceof ConversationWireError && error.status === 404)) {
        errorNote = sentenceFor(error);
      }
      // A 404 is a conversation named but never started: the empty state, not a failure.
      opening = false;
      return;
    }
    connectionTrouble = false;
    stream = createConversationStream(
      id,
      {
        readEventsAfter,
        openTail: (tailId, after, handlers) =>
          openConversationTail(tailId, after, {
            onCommittedEvent: handlers.onCommittedEvent,
            onLiveFrame: handlers.onLiveFrame,
            onTrouble: () => {
              connectionTrouble = true;
              handlers.onTrouble();
            }
          })
      },
      (next) => {
        feed = next;
        // The record draws a message once it has it. A copy it now holds is not redrawn
        // somewhere else — it simply stops being drawn.
        const stillOutgoing = outgoingMessagesTheRecordHasNot(sentMessages, next.events);
        if (stillOutgoing !== sentMessages) holdOnTo(stillOutgoing);
        connectionTrouble = false;
      },
      // Every connect asks the system about itself again, after the rows are in. This is
      // the after-a-restart path: the rows still leave a turn open, and only the system
      // can say nothing is running behind it any more.
      () => void refreshView()
    );
    try {
      await stream.connect();
    } catch (error) {
      connectionTrouble = true;
      errorNote = sentenceFor(error);
    } finally {
      opening = false;
    }
  }

  async function refreshView(): Promise<void> {
    if (openedId === null) return;
    try {
      view = await readConversation(openedId);
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
    errorNote = null;
    fateNote = null;
    // Drawn before anything is asked of the network, including the start: the person has
    // written it and pressed Enter, so it is in the thread from that moment.
    const message = mintOutgoingMessage({
      content,
      senderLabel,
      mode
    });
    holdOnTo([...sentMessages, message]);
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
      if (delivered.conversation_id !== null && delivered.conversation_id !== openedId) {
        // The message made a conversation. Pointed at it and opened, without recalling:
        // what this browser is holding is the message being sent right now, which is newer
        // than anything remembered.
        openedId = delivered.conversation_id;
        await openConversation(delivered.conversation_id);
      }
      fateNote = fateSentence(fate);
      fateNoteIsRefusal = fate.fate === "refused";
      if (fate.fate === "refused") {
        stopDrawing(message.messageId);
        await refreshView();
        return false;
      }
      // Held for a busy agent: it has reached nothing yet, and it says so rather than
      // sitting there looking like a message something is answering.
      if (fate.fate === "queued") whatIsKnownAbout(message.messageId, "waiting_for_the_agent");
      await refreshView();
      // Told after the conversation took it, and never for a refusal: a message that
      // reached nothing is not something a caller should act on.
      await onMessageAccepted?.();
      return true;
    } catch (error) {
      errorNote = sentenceFor(error);
      if (theServerTurnedItAway(error)) {
        stopDrawing(message.messageId);
        return false;
      }
      whatIsKnownAbout(message.messageId, "answer_never_came_back");
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

  function holdOnTo(messages: readonly OutgoingMessage[]): void {
    sentMessages = messages;
    if (openedId !== null) rememberOutgoingMessages(openedId, messages);
  }

  function stopDrawing(messageId: string): void {
    holdOnTo(sentMessages.filter((message) => message.messageId !== messageId));
  }

  function whatIsKnownAbout(messageId: string, knownFate: OutgoingMessageKnownFate): void {
    holdOnTo(
      sentMessages.map((message) =>
        message.messageId === messageId ? { ...message, knownFate } : message
      )
    );
  }

  async function stop(): Promise<void> {
    if (openedId === null) return;
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
  async function discard(messageId: string): Promise<void> {
    if (openedId === null) return;
    errorNote = null;
    try {
      await discardHeldPrompt(openedId, messageId);
      await refreshView();
    } catch (error) {
      errorNote = sentenceFor(error);
    }
  }

  async function answer(optionId: string): Promise<void> {
    const askId = ask?.askId;
    if (askId === undefined || openedId === null) return;
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

  /** New is the caller's to define, because what it means depends on what owns the
   *  conversation. All this does is let go of the one on screen once they have. */
  async function newConversation(): Promise<void> {
    if (onNewConversation === undefined) return;
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
    holdOnTo([]);
    openedId = null;
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
      stream?.connect().catch(() => (connectionTrouble = true));
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
  {label}
  {backendKey}
  conversationExists={started}
  {backends}
  workspaceFolder={view?.workspace_folder ?? null}
  {rows}
  outgoingMessages={sentMessages}
  ownSenderLabel={senderLabel}
  livenessPulse={feed.livenessPulse}
  {running}
  {ask}
  {askNote}
  {current}
  models={backendSnapshot?.available_models ?? []}
  effortOptions={backendSnapshot?.reasoning_effort_options ?? []}
  availableCommands={view?.available_commands ?? []}
  startsOnModel={startValues?.model ?? null}
  startsOnReasoningEffort={startValues?.reasoning_effort ?? null}
  heldPromptCount={view?.held_prompt_count ?? 0}
  bind:conversationState
  {fateNote}
  {errorNote}
  {connectionTrouble}
  composerPlaceholder={composerPlaceholder
    ?? (started ? `Message ${label}...` : "Send the first message to start it...")}
  composerDisabled={busy || opening}
  onSend={send}
  onStop={() => void stop()}
  onAnswer={(optionId) => void answer(optionId)}
  onCancelTurn={() => void stop()}
  onDiscardHeldPrompt={(messageId) => void discard(messageId)}
  onNewConversation={() => void newConversation()}
  emptyState={emptyState === undefined ? undefined : beforeThereIsAConversation}
/>

<!-- Named apart from the prop it renders: a snippet takes the name it is declared with,
     so calling this one emptyState too would shadow the prop and render itself. -->
{#snippet beforeThereIsAConversation()}
  {#if !started && emptyState}
    {@render emptyState()}
  {/if}
{/snippet}

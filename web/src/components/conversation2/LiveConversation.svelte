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
   * A caller with no conversation yet passes null and an ``onStartConversation``. The first
   * message is what starts one: it is not a state the person has to get themselves out of
   * before they can type, so a Ticket nobody has run and a Ticket mid-turn are the same box.
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
  } from "../../lib/conversation2/feed";
  import { fateSentence, sendBodyFor, type RunValues } from "../../lib/conversation2/composer";
  import {
    mintOutgoingMessage,
    outgoingMessagesTheRecordHasNot,
    recallOutgoingMessages,
    rememberOutgoingMessages,
    type OutgoingMessage,
    type OutgoingMessageKnownFate
  } from "../../lib/conversation2/outgoing";
  import { liveAskFrom, transcriptRows } from "../../lib/conversation2/transcript";
  import {
    answerPermissionAsk,
    interruptConversation,
    openConversationTail,
    readConversation,
    readEventsAfter,
    sendPrompt,
    ConversationWireError,
    type BackendSnapshot,
    type ConversationBackendKey,
    type ConversationView,
    type PromptDeliveryMode
  } from "../../lib/conversation2/wire";

  let {
    conversationId = null,
    label,
    backends = [],
    senderLabel = "owner",
    fallbackBackendKey = "codex",
    runningBackendKey = $bindable(),
    composerPlaceholder,
    emptyState,
    onStartConversation,
    onNewConversation
  }: {
    /** The conversation to show, or null for a caller that has not started one. */
    conversationId?: string | null;
    label: string;
    backends?: readonly BackendSnapshot[];
    /** Who the messages sent from here are from. Recorded on the row, display-only. */
    senderLabel?: string;
    /** Which backend's models the pickers offer before a conversation exists to ask. */
    fallbackBackendKey?: ConversationBackendKey;
    /** The backend this conversation is actually running on, read back out for a caller
     *  that says so in its own words. Only the started conversation's record knows it. */
    runningBackendKey?: ConversationBackendKey;
    composerPlaceholder?: string;
    emptyState?: Snippet;
    /** Start one and say what it is called. Absent means this caller cannot start one, and
     *  the composer says so rather than swallowing what was typed. */
    onStartConversation?: () => Promise<string | null>;
    onNewConversation?: () => Promise<void>;
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
  let running = $derived(liveness.isRunning);
  let backendKey = $derived<ConversationBackendKey>(view?.backend_key ?? fallbackBackendKey);
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
   * The message is this browser's before it is anybody else's: the text is here, so it is
   * given its id and the instant it was sent and put in the thread straight away.
   *
   * How it ends decides what happens to the copy, and there are three endings rather than
   * two. The server saying no means this text reached nothing: the copy goes and the words
   * go back to the person who wrote them. The server saying yes means the copy waits for
   * its row. No answer at all is neither — the message may have arrived and may not, so
   * the copy stays saying exactly that and the words are not put back.
   */
  async function send(
    text: string,
    mode: PromptDeliveryMode,
    picked: RunValues
  ): Promise<boolean> {
    errorNote = null;
    fateNote = null;
    // Drawn before anything is asked of the network, including the start: the person has
    // written it and pressed Enter, so it is in the thread from that moment.
    const message = mintOutgoingMessage({ text, senderLabel, mode });
    holdOnTo([...sentMessages, message]);
    try {
      let id = openedId;
      if (!started) {
        if (onStartConversation === undefined) {
          errorNote = "There is no conversation here to send into.";
          stopDrawing(message.messageId);
          return false;
        }
        id = await onStartConversation();
        if (id === null) {
          errorNote = "The conversation could not be started.";
          stopDrawing(message.messageId);
          return false;
        }
        // Pointed at it and opened, without recalling: what this browser is holding is the
        // message being sent right now, which is newer than anything remembered.
        openedId = id;
        await openConversation(id);
      }
      if (id === null) {
        errorNote = "There is no conversation here to send into.";
        stopDrawing(message.messageId);
        return false;
      }
      const fate = await sendPrompt(id, sendBodyFor({ message, current, picked }));
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
  {label}
  {backendKey}
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
  defaultModelId={backendSnapshot?.default_model_id ?? null}
  defaultReasoningEffort={backendSnapshot?.default_reasoning_effort ?? null}
  heldPromptCount={view?.held_prompt_count ?? 0}
  {fateNote}
  {errorNote}
  {connectionTrouble}
  composerPlaceholder={composerPlaceholder
    ?? (started ? "Message the agent..." : "Send the first message to start it...")}
  composerDisabled={busy || opening}
  onSend={send}
  onStop={() => void stop()}
  onAnswer={(optionId) => void answer(optionId)}
  onCancelTurn={() => void stop()}
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

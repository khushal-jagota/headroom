<script lang="ts">
  /** The conversation system on a page of its own, for looking at it while it is built.
   *
   * The conversation's id lives in the address, which is what makes reloading and opening
   * a second tab the ordinary path rather than a special one: both of them say which row
   * they hold and take everything after it. Nothing here is linked from the app's own
   * navigation, and nothing the app does touches this route.
   */
  import { onMount } from "svelte";
  import BackendCard from "../components/conversation2/BackendCard.svelte";
  import ConversationPane from "../components/conversation2/ConversationPane.svelte";
  import NewConversationForm from "../components/conversation2/NewConversationForm.svelte";
  import {
    conversationLiveness,
    createConversationStream,
    currentRunValues,
    emptyConversationFeed,
    type ConversationFeed,
    type ConversationStream
  } from "../lib/conversation2/feed";
  import { fateSentence, sendBodyFor, type RunValues } from "../lib/conversation2/composer";
  import { liveAskFrom, transcriptRows } from "../lib/conversation2/transcript";
  import {
    answerPermissionAsk,
    interruptConversation,
    killConversation,
    openConversationTail,
    readBackends,
    readConversation,
    readEventsAfter,
    sendPrompt,
    startConversation,
    updateBackend,
    ConversationWireError,
    type BackendSnapshot,
    type BackendUpdateResult,
    type ConversationBackendKey,
    type ConversationView,
    type PromptDeliveryMode
  } from "../lib/conversation2/wire";

  const SENDER_LABEL = "owner";
  const DEFAULT_WORKSPACE_FOLDER = "~/Coding";

  let conversationId = $state(readIdFromAddress() ?? mintConversationId());
  let view = $state<ConversationView | null>(null);
  let feed = $state<ConversationFeed>(emptyConversationFeed());
  let backends = $state<BackendSnapshot[]>([]);
  let updatingBackend = $state<ConversationBackendKey | null>(null);
  let updateResults = $state<Partial<Record<ConversationBackendKey, BackendUpdateResult>>>({});
  let connectionTrouble = $state(false);
  let fateNote = $state<string | null>(null);
  let fateNoteIsRefusal = $state(false);
  let errorNote = $state<string | null>(null);
  let askNote = $state<string | null>(null);
  let busy = $state(false);
  let opening = $state(false);

  // What a conversation that has not been started yet would be started as.
  let newBackendKey = $state<ConversationBackendKey>("codex");
  let newModel = $state<string | null>(null);
  let newReasoningEffort = $state<string | null>(null);
  let newWorkspaceFolder = $state(DEFAULT_WORKSPACE_FOLDER);

  let stream: ConversationStream | null = null;

  let started = $derived(view !== null);
  // The rows are the record and they are almost always the fresher of the two, so they
  // win. The one thing they cannot say is that a turn stopped without an ending — ending
  // a turn is a row, and a server that went away mid-turn wrote none — so a snapshot that
  // had already seen every row this reader holds is believed about that.
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

  // A queued or steered note describes traffic that a turn ending settles — the held
  // message has run, the steered text was taken. A refusal outlives endings: it is
  // cleared by the next send, not by a turn it never touched.
  $effect(() => {
    if (!running && fateNote !== null && !fateNoteIsRefusal) fateNote = null;
  });
  let backendKey = $derived<ConversationBackendKey>(view?.backend_key ?? newBackendKey);
  let backendSnapshot = $derived(
    backends.find((snapshot) => snapshot.backend_key === backendKey) ?? null
  );
  let current = $derived<RunValues>(
    currentRunValues(
      { model: view?.model ?? null, reasoningEffort: view?.reasoning_effort ?? null },
      feed
    )
  );

  function readIdFromAddress(): string | null {
    const hash = window.location.hash;
    const at = hash.indexOf("?");
    if (at < 0) return null;
    return new URLSearchParams(hash.slice(at + 1)).get("id");
  }

  function mintConversationId(): string {
    return `dev-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  }

  function writeIdToAddress(id: string, { replace = false } = {}): void {
    const address = `#/dev/conversation?id=${encodeURIComponent(id)}`;
    if (replace) window.location.replace(address);
    else window.location.hash = address;
  }

  /** Open, reload, second tab, tab return — one path for all of them. */
  async function openConversation(): Promise<void> {
    opening = true;
    stream?.close();
    stream = null;
    feed = emptyConversationFeed();
    view = null;
    try {
      view = await readConversation(conversationId);
    } catch (error) {
      if (!(error instanceof ConversationWireError && error.status === 404)) {
        errorNote = sentenceFor(error);
      }
      // A 404 is a conversation that was named but never started. That is the empty
      // state, not a failure.
      opening = false;
      return;
    }
    connectionTrouble = false;
    stream = createConversationStream(
      conversationId,
      {
        readEventsAfter,
        openTail: (id, after, handlers) =>
          openConversationTail(id, after, {
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
        connectionTrouble = false;
      },
      // Every connect asks the system about itself again, after the rows are in. This is
      // the after-a-restart path: the rows still leave a turn open, and only the system
      // can say that nothing is running behind it any more.
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
    try {
      view = await readConversation(conversationId);
    } catch {
      // The rows are the record; a snapshot that did not come back changes nothing here.
    }
  }

  function sentenceFor(error: unknown): string {
    if (error instanceof ConversationWireError) return error.message;
    return error instanceof Error ? error.message : String(error);
  }

  async function send(
    text: string,
    mode: PromptDeliveryMode,
    picked: RunValues
  ): Promise<boolean> {
    errorNote = null;
    fateNote = null;
    try {
      if (!started) {
        view = await startConversation({
          conversation_id: conversationId,
          backend_key: newBackendKey,
          model: newModel,
          reasoning_effort: newReasoningEffort,
          // Left out when untouched, so the server's own default folder applies.
          ...(newWorkspaceFolder === DEFAULT_WORKSPACE_FOLDER
            ? {}
            : { workspace_folder: newWorkspaceFolder })
        });
        writeIdToAddress(conversationId);
        await openConversation();
      }
      const fate = await sendPrompt(
        conversationId,
        sendBodyFor({ text, senderLabel: SENDER_LABEL, mode, current, picked })
      );
      fateNote = fateSentence(fate);
      fateNoteIsRefusal = fate.fate === "refused";
      await refreshView();
      return true;
    } catch (error) {
      errorNote = sentenceFor(error);
      return false;
    }
  }

  async function stop(): Promise<void> {
    try {
      await interruptConversation(conversationId);
      await refreshView();
    } catch (error) {
      errorNote = sentenceFor(error);
    }
  }

  async function answer(optionId: string): Promise<void> {
    const askId = ask?.askId;
    if (askId === undefined) return;
    askNote = null;
    busy = true;
    try {
      const { landed } = await answerPermissionAsk(conversationId, askId, optionId);
      askNote = landed
        ? null
        : "The backend did not take that answer. Cancelling the turn always works.";
    } catch (error) {
      askNote = sentenceFor(error);
    } finally {
      busy = false;
    }
  }

  /** New kills the old one first: freeing the agent would let its held messages run,
   *  and starting again must not be the thing that finally delivers them. */
  async function newConversation(): Promise<void> {
    if (started) {
      try {
        await killConversation(conversationId);
      } catch (error) {
        errorNote = sentenceFor(error);
      }
    }
    stream?.close();
    stream = null;
    conversationId = mintConversationId();
    writeIdToAddress(conversationId);
    view = null;
    feed = emptyConversationFeed();
    fateNote = null;
    errorNote = null;
    askNote = null;
  }

  async function loadBackends(refresh = false): Promise<void> {
    try {
      backends = await readBackends(refresh);
    } catch (error) {
      errorNote = sentenceFor(error);
    }
  }

  async function runBackendUpdate(key: ConversationBackendKey): Promise<void> {
    updatingBackend = key;
    try {
      updateResults = { ...updateResults, [key]: await updateBackend(key) };
      await loadBackends();
    } catch (error) {
      errorNote = sentenceFor(error);
    } finally {
      updatingBackend = null;
    }
  }

  onMount(() => {
    if (readIdFromAddress() === null) writeIdToAddress(conversationId, { replace: true });
    void openConversation();
    void loadBackends();
    const onVisible = (): void => {
      if (document.visibilityState !== "visible") return;
      // Coming back to the tab is the same read as opening it: what has happened since
      // the row this reader holds?
      stream?.connect().catch(() => (connectionTrouble = true));
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      stream?.close();
      stream = null;
    };
  });
</script>

<div class="c2-route" data-conversation2-route>
  <div class="c2-route-pane">
    <ConversationPane
      label={`${backendKey} · ${conversationId}`}
      {backendKey}
      workspaceFolder={view?.workspace_folder ?? null}
      {rows}
      ownSenderLabel={SENDER_LABEL}
      {running}
      {ask}
      {askNote}
      {current}
      models={backendSnapshot?.available_models ?? []}
      effortOptions={backendSnapshot?.reasoning_effort_options ?? []}
      heldPromptCount={view?.held_prompt_count ?? 0}
      {fateNote}
      {errorNote}
      {connectionTrouble}
      composerPlaceholder={started ? "Message the agent..." : "Send the first message to start it..."}
      composerDisabled={busy || opening}
      onSend={send}
      onStop={() => void stop()}
      onAnswer={(optionId) => void answer(optionId)}
      onCancelTurn={() => void stop()}
      onNewConversation={() => void newConversation()}
    >
      {#snippet emptyState()}
        {#if !started}
          <NewConversationForm
            {conversationId}
            {backends}
            bind:backendKey={newBackendKey}
            bind:model={newModel}
            bind:reasoningEffort={newReasoningEffort}
            bind:workspaceFolder={newWorkspaceFolder}
          />
        {/if}
      {/snippet}
    </ConversationPane>
  </div>

  <aside class="c2-route-backends" aria-label="Backends on this machine">
    <div class="c2-route-backends-head">
      <span>backends</span>
      <button type="button" data-conversation2-backends-refresh onclick={() => void loadBackends(true)}>
        Look again
      </button>
    </div>
    {#each backends as snapshot (snapshot.backend_key)}
      <BackendCard
        {snapshot}
        updating={updatingBackend === snapshot.backend_key}
        result={updateResults[snapshot.backend_key] ?? null}
        onUpdate={() => void runBackendUpdate(snapshot.backend_key)}
      />
    {/each}
  </aside>
</div>

<style>
  .c2-route {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 20rem);
    gap: var(--space-5);
    align-items: stretch;
    min-height: 0;
    height: 100%;
  }
  .c2-route-pane { display: flex; min-width: 0; min-height: 0; }
  .c2-route-backends {
    display: grid;
    align-content: start;
    gap: var(--space-3);
    min-width: 0;
    overflow-y: auto;
  }
  .c2-route-backends-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
    color: var(--text-faintest);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-label);
    text-transform: uppercase;
  }
  .c2-route-backends-head button {
    background: transparent;
    border: 0;
    color: var(--text-faint);
    cursor: pointer;
    font: inherit;
    letter-spacing: var(--tracking-mono);
    text-transform: none;
  }
  .c2-route-backends-head button:hover { color: var(--text-strong); }
  @media (max-width: 60rem) {
    .c2-route { grid-template-columns: minmax(0, 1fr); }
  }
</style>

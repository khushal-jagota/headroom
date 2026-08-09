<script lang="ts">
  /** The conversation system on a page of its own, for looking at it while it is built.
   *
   * The conversation's id lives in the address, which is what makes reloading and opening
   * a second tab the ordinary path rather than a special one: both of them say which row
   * they hold and take everything after it. Nothing here is linked from the app's own
   * navigation, and nothing the app does touches this route.
   *
   * What is left here is what only this page does: minting an id, keeping it in the
   * address, choosing what an unstarted conversation would be started as, and the backend
   * catalogue used by those choices. Backend management has its own production page;
   * being live is LiveConversation's job, and it is the same one a Ticket uses.
   */
  import { onMount } from "svelte";
  import LiveConversation from "../components/conversation/LiveConversation.svelte";
  import NewConversationForm from "../components/conversation/NewConversationForm.svelte";
  import {
    killConversation,
    readBackends,
    sendPrompt,
    startConversation,
    ConversationWireError,
    type BackendSnapshot,
    type ConversationBackendKey,
    type DeliveredMessage,
    type OwnerSendBody
  } from "../lib/conversation/wire";

  const SENDER_LABEL = "owner";
  const DEFAULT_WORKSPACE_FOLDER = "~/Coding";

  let conversationId = $state(readIdFromAddress() ?? mintConversationId());
  /** What is on screen. It follows the id above; New is what parts them, until the next
   *  first message starts one under the new name. */
  let liveConversationId = $state<string | null>(null);
  let runningBackendKey = $state<ConversationBackendKey | null>(null);
  let backends = $state<BackendSnapshot[]>([]);
  let errorNote = $state<string | null>(null);

  // What a conversation that has not been started yet would be started as. This page is
  // its own owner: the form is the whole of the answer, so it is what the pane is told.
  let newBackendKey = $state<ConversationBackendKey>("codex");
  let newModel = $state<string | null>(null);
  let newReasoningEffort = $state<string | null>(null);
  let newWorkspaceFolder = $state(DEFAULT_WORKSPACE_FOLDER);

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

  function sentenceFor(error: unknown): string {
    if (error instanceof ConversationWireError) return error.message;
    return error instanceof Error ? error.message : String(error);
  }

  /** What this machine says a backend runs when nobody names a model. Null is a backend
   *  that reported no catalogue, and then there is nothing here that could name one. */
  function modelTheBackendRuns(key: ConversationBackendKey): string | null {
    return backends.find((snapshot) => snapshot.backend_key === key)?.default_model_id ?? null;
  }

  /** The first message starts it, as whatever the form and the composer were left showing.
   *
   * This screen is the one place a conversation is made on purpose rather than by talking,
   * because making one on named values is what it is for. It still makes it with the
   * message: what the composer says the message runs under is what the conversation is
   * created on, so the message that creates it never has to change it.
   */
  async function sendToTheConversation(body: OwnerSendBody): Promise<DeliveredMessage> {
    const { conversation_id: sendingInto, backend_key, model, reasoning_effort, ...message } =
      body;
    // Named means a conversation that answered for itself. The address holds a name from
    // the moment this page is opened, and a name is not a conversation.
    if (sendingInto === null) {
      const startingOn = (backend_key as ConversationBackendKey | undefined) ?? newBackendKey;
      await startConversation({
        conversation_id: conversationId,
        backend_key: startingOn,
        // A start names its model. Where nobody picked one, the model is the one this
        // machine says that backend runs — reading the catalogue and sending the id is
        // naming a model, while leaving the field out is letting the tool pick one nobody
        // can see. A backend that reported no catalogue leaves nothing to name: the start
        // goes out saying so, the server answers 422, and the composer already treats that
        // as this text having got nowhere and hands the words back.
        model: model ?? modelTheBackendRuns(startingOn),
        reasoning_effort: reasoning_effort ?? newReasoningEffort,
        // Left out when untouched, so the server's own default folder applies.
        ...(newWorkspaceFolder === DEFAULT_WORKSPACE_FOLDER
          ? {}
          : { workspace_folder: newWorkspaceFolder })
      });
      writeIdToAddress(conversationId);
      liveConversationId = conversationId;
      const started = await sendPrompt(conversationId, message);
      return { ...started, conversation_id: conversationId };
    }
    const fate = await sendPrompt(sendingInto, {
      ...message,
      ...(model === undefined ? {} : { model_change: model }),
      ...(reasoning_effort === undefined ? {} : { reasoning_effort_change: reasoning_effort })
    });
    return { ...fate, conversation_id: sendingInto };
  }

  /** New kills the old one first: freeing the agent would let its held messages run,
   *  and starting again must not be the thing that finally delivers them. */
  async function newConversation(): Promise<void> {
    if (liveConversationId !== null) await killConversation(liveConversationId);
    conversationId = mintConversationId();
    liveConversationId = null;
    writeIdToAddress(conversationId);
  }

  async function loadBackends(): Promise<void> {
    try {
      backends = await readBackends();
    } catch (error) {
      errorNote = sentenceFor(error);
    }
  }

  onMount(() => {
    if (readIdFromAddress() === null) writeIdToAddress(conversationId, { replace: true });
    // An id in the address is one this page has been at before, so it is opened straight
    // away: if it names nothing yet, that is the empty state rather than a failure.
    liveConversationId = conversationId;
    void loadBackends();
  });
</script>

<div class="c2-route" data-conversation-route>
  {#if errorNote}
    <div class="c2-route-error" role="alert">{errorNote}</div>
  {/if}
  <div class="c2-route-pane">
    <LiveConversation
      conversationId={liveConversationId}
      label={`${runningBackendKey ?? newBackendKey} · ${liveConversationId ?? conversationId}`}
      {backends}
      senderLabel={SENDER_LABEL}
      startValues={{
        backend_key: newBackendKey,
        model: newModel ?? modelTheBackendRuns(newBackendKey),
        reasoning_effort: newReasoningEffort
      }}
      bind:runningBackendKey
      sendMessage={sendToTheConversation}
      onNewConversation={newConversation}
    >
      {#snippet emptyState()}
        <NewConversationForm
          {conversationId}
          {backends}
          bind:backendKey={newBackendKey}
          bind:model={newModel}
          bind:reasoningEffort={newReasoningEffort}
          bind:workspaceFolder={newWorkspaceFolder}
        />
      {/snippet}
    </LiveConversation>
  </div>

</div>

<style>
  .c2-route {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    align-items: stretch;
    min-height: 0;
    /* The pane's thread is the thing that scrolls, so this route has to be as tall as the
       space it was given rather than as tall as what is in it. Nothing above it sets a
       height — the shell is a min-height and the screen is just a box — so a percentage
       resolves to nothing and the page ends up scrolling instead of the thread. The
       height is the viewport, less the shell's nav bar and the padding around a screen. */
    height: calc(100vh - var(--shell-nav-height) - var(--space-5) * 2);
  }
  .c2-route-pane { display: flex; flex: 1; min-width: 0; min-height: 0; }
  .c2-route-error {
    color: var(--accent-error);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
  }
  @media (max-width: 720px) {
    /* Both fixed navigation bars, both content gutters, and the home indicator own space. */
    .c2-route {
      --conversation-safe-area-bottom: env(safe-area-inset-bottom);
      height: calc(
        100dvh
        - 2 * var(--shell-nav-height)
        - 2 * var(--page-gutter)
        - var(--conversation-safe-area-bottom)
      );
    }
  }
</style>

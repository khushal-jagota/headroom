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
   * cards. Being live is LiveConversation's job, and it is the same one a Ticket uses.
   */
  import { onMount } from "svelte";
  import BackendCard from "../components/conversation/BackendCard.svelte";
  import LiveConversation from "../components/conversation/LiveConversation.svelte";
  import NewConversationForm from "../components/conversation/NewConversationForm.svelte";
  import {
    killConversation,
    readBackends,
    startConversation,
    updateBackend,
    ConversationWireError,
    type BackendSnapshot,
    type BackendUpdateResult,
    type ConversationBackendKey
  } from "../lib/conversation/wire";

  const SENDER_LABEL = "owner";
  const DEFAULT_WORKSPACE_FOLDER = "~/Coding";

  let conversationId = $state(readIdFromAddress() ?? mintConversationId());
  /** What is on screen. It follows the id above; New is what parts them, until the next
   *  first message starts one under the new name. */
  let liveConversationId = $state<string | null>(null);
  let runningBackendKey = $state<ConversationBackendKey>("codex");
  let backends = $state<BackendSnapshot[]>([]);
  let updatingBackend = $state<ConversationBackendKey | null>(null);
  let updateResults = $state<Partial<Record<ConversationBackendKey, BackendUpdateResult>>>({});
  let errorNote = $state<string | null>(null);

  // What a conversation that has not been started yet would be started as.
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

  /** The first message starts it, as whatever the form was left showing. */
  async function startTheConversation(): Promise<string | null> {
    await startConversation({
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
    liveConversationId = conversationId;
    return conversationId;
  }

  /** New kills the old one first: freeing the agent would let its held messages run,
   *  and starting again must not be the thing that finally delivers them. */
  async function newConversation(): Promise<void> {
    if (liveConversationId !== null) await killConversation(liveConversationId);
    conversationId = mintConversationId();
    liveConversationId = null;
    writeIdToAddress(conversationId);
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
    // An id in the address is one this page has been at before, so it is opened straight
    // away: if it names nothing yet, that is the empty state rather than a failure.
    liveConversationId = conversationId;
    void loadBackends();
  });
</script>

<div class="c2-route" data-conversation-route>
  <div class="c2-route-pane">
    <LiveConversation
      conversationId={liveConversationId}
      label={`${runningBackendKey} · ${liveConversationId ?? conversationId}`}
      {backends}
      senderLabel={SENDER_LABEL}
      fallbackBackendKey={newBackendKey}
      bind:runningBackendKey
      onStartConversation={startTheConversation}
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

  <aside class="c2-route-backends" aria-label="Backends on this machine">
    <div class="c2-route-backends-head">
      <span>backends</span>
      <button type="button" data-conversation-backends-refresh onclick={() => void loadBackends(true)}>
        Look again
      </button>
    </div>
    {#if errorNote}
      <div class="c2-route-backends-error" role="alert">{errorNote}</div>
    {/if}
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
    /* The pane's thread is the thing that scrolls, so this route has to be as tall as the
       space it was given rather than as tall as what is in it. Nothing above it sets a
       height — the shell is a min-height and the screen is just a box — so a percentage
       resolves to nothing and the page ends up scrolling instead of the thread. The
       height is the viewport, less the shell's nav bar and the padding around a screen. */
    height: calc(100vh - var(--shell-nav-height) - var(--space-5) * 2);
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
  .c2-route-backends-error {
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
  }
  @media (max-width: 60rem) {
    .c2-route { grid-template-columns: minmax(0, 1fr); }
  }
</style>

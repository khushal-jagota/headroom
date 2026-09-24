<script lang="ts">
  /** The pane: who this is, its controls, and the one box you speak into.
   *
   * A page that wants the conversation as a layer says how far open it is, and this is one
   * conversation at three heights rather than three of anything: at rest the head and the
   * thread give up their height and the rest bar says what happened last, and peeking and
   * opening give it back. The same viewport, composer, and draft survive every move.
   */
  import type { Snippet } from "svelte";
  import ConversationComposer from "./ConversationComposer.svelte";
  import ConversationRestBar from "./ConversationRestBar.svelte";
  import TaskProgress from "./TaskProgress.svelte";
  import ConversationViewport from "./viewport/ConversationViewport.svelte";
  import type { RunValues } from "../../lib/conversation/composer";
  import type { ConversationState } from "../../lib/conversation/conversationState";
  import type { OutgoingMessage } from "../../lib/conversation/outgoing";
  import type { HeldPromptRow } from "../../lib/conversation/heldPrompts";
  import {
    keyTogglesConversationLens,
    type ConversationLens
  } from "../../lib/conversation/lens";
  import { restLineFrom } from "../../lib/conversation/restLine";
  import { taskProgressFrom } from "../../lib/conversation/taskProgress";
  import type { TranscriptRow } from "../../lib/conversation/transcript";
  import type {
    ComposerCatalogEntry,
    BackendModel,
    BackendSnapshot,
    ConversationBackendKey,
    PastConversation,
    PermissionAskOption,
    PromptDeliveryMode,
    SentMessagePiece,
    UserInputAnswers,
    UserInputQuestion
  } from "../../lib/conversation/wire";

  let {
    conversationId,
    backendKey = null,
    conversationExists = false,
    workspaceFolder = null,
    rows = [],
    visibleRows = null,
    outgoingMessages = [],
    running = false,
    ask = null,
    userInput = null,
    askNote = null,
    current = { model: null, reasoningEffort: null },
    models = [],
    backends = $bindable([]),
    ownSenderLabel = null,
    livenessPulse = 0,
    effortOptions = [],
    composerCatalog = [],
    startsOnModel = null,
    startsOnReasoningEffort = null,
    heldPromptRows = [],
    supportsSteer = false,
    fateNote = null,
    errorNote = null,
    connectionTrouble = false,
    composerPlaceholder = "Message the agent...",
    composerDisabled = false,
    showRunPicker = true,
    readOnly = false,
    pastConversations = [],
    pastConversationsLabel = "Conversation",
    selectedPastConversationId = $bindable(null),
    ownerReadThroughSequence = 0,
    lens = $bindable("focus"),
    conversationState = $bindable(null),
    emptyState,
    onSend,
    onStop,
    onAnswer,
    onSubmitUserInput,
    onCancelTurn,
    onDiscardHeldPrompt,
    onPromoteHeldPrompt,
    onStopDrawingHeldPrompt,
    onSendHeldPromptAgain,
    onNewConversation
  }: {
    /** Which conversation is on the screen. A message's files are fetched under it, so a
     *  piece can only ever reach a file kept for the conversation it belongs to. */
    conversationId: string;
    backendKey?: ConversationBackendKey | null;
    /** Whether there is a conversation yet, which is what fixes its backend. Holding an id
     *  is not one existing, so only whoever has read the record can say. */
    conversationExists?: boolean;
    workspaceFolder?: string | null;
    rows?: readonly TranscriptRow[];
    /** Rows this lens draws. The complete rows still own turn structure. */
    visibleRows?: readonly TranscriptRow[] | null;
    /** Messages this browser has sent that the record does not have yet, oldest first. */
    outgoingMessages?: readonly OutgoingMessage[];
    running?: boolean;
    ask?: {
      askId: string;
      title: string;
      detail: string | null;
      options: readonly PermissionAskOption[];
    } | null;
    userInput?: {
      requestId: string;
      questions: readonly UserInputQuestion[];
    } | null;
    askNote?: string | null;
    current?: RunValues;
    models?: readonly BackendModel[];
    /** Every backend this machine reported. The composer's rail chooses from them while
     *  there is no conversation to be fixed to one. */
    backends?: readonly BackendSnapshot[];
    /** The label this pane sends under, so your own messages are not labelled as yours. */
    ownSenderLabel?: string | null;
    livenessPulse?: number;
    effortOptions?: readonly string[];
    /** The typed text shortcuts this conversation offers in the composer. */
    composerCatalog?: readonly ComposerCatalogEntry[];
    /** What a conversation started from here would run on, for the composer to show while
     *  there is none. Its owner resolved them; nothing here reads them. */
    startsOnModel?: string | null;
    startsOnReasoningEffort?: string | null;
    heldPromptRows?: readonly HeldPromptRow[];
    supportsSteer?: boolean;
    fateNote?: string | null;
    errorNote?: string | null;
    connectionTrouble?: boolean;
    composerPlaceholder?: string;
    composerDisabled?: boolean;
    showRunPicker?: boolean;
    /** A historical transcript is visible through this single boundary. No mutation
     *  control is rendered inside it. */
    readOnly?: boolean;
    /** The owner's earlier conversations, for the options menu to offer beside its own
     *  actions. Empty for an owner that keeps one, and then no picker is drawn. */
    pastConversations?: readonly PastConversation[];
    /** What the picker is called to a screen reader, in the owner's own words. */
    pastConversationsLabel?: string;
    /** Which earlier conversation the owner is reading, or null for the current one.
     *  The owner opens what this names; nothing here reads it back. */
    selectedPastConversationId?: string | null;
    /** How far through the record this person has read, from the conversation view. */
    ownerReadThroughSequence?: number;
    /** Which projection of this conversation record is visible. */
    lens?: ConversationLens;
    /** How far open the conversation is, or null for a page that is not making a layer of
     *  it. The page sets what it opens in; this writes back when the person moves it. */
    conversationState?: ConversationState | null;
    emptyState?: Snippet;
    onSend: (
      content: SentMessagePiece[],
      mode: PromptDeliveryMode,
      picked: RunValues
    ) => Promise<boolean>;
    onStop?: () => void;
    onAnswer?: (optionId: string) => void;
    onSubmitUserInput?: (answers: UserInputAnswers) => void;
    onCancelTurn?: () => void;
    /** Throw away one message that is still waiting for the agent, by its own id. */
    onDiscardHeldPrompt?: (messageId: string) => void;
    onStopDrawingHeldPrompt?: (senderMessageId: string) => Promise<void> | void;
    onSendHeldPromptAgain?: (senderMessageId: string) => Promise<void> | void;
    onPromoteHeldPrompt?: (
      heldPromptId: string,
      mode: "send_now" | "steer"
    ) => Promise<void> | void;
    onNewConversation?: () => void;
  } = $props();

  let paneElement = $state<HTMLDivElement | null>(null);
  let menuOpen = $state(false);
  let confirmArmed = $state(false);
  let menuElement = $state<HTMLDivElement | null>(null);
  let menuButton = $state<HTMLButtonElement | null>(null);

  let headerException = $derived.by(() => {
    if (readOnly) return { text: "past · read only", accent: false };
    if (ask || userInput) return { text: "waiting for you", accent: true };
    if (running) return { text: "working", accent: false };
    return null;
  });

  // Only at rest is there a bar to put it in. Peeked and opened have the turn head.
  let rowsForLens = $derived(visibleRows ?? rows);
  let taskProgress = $derived(taskProgressFrom(rowsForLens));
  let restLine = $derived(
    conversationState === "rest"
      ? restLineFrom(
          rows,
          ownSenderLabel ?? "",
          { ...taskProgress, turnRunning: running },
          { visibleRows: rowsForLens, lens },
          ownerReadThroughSequence
        )
      : null
  );

  /** The one control through the states, and what it means where it is standing.
   *
   * Drawn, not typed. A character is sized by the font's own idea of how much of its em to
   * fill, which left a 32px control carrying about twelve pixels of ink — the "absolutely
   * tiny" the owner reported. A stroked path fills the box it is given. */
  let stateControl = $derived(
    conversationState === "peeked"
      ? { opensIt: true, label: "Open the conversation full height" }
      : conversationState === "opened"
        ? { opensIt: false, label: "Put the conversation back to a card" }
        : null
  );

  function closeMenu(): void {
    menuOpen = false;
    confirmArmed = false;
  }

  function onWindowPointerDown(event: PointerEvent): void {
    if (menuOpen && menuElement && !menuElement.contains(event.target as Node)) closeMenu();
    theComposersInputWasPressed(event);
  }

  /** Pressing this pane's input or rest line opens a resting conversation a little. */
  function theComposersInputWasPressed(event: PointerEvent): void {
    if (conversationState !== "rest") return;
    const pressed = event.target;
    if (!(pressed instanceof Element) || paneElement === null) return;
    if (!paneElement.contains(pressed)) return;
    if (pressed.closest("[data-conversation-task-control], [data-conversation-task-list]")) return;
    if (
      pressed.closest("[data-conversation-input]") === null
      && pressed.closest("[data-conversation-rest-bar]") === null
    ) {
      return;
    }
    conversationState = "peeked";
  }

  function onWindowKeydown(event: KeyboardEvent): void {
    if (
      keyTogglesConversationLens(event)
      && conversationState !== "rest"
      && paneElement !== null
      && paneElement.offsetParent !== null
    ) {
      event.preventDefault();
      lens = lens === "focus" ? "full" : "focus";
      return;
    }
    if (event.key !== "Escape" || event.defaultPrevented) return;
    if (menuOpen) {
      closeMenu();
      menuButton?.focus();
      return;
    }
    // One press, one state back. A non-layer conversation has nowhere to move.
    if (conversationState === "opened") conversationState = "peeked";
    else if (conversationState === "peeked") conversationState = "rest";
  }

  /** The control was pressed: the card's height, and nothing else.
   *
   * It is one control with two meanings, and both are about how tall the conversation
   * is. Putting it away entirely is what Escape and a press on the page behind it do. */
  function moveThroughTheStates(): void {
    if (conversationState === "peeked") conversationState = "opened";
    else if (conversationState === "opened") conversationState = "peeked";
  }

  function confirmNewConversation(): void {
    onNewConversation?.();
    closeMenu();
  }

  /** The picker's value for the conversation the owner is on now, which has no id here. */
  const CURRENT_CONVERSATION = "__current__";

  function pastConversationName(entry: PastConversation): string {
    const started = new Date(entry.created_at * 1000);
    const when = Number.isNaN(started.valueOf())
      ? "Unknown date"
      : started.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
    return `${when} · ${entry.conversation_id}`;
  }

  function openConversation(event: Event): void {
    const picked = (event.currentTarget as HTMLSelectElement).value;
    selectedPastConversationId = picked === CURRENT_CONVERSATION ? null : picked;
  }
</script>

<svelte:window
  onpointerdown={onWindowPointerDown}
  onkeydown={onWindowKeydown}
/>

<div
  class="chat-panel"
  data-conversation-pane
  data-conversation-read-only={readOnly ? "true" : undefined}
  data-conversation-read-only-boundary={readOnly ? "true" : undefined}
  data-conversation-state={conversationState}
  data-conversation-lens={lens}
  bind:this={paneElement}
>
  <div class="chat-head">
    {#if connectionTrouble}
      <span class="chat-conn-dot" role="img" aria-label="Connection trouble"></span>
    {/if}
    {#if headerException && conversationState !== "opened"}
      <span class={`chat-state ${headerException.accent ? "chat-state--attn" : ""}`}>
        {headerException.text}
      </span>
    {/if}
    <!-- Two named choices rather than one control whose label is the state it is in:
         which lens you are in and which you can move to are both on the screen. -->
    <div
      class="chat-lens"
      role="group"
      aria-label="How much of the conversation to show (F)"
      data-conversation-lens-group
    >
      <button
        type="button"
        class="chat-lens-choice"
        data-conversation-lens-choice="focus"
        aria-pressed={lens === "focus"}
        title="The exchange only (F)"
        onclick={() => (lens = "focus")}
      >Focus</button>
      <button
        type="button"
        class="chat-lens-choice"
        data-conversation-lens-choice="full"
        aria-pressed={lens === "full"}
        title="The exchange and the work (F)"
        onclick={() => (lens = "full")}
      >Full</button>
    </div>
    <div class="chat-head-right">
      <!-- One state control survives as its meaning changes, preserving keyboard focus. -->
      {#if stateControl !== null}
        <button
          type="button"
          class="chat-overflow-btn"
          data-conversation-expand={stateControl.opensIt ? true : undefined}
          data-conversation-collapse={stateControl.opensIt ? undefined : true}
          aria-label={stateControl.label}
          title={stateControl.label}
          onclick={moveThroughTheStates}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            {#if stateControl.opensIt}
              <path d="M9 4H4v5M15 20h5v-5" />
            {:else}
              <path d="M4 9h5V4M20 15h-5v5" />
            {/if}
          </svg>
        </button>
      {/if}
      {#if !readOnly || pastConversations.length > 0}
        <div class="chat-overflow" bind:this={menuElement}>
          <button
            type="button"
            class="chat-overflow-btn"
            bind:this={menuButton}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label="Conversation options"
            onclick={() => (menuOpen ? closeMenu() : (menuOpen = true))}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" class="chat-overflow-dots">
              <circle cx="5" cy="12" r="1.5" />
              <circle cx="12" cy="12" r="1.5" />
              <circle cx="19" cy="12" r="1.5" />
            </svg>
          </button>
          {#if menuOpen}
            <div class="chat-overflow-menu">
              {#if !readOnly}
                <div class="chat-overflow-actions" role="menu">
                  {#if confirmArmed}
                    <button
                      type="button"
                      class="chat-overflow-item chat-overflow-item--confirm"
                      role="menuitem"
                      data-conversation-new-confirm
                      onclick={confirmNewConversation}
                    >Confirm — this kills the old one</button>
                  {:else}
                    <button
                      type="button"
                      class="chat-overflow-item"
                      role="menuitem"
                      data-conversation-new-arm
                      onclick={() => (confirmArmed = true)}
                    >New conversation</button>
                  {/if}
                </div>
              {/if}
              <!-- The earlier conversations sit under the actions rather than in a row
                   above the card: the card is already named by the screen it is on. A
                   read-only pane keeps this section and loses the one above it, so the
                   way back to the current conversation is where it was left. -->
              {#if pastConversations.length > 0}
                <label class="chat-overflow-history" data-conversation-history>
                  <select
                    aria-label={pastConversationsLabel}
                    value={selectedPastConversationId ?? CURRENT_CONVERSATION}
                    onchange={openConversation}
                  >
                    <option value={CURRENT_CONVERSATION}>
                      {selectedPastConversationId === null && !conversationExists
                        ? "Current · new conversation"
                        : "Current conversation"}
                    </option>
                    {#each pastConversations as entry (entry.conversation_id)}
                      <option value={entry.conversation_id}>{pastConversationName(entry)}</option>
                    {/each}
                  </select>
                </label>
              {/if}
              {#if workspaceFolder}
                <div class="chat-overflow-path" data-conversation-workspace>{workspaceFolder}</div>
              {/if}
            </div>
          {/if}
        </div>
      {/if}
    </div>
  </div>

  <ConversationViewport
    {conversationId}
    {rows}
    visibleRows={rowsForLens}
    {lens}
    {outgoingMessages}
    {models}
    {ownSenderLabel}
    {livenessPulse}
    {conversationState}
    {emptyState}
  />

  {#if conversationState === "peeked" || conversationState === "opened"}
    <TaskProgress
      progress={taskProgress}
      variant="strip"
      {running}
      moving={running && ask === null && userInput === null}
      composerGap={conversationState === "opened"}
    />
  {/if}

  <!-- At rest this one line is the whole conversation visible above the composer. -->
  {#if conversationState === "rest"}
    <ConversationRestBar line={restLine} />
  {/if}

  {#if readOnly}
    <div class="conversation-read-only" data-conversation-read-only-notice>
      Past conversation · read only
    </div>
  {:else}
    <ConversationComposer
      conversationId={conversationExists ? conversationId : null}
      {backendKey}
      {conversationExists}
      {running}
      {ask}
      {askNote}
      {current}
      {models}
      bind:backends
      {effortOptions}
      {composerCatalog}
      {startsOnModel}
      {startsOnReasoningEffort}
      {heldPromptRows}
      {supportsSteer}
      {fateNote}
      {errorNote}
      placeholder={composerPlaceholder}
      disabled={composerDisabled}
      {showRunPicker}
      {onSend}
      {onStop}
      {onAnswer}
      {userInput}
      {onSubmitUserInput}
      {onCancelTurn}
      {onDiscardHeldPrompt}
      {onPromoteHeldPrompt}
      {onStopDrawingHeldPrompt}
      {onSendHeldPromptAgain}
    />
  {/if}
</div>

<style>
  /* The component measures itself, not the window. The same pane is narrow in a side
     panel on a wide screen, and a host that names no container of its own — the Chief
     conversation is one — still gets the adaptation. */
  :global([data-conversation-pane]) {
    gap: var(--space-2);
    container-type: inline-size;
    container-name: conversation-pane;
  }
  /* A narrow pane gives its width to the words, not to two sets of edges. This is what
     the app already does for a narrow window, said about the pane instead — so a side
     panel on a wide screen, and a host that names no container, get it too. It must not
     add a gutter back: the widths here are the narrow ones, never the wide ones. */
  @container conversation-pane (max-width: 480px) {
    :global([data-conversation-pane] .chat-u) { max-width: 86%; }
  }
  /* Inside the conversation card the well and rest line are two halves of one card. */
  :global(.conversation-layer [data-conversation-pane]) { gap: 0; }
  /* Rest keeps the same child viewport mounted, but gives its head and thread no display. */
  :global([data-conversation-pane][data-conversation-state="rest"] .chat-head),
  :global([data-conversation-pane][data-conversation-state="rest"] .chat-thread-shell) {
    display: none;
  }
  .chat-lens {
    display: inline-flex;
    gap: var(--space-1);
    margin-inline-start: var(--space-2);
  }
  .chat-lens-choice {
    border: var(--border-hairline) solid transparent;
    border-radius: var(--radius-pill);
    background: transparent;
    color: var(--text-faint);
    /* A target a finger can find, at the size the rest of the head uses. */
    min-height: var(--conversation-target);
    padding: 6px var(--space-3);
    font: inherit;
    font-size: var(--type-sm);
    cursor: pointer;
    transition:
      color var(--motion-fast, 90ms) ease,
      background var(--motion-fast, 90ms) ease;
  }
  .chat-lens-choice:hover { color: var(--text-strong); }
  .chat-lens-choice[aria-pressed="true"] {
    background: var(--surface-2);
    color: var(--text-strong);
  }
  .conversation-read-only {
    flex: none;
    margin: 0 calc(var(--space-4) * -1);
    border-top: var(--border-hairline) solid var(--border-color);
    padding: var(--space-3) var(--space-4);
    color: var(--text-faint);
    font-family: var(--font-ui);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-label);
    text-align: center;
    text-transform: uppercase;
  }
</style>

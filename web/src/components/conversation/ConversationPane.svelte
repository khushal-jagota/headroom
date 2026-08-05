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
  import ConversationViewport from "./viewport/ConversationViewport.svelte";
  import type { RunValues } from "../../lib/conversation/composer";
  import type { ConversationState } from "../../lib/conversation/conversationState";
  import type { OutgoingMessage } from "../../lib/conversation/outgoing";
  import { restLineFrom } from "../../lib/conversation/restLine";
  import type { TranscriptRow } from "../../lib/conversation/transcript";
  import type {
    AgentCommand,
    BackendModel,
    BackendSnapshot,
    ConversationBackendKey,
    PermissionAskOption,
    PromptDeliveryMode,
    SentMessagePiece,
    UserInputAnswers,
    UserInputQuestion
  } from "../../lib/conversation/wire";

  let {
    conversationId,
    label,
    backendKey = null,
    conversationExists = false,
    workspaceFolder = null,
    rows = [],
    outgoingMessages = [],
    running = false,
    ask = null,
    userInput = null,
    askNote = null,
    current = { model: null, reasoningEffort: null },
    models = [],
    backends = [],
    ownSenderLabel = null,
    livenessPulse = 0,
    effortOptions = [],
    availableCommands = [],
    startsOnModel = null,
    startsOnReasoningEffort = null,
    heldPromptCount = 0,
    fateNote = null,
    errorNote = null,
    connectionTrouble = false,
    composerPlaceholder = "Message the agent...",
    composerDisabled = false,
    showRunPicker = true,
    conversationState = $bindable(null),
    emptyState,
    onSend,
    onStop,
    onAnswer,
    onSubmitUserInput,
    onCancelTurn,
    onDiscardHeldPrompt,
    onNewConversation,
    ticketId = null
  }: {
    /** Which conversation is on the screen. A message's files are fetched under it, so a
     *  piece can only ever reach a file kept for the conversation it belongs to. */
    conversationId: string;
    ticketId?: string | null;
    label: string;
    backendKey?: ConversationBackendKey | null;
    /** Whether there is a conversation yet, which is what fixes its backend. Holding an id
     *  is not one existing, so only whoever has read the record can say. */
    conversationExists?: boolean;
    workspaceFolder?: string | null;
    rows?: readonly TranscriptRow[];
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
    /** The commands this conversation's agent reports, which the composer offers under a
     *  line being written as one. */
    availableCommands?: readonly AgentCommand[];
    /** What a conversation started from here would run on, for the composer to show while
     *  there is none. Its owner resolved them; nothing here reads them. */
    startsOnModel?: string | null;
    startsOnReasoningEffort?: string | null;
    heldPromptCount?: number;
    fateNote?: string | null;
    errorNote?: string | null;
    connectionTrouble?: boolean;
    composerPlaceholder?: string;
    composerDisabled?: boolean;
    showRunPicker?: boolean;
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
    onNewConversation?: () => void;
  } = $props();

  let paneElement = $state<HTMLDivElement | null>(null);
  let menuOpen = $state(false);
  let confirmArmed = $state(false);
  let menuElement = $state<HTMLDivElement | null>(null);
  let menuButton = $state<HTMLButtonElement | null>(null);

  let headerException = $derived.by(() => {
    if (ask || userInput) return { text: "waiting for you", accent: true };
    if (running) return { text: "working", accent: false };
    return null;
  });

  // Only at rest is there a bar to put it in. Peeked and opened have the turn head.
  let restLine = $derived(
    conversationState === "rest" ? restLineFrom(rows, ownSenderLabel ?? "") : null
  );

  /** The one control through the states, and what it means where it is standing. */
  let stateControl = $derived(
    conversationState === "peeked"
      ? { opensIt: true, glyph: "⤢", label: "Open the conversation full height" }
      : conversationState === "opened"
        ? { opensIt: false, glyph: "⤡", label: "Put the conversation back to a card" }
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
    if (
      pressed.closest("[data-conversation-input]") === null
      && pressed.closest("[data-conversation-rest-bar]") === null
    ) {
      return;
    }
    conversationState = "peeked";
  }

  function onWindowKeydown(event: KeyboardEvent): void {
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

  /** The control was pressed: forward from peeked, back from opened. */
  function moveThroughTheStates(): void {
    if (conversationState === "peeked") conversationState = "opened";
    else if (conversationState === "opened") conversationState = "rest";
  }

  function confirmNewConversation(): void {
    onNewConversation?.();
    closeMenu();
  }
</script>

<svelte:window
  onpointerdown={onWindowPointerDown}
  onkeydown={onWindowKeydown}
/>

<div
  class="chat-panel"
  data-conversation-pane
  data-conversation-state={conversationState}
  bind:this={paneElement}
>
  <div class="chat-head">
    {#if connectionTrouble}
      <span class="chat-conn-dot" role="img" aria-label="Connection trouble"></span>
    {/if}
    <span class="chat-lbl">{label}</span>
    {#if headerException}
      <span class={`chat-state ${headerException.accent ? "chat-state--attn" : ""}`}>
        {headerException.text}
      </span>
    {/if}
    <div class="chat-head-right">
      {#if workspaceFolder}
        <span class="chat-usage" data-conversation-workspace>{workspaceFolder}</span>
      {/if}
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
        >{stateControl.glyph}</button>
      {/if}
      <div class="chat-overflow" bind:this={menuElement}>
        <button
          type="button"
          class="chat-overflow-btn"
          bind:this={menuButton}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label="Conversation options"
          onclick={() => (menuOpen ? closeMenu() : (menuOpen = true))}
        >⋯</button>
        {#if menuOpen}
          <div class="chat-overflow-menu" role="menu">
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
      </div>
    </div>
  </div>

  <ConversationViewport
    {conversationId}
    {ticketId}
    {rows}
    {outgoingMessages}
    {models}
    {ownSenderLabel}
    {livenessPulse}
    {conversationState}
    {emptyState}
    {onDiscardHeldPrompt}
  />

  <!-- At rest this one line is the whole conversation visible above the composer. -->
  {#if conversationState === "rest"}
    <ConversationRestBar line={restLine} />
  {/if}

  <ConversationComposer
    conversationId={conversationExists ? conversationId : null}
    {backendKey}
    {conversationExists}
    {running}
    {ask}
    {askNote}
    {current}
    {models}
    {backends}
    {effortOptions}
    {availableCommands}
    {startsOnModel}
    {startsOnReasoningEffort}
    {heldPromptCount}
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
  />
</div>

<style>
  :global([data-conversation-pane]) { gap: var(--space-2); }
  /* Inside the ticket's card the well and rest line are two halves of one card. */
  :global(.ticket-conversation-layer [data-conversation-pane]) { gap: 0; }
  /* Rest keeps the same child viewport mounted, but gives its head and thread no display. */
  :global([data-conversation-pane][data-conversation-state="rest"] .chat-head),
  :global([data-conversation-pane][data-conversation-state="rest"] .chat-thread-shell) {
    display: none;
  }
</style>

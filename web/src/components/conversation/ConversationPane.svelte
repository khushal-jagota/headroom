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
  import { restLineFrom } from "../../lib/conversation/restLine";
  import { taskProgressFrom } from "../../lib/conversation/taskProgress";
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
    heldPromptRows = [],
    fateNote = null,
    errorNote = null,
    connectionTrouble = false,
    composerPlaceholder = "Message the agent...",
    composerDisabled = false,
    showRunPicker = true,
    readOnly = false,
    conversationState = $bindable(null),
    emptyState,
    onSend,
    onStop,
    onAnswer,
    onSubmitUserInput,
    onCancelTurn,
    onDiscardHeldPrompt,
    onPromoteHeldPrompt,
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
    heldPromptRows?: readonly HeldPromptRow[];
    fateNote?: string | null;
    errorNote?: string | null;
    connectionTrouble?: boolean;
    composerPlaceholder?: string;
    composerDisabled?: boolean;
    showRunPicker?: boolean;
    /** A historical transcript is visible through this single boundary. No mutation
     *  control is rendered inside it. */
    readOnly?: boolean;
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

  // A layered card still identifies the employee. Full height uses the plain worker type
  // that its route supplies, while a non-layer conversation uses its label unchanged.
  let headerLabel = $derived(
    conversationState === "rest" || conversationState === "peeked"
      ? /worker$/i.test(label)
        ? label
        : `${label} worker`
      : label
  );

  // Only at rest is there a bar to put it in. Peeked and opened have the turn head.
  let taskProgress = $derived(taskProgressFrom(rows));
  let restLine = $derived(
    conversationState === "rest"
      ? restLineFrom(rows, ownSenderLabel ?? "", { ...taskProgress, turnRunning: running })
      : null
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
  data-conversation-read-only={readOnly ? "true" : undefined}
  data-conversation-read-only-boundary={readOnly ? "true" : undefined}
  data-conversation-state={conversationState}
  bind:this={paneElement}
>
  <div class="chat-head">
    {#if connectionTrouble}
      <span class="chat-conn-dot" role="img" aria-label="Connection trouble"></span>
    {/if}
    <span class="chat-lbl">{headerLabel}</span>
    {#if headerException && conversationState !== "opened"}
      <span class={`chat-state ${headerException.accent ? "chat-state--attn" : ""}`}>
        {headerException.text}
      </span>
    {/if}
    <div class="chat-head-right">
      {#if workspaceFolder && conversationState !== "opened"}
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
      {#if !readOnly}
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
            <div class="chat-overflow-menu">
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
              {#if conversationState === "opened" && workspaceFolder}
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
    {ticketId}
    {rows}
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
      {backends}
      {effortOptions}
      {availableCommands}
      {startsOnModel}
      {startsOnReasoningEffort}
      {heldPromptRows}
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
    />
  {/if}
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

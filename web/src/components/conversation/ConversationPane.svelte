<script lang="ts">
  import type { UploadDestination, ComposerSendContext } from "../../lib/conversation/videoUploads/contracts";
  import ConversationHistoryPicker from "./ConversationHistoryPicker.svelte";
  import type { HistoryOwnerKind } from "../../lib/catalogs/history";
  /** One composer and viewport survive movement between the reserved dock and full conversation. */
  import type {ConversationViewReader,ViewReaderAnchor} from '../../lib/conversation/view/reader.svelte';
  import {progressFor,restFor} from '../../lib/conversation/view/presentation';
  import { tick, setContext, type Snippet } from "svelte";
  import { CREDENTIAL_REQUEST_READ_ONLY, CREDENTIAL_REQUEST_DRAFTS, type CredentialRequestDrafts } from '../../lib/conversation/credentialRequest.svelte';
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
  import { dockPreviewText, latestUnreadReply } from "../../lib/conversation/dockPresentation";
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
    roleLabel = "Worker",
    backendKey = null,
    conversationExists = false,
    workspaceFolder = null,
    rows = [],
    visibleRows = null,
    outgoingMessages = [],
    withdrawnDisplayIds = new Set<string>(),
    running = false,
    ask = null,
    userInput = null,
    askNote = null,
    userInputNote = null,
    permissionAvailable = true,
    userInputAvailable = true,
    activityUncertain = false,
    current = { model: null, reasoningEffort: null },
    readError = null,
    onRetryModelChoices,
    onPreviewRunSelection,
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
    historyOwner,
    pastConversations = [],
    pastConversationsLabel = "Conversation",
    selectedPastConversationId = $bindable(null),
    ownerReadThroughSequence = 0,
    lens = $bindable("focus"),
    conversationState = $bindable(null),
    emptyState,
    reader = null,
    acknowledgedMessageIds = new Set<string>(),
    viewAnchor = $bindable(null),
    transcriptVisible = true,
    draftKey,
    uploadDestination = null,
    reconcileLocalSend,
    transcriptLoading = false,
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
    reader?: ConversationViewReader | null;
    acknowledgedMessageIds?: ReadonlySet<string>;
    viewAnchor?: ViewReaderAnchor | null;
    transcriptVisible?: boolean;
    draftKey: string;
    uploadDestination?: UploadDestination | null;
    reconcileLocalSend?: (messageId: string) => Promise<boolean>;
    transcriptLoading?: boolean;
    conversationId: string;
    roleLabel?: "Worker" | "Manager" | "Chief" | "Goal";
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
    withdrawnDisplayIds?: ReadonlySet<string>;
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
    userInputNote?: string | null;
    permissionAvailable?: boolean;
    userInputAvailable?: boolean;
    activityUncertain?: boolean;
    current?: RunValues;
    readError?: string | null;
    onRetryModelChoices?: () => Promise<void>;
    onPreviewRunSelection?: (selection: import("../../lib/conversation/runControls/contracts").ComposerRunSelection, backend?: ConversationBackendKey) => void;
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
    historyOwner?: {kind:HistoryOwnerKind;id:string};
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
      picked: RunValues,
      context?: ComposerSendContext
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

  setContext(CREDENTIAL_REQUEST_READ_ONLY, () => readOnly);
  const credentialDrafts = $derived.by(() => { conversationId; return new Map() as CredentialRequestDrafts; });
  setContext(CREDENTIAL_REQUEST_DRAFTS, () => credentialDrafts);
  let placementReady = $state(false);
  let entryPresentationPending = $state(true);
  let paneElement = $state<HTMLDivElement | null>(null);
  let menuOpen = $state(false);
  let confirmArmed = $state(false);
  let menuElement = $state<HTMLDivElement | null>(null);
  let menuButton = $state<HTMLButtonElement | null>(null);

  let headerException = $derived.by(() => {
    if (readOnly) return { text: "past · read only", accent: false };
    if (activityUncertain) return {text:"activity unknown on Mac",accent:false};
    if (ask || userInput) return { text: "waiting for you", accent: true };
    if (running) return { text: "working", accent: false };
    return null;
  });

  // The dock has an activity header. Full conversation has the turn head.
  let rowsForLens = $derived(visibleRows ?? rows);
  let planEntries = $state<import('../../lib/conversation/wire').PlanEntry[]>([]);
  let planController:AbortController|null=null;
  let planVersion='';
  let planLoading=false;
  async function demandPlan():Promise<void>{
    const currentReader=reader;
    const reference=currentReader?.status?.plan;
    if(!currentReader||!reference||planLoading||planVersion===reference.version)return;
    planController?.abort();const controller=new AbortController();planController=controller;planLoading=true;
    try{const value=JSON.parse(await currentReader.body(reference,controller.signal)) as {entries:import('../../lib/conversation/wire').PlanEntry[]};if(!controller.signal.aborted&&reader===currentReader){planEntries=value.entries;planVersion=reference.version;}}
    catch{}finally{if(planController===controller)planLoading=false;}
  }
  $effect(()=>{const version=reader?.status?.plan?.version??'';if(version!==planVersion){planEntries=[];planVersion='';}return()=>planController?.abort();});
  let taskProgress = $derived(reader?.status ? {...progressFor(reader.status),entries:planEntries,onDemand:()=>void demandPlan(),onRelease:()=>{planController?.abort();planEntries=[];planVersion='';}} : taskProgressFrom(rowsForLens));
  let restLine = $derived(
    conversationState === "rest"
      ? reader?.status ? restFor(reader.status,ownSenderLabel ?? "",ownerReadThroughSequence,taskProgress) : restLineFrom(
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
  let stateControl = $derived(conversationState === null ? null : {
    opensIt: conversationState === "rest",
    label: conversationState === "rest" ? "Open the conversation full height" : "Return to the conversation dock"
  });
  let unreadPreview = $derived(dockPreviewText(reader?.status
    ? reader.status.rest.latest_owner_message_sequence > ownerReadThroughSequence
      ? reader.status.rest.latest_owner_message_preview : null
    : latestUnreadReply(rows, ownerReadThroughSequence)));
  let dockFeedback = $derived.by(() => {
    if (userInput) return "Waiting for you";
    if (restLine?.waiting || restLine?.marks.failed) return restLine.text;
    const latest = reader?.status?.rest.latest_happened ?? rows.at(-1);
    if (latest?.kind === "turn_ended" && latest.ending === "completed") return null;
    const kind = latest?.kind;
    return kind && ["prompt_refused", "prompt_uncertain", "prompt_discarded", "explicit_reply_missing", "turn_stopped", "turn_ended"].includes(kind)
      ? restLine?.text ?? null : null;
  });
  let activityKnown = $derived(!activityUncertain && (reader ? typeof reader.status?.turn_running === "boolean" : true));

  function closeMenu(): void {
    menuOpen = false;
    confirmArmed = false;
  }

  function onWindowPointerDown(event: PointerEvent): void {
    if (menuOpen && menuElement && !menuElement.contains(event.target as Node)) closeMenu();

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
    // Escape returns full conversation to the dock. A full-container pane stays open.
    if (conversationState === "opened") { event.preventDefault(); conversationState = "rest"; }
  }

  /** Preview and Return share one action and restore focus to the corresponding control. */
  function moveThroughTheStates(): void {
    const restoreControlFocus = document.activeElement instanceof HTMLElement
      && paneElement?.contains(document.activeElement)
      && document.activeElement.hasAttribute("data-conversation-state-control");
    if (conversationState === "rest") conversationState = "opened";
    else if (conversationState === "opened") conversationState = "rest";
    if (restoreControlFocus) void tick().then(() => paneElement?.querySelector<HTMLButtonElement>("[data-conversation-state-control]")?.focus({ preventScroll: true }));
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
  data-conversation-id={conversationId}
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
      <!-- The corresponding state control receives focus after preview activation. -->
      {#if stateControl !== null && conversationState !== "rest"}
        <button
          type="button"
          class="chat-overflow-btn"
          data-conversation-state-control
          data-conversation-expand={stateControl.opensIt ? true : undefined}
          data-conversation-collapse={stateControl.opensIt ? undefined : true}
          aria-label={stateControl.label}
          title={stateControl.label}
          onclick={moveThroughTheStates}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            {#if stateControl.opensIt}
              <path d="m6 14 6-6 6 6" />
            {:else}
              <path d="m6 10 6 6 6-6" />
            {/if}
          </svg>
        </button>
      {/if}
      {#if !readOnly || historyOwner || pastConversations.length > 0}
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
              {#if historyOwner}
                <ConversationHistoryPicker owner={historyOwner} bind:selected={selectedPastConversationId} label={pastConversationsLabel} {conversationExists} />
              {:else if pastConversations.length > 0}
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

  <div class="conversation-reader" hidden={conversationState === "rest" || !transcriptVisible}>
  <!-- The conversation is never taken off the screen to say that it is loading. A
       region that removes its children while it waits removes the transcript the
       reader is in, and with it everything the viewport knows about where they were. -->
  {#if reader?.entryPhase === 'error'}
    <div class="chat-receipt" role="status" data-conversation-entry-status="error">The conversation did not load.
      <button type="button" class="chat-qrow-act" onclick={() => { void reader?.connect().catch(() => undefined); }}>Retry conversation</button>
    </div>
  {/if}
  <div class="conversation-transcript-region" data-conversation-entry={reader?.entryPhase ?? 'idle'}
    style:visibility={!placementReady || reader && ['loading', 'placing', 'error'].includes(reader.entryPhase) ? 'hidden' : undefined}
    aria-busy={reader?.entryPhase !== 'error' && (!placementReady || entryPresentationPending || transcriptLoading || reader?.entryPhase === 'loading' || reader?.entryPhase === 'placing')}>
  <ConversationViewport
    bind:placementReady
    bind:entryPresentationPending
    {ownerReadThroughSequence}
    bind:viewAnchor
    {reader}
    {acknowledgedMessageIds}
    onStopDrawingMessage={onStopDrawingHeldPrompt}
    onSendAgain={onSendHeldPromptAgain}
    {conversationId}
    {rows}
    visibleRows={rowsForLens}
    {lens}
    {outgoingMessages}
    {withdrawnDisplayIds}
    {models}
    {ownSenderLabel}
    {livenessPulse}
    {activityUncertain}
    {conversationState}
    {emptyState}
  />
  </div>

  </div>

  {#if conversationState === "opened"}
    <TaskProgress
      progress={taskProgress}
      variant="strip"
      {running}
      moving={!activityUncertain && running && ask === null && userInput === null}
      composerGap={conversationState === "opened"}
    />
  {/if}

  <!-- Activity and the latest unread preview belong to the dock surface above the input. -->
  {#if conversationState === "rest"}
    <ConversationRestBar preview={unreadPreview} {roleLabel} {activityKnown}
      {connectionTrouble} running={running && !activityUncertain} feedback={activityUncertain ? "Current activity on Mac is unknown." : dockFeedback} onOpen={moveThroughTheStates} />
  {/if}

  {#if readOnly}
    <div class="conversation-read-only" data-conversation-read-only-notice>
      Past conversation · read only
    </div>
  {:else}
    <ConversationComposer
      {draftKey}
      {uploadDestination}
      {reconcileLocalSend}
      conversationId={conversationExists ? conversationId : null}
      {backendKey}
      {conversationExists}
      {running}
      {ask}
      {askNote}
    {userInputNote}
    {permissionAvailable}
    {userInputAvailable}
      {current}
      {readError}
      {onRetryModelChoices}
      {onPreviewRunSelection}
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
  .conversation-reader { display:flex; flex-direction:column; flex:1; min-height:0; }
  .conversation-reader[hidden] { display:none; }
  .conversation-transcript-region { display:flex; flex:1; min-height:0; min-width:0; }
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
  /* The activity preview owns its gap above the separate rounded input. */
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

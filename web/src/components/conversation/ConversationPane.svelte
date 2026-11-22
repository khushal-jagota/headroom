<script lang="ts">
  import type { UploadDestination, ComposerSendContext } from "../../lib/conversation/videoUploads/contracts";
  import ConversationHistoryPicker from "./ConversationHistoryPicker.svelte";
  import type { HistoryOwnerKind } from "../../lib/catalogs/history";
  /** One composer and viewport survive movement between the reserved dock and full conversation. */
  import type {ConversationViewReader,ViewReaderAnchor} from '../../lib/conversation/view/reader.svelte';
  import {happened,progressFor,restFor} from '../../lib/conversation/view/presentation';
  import { tick, setContext, type Snippet } from "svelte";
  import { COMPACT_LAYOUT_MEDIA_QUERY } from "../../lib/compactLayout";
  import { CREDENTIAL_REQUEST_READ_ONLY, CREDENTIAL_REQUEST_DRAFTS, type CredentialRequestDrafts } from '../../lib/conversation/credentialRequest.svelte';
  import ConversationComposer from "./ConversationComposer.svelte";
  import ConversationRestBar from "./ConversationRestBar.svelte";
  import HeldPromptStack from "./composer/HeldPromptStack.svelte";
  import ConversationViewport from "./viewport/ConversationViewport.svelte";
  import { askShape, DOCKED_CREDENTIAL_REQUEST, pendingCredentialRequest, type RunValues } from "../../lib/conversation/composer";
  import type { ConversationState } from "../../lib/conversation/conversationState";
  import type { OutgoingMessage } from "../../lib/conversation/outgoing";
  import type { HeldPromptRow } from "../../lib/conversation/heldPrompts";
  import {
    conversationThreadItemsForLens,
    keyTogglesConversationLens,
    type ConversationLens
  } from "../../lib/conversation/lens";
  import { awaitsCredential, conversationState as readConversationState, copyText, paneHeadStatus, dockFeedbackFor, dockPreviewText, interruptedFrom, latestUnreadReply, paneFailureFrom, paneTitleFor, uncertainActivityFeedback } from "../../lib/conversation/dockPresentation";
  import type { ConversationJump } from "./viewport/viewportConfiguration";
  import { elapsedTurnTime } from "../../lib/conversation/elapsedTime.svelte";
  import { formatDuration } from "../../lib/conversation/transcript";
  import { fittedTitle } from "../../lib/conversation/titleFit";
  import { workerSetupSentence, workerSetupStartedAtUnixMilliseconds } from "../../lib/workerSetup";
  import { restLineFrom, restLineMarksFrom, whatThisRowSays } from "../../lib/conversation/restLine";
  import { taskProgressFrom } from "../../lib/conversation/taskProgress";
  import type { TranscriptRow } from "../../lib/conversation/transcript";
  import { hasBackgroundTaskAfterTheTurn } from "../../lib/conversation/transcript";
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
    title = null,
    subtitle = null,
    workerSetup = null,
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
    waitingOnPage = null,
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
    /** Who this conversation is with, in the page's own words ("Vylo pricing and launch",
     *  "Coding worker"), for the opened pane's top bar. Without it the role names it. */
    title?: string | null;
    /** Who does the work, in a quieter voice after the title (a ticket's "Coding worker"). */
    subtitle?: string | null;
    /** The owning Ticket's Worker being prepared; the status line says so until a turn runs. */
    workerSetup?: import("../../lib/navigation/contracts").WorkerSetup | null;
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
    /** A decision for the person that the page shows above the dock (a proposal awaiting
     *  their approval). The dock's line then says the conversation waits on them. */
    waitingOnPage?: "approval" | null;
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
    onDiscardHeldPrompt?: (messageId: string) => Promise<void> | void;
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
  /** The key request asked in the composer's place: the newest pending one, never in a
   *  read-only pane. The transcript leaves exactly this one out (DOCKED_CREDENTIAL_REQUEST);
   *  settled requests, and any older one still pending, stay inline. */
  let credentialRequest = $derived(readOnly ? null : pendingCredentialRequest(rows, reader?.pages.flatMap(page => page.items) ?? []));
  setContext(DOCKED_CREDENTIAL_REQUEST, () => credentialRequest?.request_id ?? null);
  let placementReady = $state(false);
  let entryPresentationPending = $state(true);
  let paneElement = $state<HTMLDivElement | null>(null);
  let menuOpen = $state(false);
  let confirmArmed = $state(false);
  let menuElement = $state<HTMLDivElement | null>(null);
  let menuButton = $state<HTMLButtonElement | null>(null);


  // Work the agent started that outlived its turn counts as working, as the sidebar does.
  // Stop and the composer still follow the turn alone.
  let backgroundWorking = $derived(!activityUncertain && hasBackgroundTaskAfterTheTurn(reader?.status));
  // The dock has an activity header. Full conversation has the turn head.
  let rowsForLens = $derived(visibleRows ?? rows);
  // The plan is not drawn in the conversation (owner): the dock's one line reads it only to
  // say what the turn is on.
  let taskProgress = $derived(reader?.status ? progressFor(reader.status) : taskProgressFrom(rowsForLens));
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

  let turnStartedAt = $derived(reader?.status
    ? reader.status.rest.latest_turn?.started_at_unix_milliseconds ?? null
    : conversationThreadItemsForLens(rows, rowsForLens, lens).filter(item => item.kind === "turn").at(-1)?.startedAtUnixMilliseconds ?? null);

  // The opened pane's top bar says who the conversation is with and what it is doing. At rest
  // the bar is not drawn: the dock's status line says the same there.
  let headTitle = $derived(paneTitleFor(title, roleLabel));
  /** v5: the header carries the title alone, in every state. The composer's placeholder
   *  ("Message <role>") already names who does the work. */
  let headSettingUp = $derived(!running && !backgroundWorking && workerSetup !== null);
  const headElapsed = elapsedTurnTime(() => running && !activityUncertain ? turnStartedAt : null);
  const headSetupElapsed = elapsedTurnTime(() => headSettingUp ? workerSetupStartedAtUnixMilliseconds(workerSetup) : null);
  // A key request blocks on the person as an ask or a question does, so the bar says so too.
  let credentialWaiting = $derived(!readOnly && awaitsCredential(rows, reader?.pages.flatMap(page => page.items) ?? []));
  // A turn that failed, or a message that was not delivered, is said in the bar until the
  // person sends something after it: the same facts the dock's line and the error rows use.
  let headFailure = $derived(outgoingMessages.length > 0 ? null : paneFailureFrom(reader?.status
    ? { lastBoundaryFailed: reader.status.rest.last_boundary_failed, latestKind: reader.status.rest.latest_happened?.kind ?? null }
    : { lastBoundaryFailed: restLineMarksFrom(rows, ownerReadThroughSequence, running).failed, latestKind: rows.at(-1)?.kind ?? null }));
  // The newest thing that happened is the turn being stopped: "Interrupted" until the next turn.
  let headInterrupted = $derived(outgoingMessages.length > 0 ? false : interruptedFrom(reader?.status
    ? reader.status.rest.latest_happened as { kind: string; ending?: string } | null
    : rows.findLast(row => row.kind === "turn_ended" || row.kind === "turn_stopped" || row.kind === "prompt") as { kind: string; ending?: string } | undefined));
  /** The one state of this conversation. The header, the dock's line and the turn meta read
   *  it, so they never disagree; "Working" is never shown while an ask waits. Its words never
   *  name a tool or command, so it reads the same in Focus and Full. */
  function stateFacts() {
    return {
      readOnly,
      activityUncertain,
      waitingForYou: ask !== null || userInput !== null || credentialWaiting || waitingOnPage !== null,
      waitingFor: ask !== null ? "approval" as const : userInput !== null ? "answer" as const : credentialWaiting ? "key" as const : waitingOnPage,
      running,
      backgroundWorking,
      settingUp: headSettingUp && workerSetup ? workerSetupSentence(workerSetup, headSetupElapsed.seconds) : null,
      elapsed: headElapsed.seconds === null ? null : formatDuration(headElapsed.seconds),
      failure: headFailure,
      interrupted: headInterrupted
    };
  }
  // Said once: the top bar carries only the states the thread has no place for.
  let headStatus = $derived(paneHeadStatus(stateFacts()));
  let restState = $derived(readConversationState(stateFacts()));
  /** What the waiting ask is about, for the dock's line ("Needs your approval — Upload build 41"). */
  let restWaitingTitle = $derived(ask?.title ?? null);
  let jump = $state<ConversationJump | null>(null);
  let workspaceCopied = $state(false);
  let workspaceCopiedTimer: ReturnType<typeof setTimeout> | null = null;
  async function copyWorkspace(path: string): Promise<void> {
    if (!(await copyText(path))) return;
    workspaceCopied = true;
    if (workspaceCopiedTimer) clearTimeout(workspaceCopiedTimer);
    workspaceCopiedTimer = setTimeout(() => (workspaceCopied = false), 1500);
  }
  // A model list that could not be read because the machine is not connected is that same
  // machine state, which the dock already says. The model picker keeps the sentence and its
  // retry; the red line under the box is kept for failures nothing else explains.
  /** v11 (4): Edit on a queued message: it leaves the queue, and its words come back to the
   *  composer (after anything already written there). */
  let composerHandle = $state<{ takeBackQueuedWords: (words: string) => void } | null>(null);
  async function editHeldPrompt(heldPromptId: string, words: string): Promise<void> {
    if (!onDiscardHeldPrompt || composerHandle === null) return;
    await onDiscardHeldPrompt(heldPromptId);
    if (heldPromptRows.some((row) => row.heldPromptId === heldPromptId)) return;
    composerHandle.takeBackQueuedWords(words);
  }
  let composerErrorNote = $derived(activityUncertain && errorNote !== null && errorNote === readError ? null : errorNote);

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
    // The line's own label already says what the person is asked for.
    if (userInput || credentialWaiting || waitingOnPage !== null) return null;
    if (restLine?.waiting) return restLine.text;
    const latest = reader?.status?.rest.latest_happened ?? rows.at(-1);
    const said = reader?.status ? happened(reader.status.rest.latest_happened, ownSenderLabel ?? "") : latest ? whatThisRowSays(latest as TranscriptRow, ownSenderLabel ?? "") : null;
    return dockFeedbackFor(latest ?? null, said);
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
    if (conversationState === "opened") {
      event.preventDefault();
      conversationState = "rest";
      void tick().then(() => paneElement?.querySelector<HTMLButtonElement>("[data-conversation-state-control]")?.focus({ preventScroll: true }));
    }
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

  /** On a phone the owner types in the open conversation, which stands directly above the
   *  keyboard, never in the dock over a page the browser scrolls to show the field. The
   *  person's own focus decides, not only a tap on the field: a phone can move a near-miss
   *  tap's focus onto it. */
  function openForWritingOnAPhone(): void {
    if (conversationState === "rest" && window.matchMedia(COMPACT_LAYOUT_MEDIA_QUERY).matches) conversationState = "opened";
  }

  function stopFromTheMenu(): void {
    closeMenu();
    onStop?.();
    menuButton?.focus();
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
    <div class="chat-head-identity" data-conversation-head-identity>
      <span class="chat-head-name">
        <span class="chat-head-title" data-conversation-title title={headTitle} use:fittedTitle={headTitle}>{headTitle}</span>
      </span>
      {#if headStatus}
        <!-- One derived state (conversationState), the same the dock's line reads. -->
        <span class="cv-state" data-conversation-head-status={headStatus.state}
          role="status" aria-label={headStatus.text} title={headStatus.text}>
          {#if headStatus.mark === "spinner"}<span class="cv-spinner" aria-hidden="true"></span>
          {:else if headStatus.mark === "dot"}<span class="cv-dot" aria-hidden="true"></span>
          {:else if headStatus.mark === "alert"}<svg class="cv-glyph" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.25" /><path d="M8 4.9v3.6" /><path d="M8 11.1v.1" /></svg>
          {:else if headStatus.mark === "stop"}<svg class="cv-glyph" viewBox="0 0 16 16" aria-hidden="true"><rect x="3.5" y="3.5" width="9" height="9" rx="2" /></svg>{/if}
          <span class="cv-state-text" aria-hidden="true">{headStatus.text}</span>
        </span>
      {/if}
      {#if connectionTrouble}
        <span class="cv-state" data-conversation-connection role="img" aria-label="Connection trouble" title="Connection trouble">
          <svg class="cv-glyph cv-glyph--danger" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 6.2a8 8 0 0 1 11 0" /><path d="M4.6 8.6a5 5 0 0 1 6.8 0" /><path d="M8 11.6v.1" /><path d="m3 3 10 10" /></svg>
        </span>
      {/if}
    </div>
    <!-- Two named choices rather than one control whose label is the state it is in:
         which lens you are in and which you can move to are both on the screen. -->
    <div
      class="cv-segmented"
      role="group"
      aria-label="How much of the conversation to show (F)"
      data-conversation-lens-group
    >
      <button
        type="button"
        class="cv-segment"
        data-conversation-lens-choice="focus"
        aria-pressed={lens === "focus"}
        title="What is said to you (F)"
        onclick={() => (lens = "focus")}
      >Focus</button>
      <button
        type="button"
        class="cv-segment"
        data-conversation-lens-choice="full"
        aria-pressed={lens === "full"}
        title="Also the work as it happens (F)"
        onclick={() => (lens = "full")}
      >Full</button>
    </div>
    <div class="chat-head-right">
      {#if stateControl !== null && conversationState !== "rest"}
        <!-- v8 (14): a text button "Dock", the label alone (no chevron), the same style as
             the dock's "Open conversation". -->
        <button
          type="button"
          class="chat-head-collapse"
          data-conversation-state-control
          data-conversation-expand={stateControl.opensIt ? true : undefined}
          data-conversation-collapse={stateControl.opensIt ? undefined : true}
          aria-label={stateControl.label}
          title={stateControl.label}
          onclick={moveThroughTheStates}
        >
          <span class="chat-head-collapse-label">{stateControl.opensIt ? "Open conversation" : "Dock"}</span>
          <!-- v11 (6): no arrow after "Dock" (the pill stays); only the dock's Open
               conversation keeps its arrow (owner). -->
        </button>
      {/if}
      {#if !readOnly || historyOwner || pastConversations.length > 0 || workspaceFolder}
        <div class="chat-overflow" bind:this={menuElement}>
          <button
            type="button"
            class="cv-icon-button"
            bind:this={menuButton}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label="Conversation options"
            title="Conversation options"
            onclick={() => (menuOpen ? closeMenu() : (menuOpen = true))}
          >
            <svg viewBox="0 0 16 16" aria-hidden="true" class="cv-dots">
              <circle cx="3.5" cy="8" r="1.25" />
              <circle cx="8" cy="8" r="1.25" />
              <circle cx="12.5" cy="8" r="1.25" />
            </svg>
          </button>
          {#if menuOpen}
            <div class="chat-overflow-menu" data-conversation-options-menu>
              {#if !readOnly}
                <div class="chat-overflow-actions" role="menu" aria-label="Conversation">
                  {#if running && onStop}
                    <!-- v14 (2): while a turn is open (working, or waiting on an ask) the menu
                         leads with Stop; an ask has no Stop button of its own. -->
                    <button
                      type="button"
                      class="chat-overflow-item"
                      role="menuitem"
                      data-conversation-stop={true}
                      data-conversation-menu-stop
                      onclick={stopFromTheMenu}
                    >Stop the agent</button>
                  {/if}
                  {#if confirmArmed}
                    <button
                      type="button"
                      class="chat-overflow-item chat-overflow-item--confirm"
                      role="menuitem"
                      data-conversation-new-confirm
                      onclick={confirmNewConversation}
                    >Confirm — this ends the current one</button>
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
                <div class="chat-overflow-section">
                  <span class="chat-overflow-label" aria-hidden="true">Earlier conversations</span>
                  <ConversationHistoryPicker owner={historyOwner} bind:selected={selectedPastConversationId} label={pastConversationsLabel} {conversationExists} />
                </div>
              {:else if pastConversations.length > 0}
                <label class="chat-overflow-section chat-overflow-history" data-conversation-history>
                  <span class="chat-overflow-label">Earlier conversations</span>
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
                <!-- A fact, not an action: where the agent works. The path wraps, because
                     either truncated end can be the part that identifies it. -->
                <div class="chat-overflow-section chat-overflow-fact" data-conversation-workspace>
                  <span class="chat-overflow-label">Workspace</span>
                  <div class="chat-overflow-fact-row">
                    <span class="chat-overflow-path">{workspaceFolder}</span>
                    <button type="button" class="cv-icon-button cv-icon-button--small" data-conversation-workspace-copy
                      aria-label={workspaceCopied ? "Workspace path copied" : "Copy the workspace path"}
                      title={workspaceCopied ? "Copied" : "Copy"}
                      onclick={() => void copyWorkspace(workspaceFolder)}>
                      <svg viewBox="0 0 16 16" aria-hidden="true">
                        {#if workspaceCopied}<path d="m3.5 8.5 3 3 6-7" />
                        {:else}<rect x="5.5" y="5.5" width="8" height="8" rx="1.75" /><path d="M10.5 3.5v-.25A1.75 1.75 0 0 0 8.75 1.5h-5.5A1.75 1.75 0 0 0 1.5 3.25v5.5A1.75 1.75 0 0 0 3.25 10.5h.25" />{/if}
                      </svg>
                    </button>
                  </div>
                </div>
              {/if}
            </div>
          {/if}
        </div>
      {/if}
    </div>
  </div>

  {#snippet waitingMessages()}
    <HeldPromptStack
      rows={heldPromptRows}
      {conversationId}
      supportsSteer={supportsSteer && ask === null && userInput === null}
      {running}
      onDiscard={onDiscardHeldPrompt}
      onPromote={onPromoteHeldPrompt}
      onStopDrawing={onStopDrawingHeldPrompt}
      onSendAgain={onSendHeldPromptAgain}
      onEdit={readOnly || !onDiscardHeldPrompt ? undefined : editHeldPrompt}
    />
  {/snippet}
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
    afterTranscript={readOnly ? undefined : waitingMessages}
    bind:jump
  />
  </div>

  </div>

  <!-- Activity and the latest unread preview belong to the dock surface above the input. -->
  {#if conversationState === "rest"}
    <ConversationRestBar preview={unreadPreview} {roleLabel}
      state={restState}
      {connectionTrouble}
      waitingTitle={restLine?.waiting ? restWaitingTitle : null}
      pageHoldsTheAsk={waitingOnPage !== null && ask === null && userInput === null && !credentialWaiting}
      askBelow={ask !== null || userInput !== null || credentialWaiting}
      offline={activityUncertain}
      feedback={activityUncertain ? uncertainActivityFeedback(!running && !backgroundWorking && workerSetup !== null) : dockFeedback} onOpen={moveThroughTheStates} />
  {/if}

  <!-- v5: the way back to the newest line is a 36px round icon button at the bottom-right of
       the column, 12px above the composer. The slot takes no room in the flow. -->
  {#if jump && conversationState !== "rest"}
  <div class="cv-status-slot" data-conversation-status-slot>
      <button
        type="button"
        class="cv-jump"
        data-conversation-jump
        aria-label={jump.label === "Jump to latest" ? "Jump to latest message" : `Jump to latest message (${jump.label})`}
        title={jump.label}
        onclick={(event) => jump?.go(event.currentTarget)}
      ><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.5v9M4.5 9 8 12.5 11.5 9" /></svg></button>
  </div>
  {/if}

  {#if readOnly}
    <div class="conversation-read-only" data-conversation-read-only-notice>
      Past conversation · read only
    </div>
  {:else}
    <ConversationComposer
      bind:this={composerHandle}
      {draftKey}
      {uploadDestination}
      {reconcileLocalSend}
      conversationId={conversationExists ? conversationId : null}
      {backendKey}
      {conversationExists}
      {running}
      {ask}
      {credentialRequest}
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
      errorNote={composerErrorNote}
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
      onFieldChosen={openForWritingOnAPhone}
    />
  {/if}
</div>

<style>
  .conversation-reader { display:flex; flex-direction:column; flex:1; min-height:0; }
  /* v4.1 (revised): an empty conversation is the composer in its normal bottom place, as in
     every other state, and an empty canvas above it. No placement of its own. */
  .conversation-reader[hidden] { display:none; }
  .conversation-transcript-region { display:flex; flex:1; min-height:0; min-width:0; }
  /* The component measures itself, not the window. The same pane is narrow in a side
     panel on a wide screen, and a host that names no container of its own — the Chief
     conversation is one — still gets the adaptation. */
  :global([data-conversation-pane]) {
    container-type: inline-size;
    container-name: conversation-pane;
  }
  /* The stack under the transcript (status slot, composer) has one gap: 8. */
  :global(.conversation-layer [data-conversation-pane]) { gap: 0; }
  /* Rest keeps the same child viewport mounted, but gives its head and thread no display. */
  :global([data-conversation-pane][data-conversation-state="rest"] .chat-head),
  :global([data-conversation-pane][data-conversation-state="rest"] .chat-thread-shell) {
    display: none;
  }

  /* --- Header: title, state, lens, collapse, options. 56 tall; space, not a rule, sets it
     off from the transcript (the transcript's own 16px fade takes what scrolls under it). */
  /* Pass 11: at least 24px between the title (or role) and the Focus/Full control. */
  .chat-head-identity {
    display: flex;
    flex: 1 1 auto;
    margin-inline-end: 12px;
    align-items: center;
    gap: var(--cv-s3);
    min-width: 0;
    overflow: hidden;
  }
  /* v5: the ticket's title alone, one line, ellipsized. No role beside it. */
  .chat-head-name { display: flex; align-items: baseline; min-width: 0; flex: 0 1 auto; }
  .chat-head-title {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
    color: #ececec;
    /* v10 (7): the header title 17/24 600 (answer headings are 17 at most). */
    font: var(--cv-weight-title) 17px/24px var(--cv-font);
  }
  .cv-state {
    display: inline-flex;
    flex: 0 1 auto;
    align-items: center;
    gap: 6px;
    min-width: 0;
    color: var(--cv-t3);
    font: var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .cv-state-text { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  /* Weight follows the state: the person's accent when it waits on them, the danger colour
     when something failed, quiet meta for everything moving or past. */
  .cv-state[data-conversation-head-status="waiting"] { color: var(--cv-accent); font-weight: var(--cv-weight-label); }
  .cv-state:is([data-conversation-head-status="failed"], [data-conversation-head-status="undelivered"]) { color: var(--cv-danger); font-weight: var(--cv-weight-label); }
  .cv-state[data-conversation-head-status="working"] { color: var(--cv-t3); }

  /* v5.1: "Dock ⌄", the dock's "Open conversation ⌃" style: 32 high, 13 medium #d9d9d9. */
  .chat-head-collapse {
    flex: none;
    display: inline-flex;
    align-items: center;
    gap: var(--cv-s1);
    /* v7: a quiet Button: 32 tall pill, 13/20 500, #bdbdbd, its 16px chevron 6px after. */
    gap: 6px;
    height: 32px;
    padding: 0 14px;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: #bdbdbd;
    font: var(--cv-weight-label) 13px/20px var(--cv-font);
    cursor: pointer;
    transition: color var(--cv-dur-fast) var(--cv-ease-out), background-color var(--cv-dur-fast) var(--cv-ease-out);
  }
  /* v10 (5): the same resting pill as the dock's Open conversation: #232323, #d9d9d9, hover
     #2a2a2a. v11 (6): the label alone, no arrow. */
  .chat-head-collapse { background: #232323; color: #d9d9d9; }
  .chat-head-collapse:hover, .chat-head-collapse:focus-visible { color: #ececec; background-color: #2a2a2a; }
  .chat-head-collapse:focus-visible { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: var(--cv-focus-offset); }

  /* --- Lens: a segmented control. The track is a well; the chosen segment is a control. */
  /* v9 (16): Focus and Full sit in one segmented track: #1c1c1c, radius 10, a 2px inset; the
     shown lens is a #2a2a2a segment (#ececec), the other quiet (#bdbdbd, hover #ececec on
     #242424). Segments 28 tall (the track 32), radius 8, 13/20 500, padding 0 12. Then 16px
     (the header's 12 gap + 4) before Dock and more, which are quiet buttons. */
  .cv-segmented {
    display: inline-flex;
    flex: none;
    gap: 2px;
    padding: 2px;
    border-radius: 10px;
    background: #1c1c1c;
    margin-inline-end: 4px;
  }
  .cv-segment {
    min-height: 28px;
    height: 28px;
    padding: 0 12px;
    border: 0;
    border-radius: 8px;
    background: transparent;
    color: #bdbdbd;
    font: var(--cv-weight-label) 13px/20px var(--cv-font);
    cursor: pointer;
    transition: color var(--cv-dur-fast) var(--cv-ease-out), background-color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .cv-segment:hover { background: #242424; color: #ececec; }
  .cv-segment[aria-pressed="true"], .cv-segment[aria-pressed="true"]:hover { background: #2a2a2a; color: #ececec; }
  .cv-segment:focus-visible { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: var(--cv-focus-offset); }

  .conversation-read-only {
    flex: none;
    padding: var(--cv-s3) 0 var(--cv-s5);
    color: var(--cv-t3);
    font: var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font);
    text-align: center;
  }
</style>

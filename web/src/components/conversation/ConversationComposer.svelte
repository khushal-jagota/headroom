<script lang="ts">
  /** The one place text goes in, and the one place a change to the run rides out on.
   *
   * What is typed here is this component's own and nothing arriving from the server ever
   * touches it. A model or effort picked here changes nothing until the next message
   * carries it; closing the picker without sending leaves the conversation exactly as it
   * was. When an ask is waiting the whole box gives way to it, because until it is
   * answered there is nothing else this composer could usefully do.
   *
   * Pressing Enter empties the box there and then and the box stays typeable, because the
   * message is already gone as far as the person is concerned — only the send control says
   * anything is still happening. If the send turns out not to have gone anywhere, its text,
   * pictures and run choices come back exactly as they were, with the cursor at the end;
   * unless something else has been composed in the meantime, in which case that draft is
   * what matters and the error under the box is the whole of the news.
   *
   * Writing a catalog token starts the message with slash, dollar, or at. While the cursor
   * is still inside that first word the eligible catalog entries are offered under it.
   * Choosing one inserts its exact text. The message still goes as ordinary text.
   */
  import { onMount, tick } from "svelte";
  import { videoUploadAdapter } from "../../lib/conversation/videoUploads/adapter";
  import { mintOutgoingMessage, reserveOutgoingMessageImages, reserveOutgoingMessageFiles, releaseOutgoingMessageImages, releaseOutgoingMessageFiles } from "../../lib/conversation/outgoing";
  import { VideoTransfers } from "../../lib/conversation/videoUploads/uploads";
  import { isVideoFile, VIDEO_ACCEPT } from "../../lib/conversation/videoUploads/config";
  import type { PendingVideo, UploadDestination, ComposerSendContext } from "../../lib/conversation/videoUploads/contracts";
  import { LocalComposerSends, releaseRemovedLocalSends, recallLocalSends, rememberLocalSends, recallDraftVideo, rememberDraftVideo, type PendingComposerSend } from "../../lib/conversation/videoUploads/localSends";

  import ComposerCatalogMenu from "./ComposerCatalogMenu.svelte";
  import {
    orderedComposerAttachments, attachmentExtension, attachmentByteLabel,
    type ComposerAttachmentReference
  } from "./composer/attachments";
  import { createComposerTextareaMeasurement } from "./composer/textareaMeasurement";
  import ComposerRunControls from "./composer/ComposerRunControls.svelte";
  import HeldPromptStack from "./composer/HeldPromptStack.svelte";
  import PermissionAskActions from "./PermissionAskActions.svelte";
  import PermissionAskCard from "./PermissionAskCard.svelte";
  import UserInputQuestionPanel from "./UserInputQuestionPanel.svelte";
  import {
    beginComposerSend,
    restoreRefusedComposerSend,
    type ComposerDraft,
    type ComposerSendAttempt
  } from "./composer/draftTransaction";
  import { untrack } from "svelte";
  import { readComposerDraft, writeComposerDraft } from "../../lib/conversation/composerDraft";
  import {
    applyComposerRunSelectionIntent,
    resolveComposerRunControls
  } from "../../lib/conversation/runControls/logic/runSelection";
  import type {
    ComposerRunControlIntents,
    ComposerRunControlsInput,
    ComposerRunSelection,
    ComposerRunSelectionIntent
  } from "../../lib/conversation/runControls/contracts";
  import { askPlaceholder } from "../../lib/conversation/composer";
  import type { RunValues } from "../../lib/conversation/composer";
  import { catalogTokenAtMessageStart } from "../../lib/conversation/composerCatalog";
  import type { HeldPromptRow } from "../../lib/conversation/heldPrompts";
  import { COMPOSITION_STATE_EVENT } from "../../lib/releaseMonitor";
  import {
    createVoiceCapture,
    formatVoiceTime,
    voiceCaptureAvailable,
    voiceCaptureSupported,
    voiceFirstComposer,
    type VoiceCapture,
    type VoiceCaptureState
  } from "../../lib/conversation/voiceCapture";
  import {
    createPendingConversationImages,
    pendingConversationImageBytes,
    releasePendingImages,
    type PendingConversationImage
  } from "../../lib/conversation/pendingImages";
  import {
    CONVERSATION_FILE_ACCEPT,
    createPendingConversationFiles,
    pendingConversationFileBytes,
    type PendingConversationFile
  } from "../../lib/conversation/pendingFiles";
  import type {
    ComposerCatalogEntry,
    ComposerCatalogEntryKind,
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
    conversationId = null,
    draftKey,
    uploadDestination = null,
    reconcileLocalSend = async () => false,
    backendKey = null,
    conversationExists = false,
    running = false,
    ask = null,
    userInput = null,
    askNote = null,
    userInputNote = null,
    permissionAvailable = true,
    userInputAvailable = true,
    current = { model: null, reasoningEffort: null },
    canonicalRows,
    validation = null,
    onRetryModelChoices,
    onPreviewRunSelection,
    models = [],
    backends = $bindable([]),
    effortOptions = [],
    composerCatalog = [],
    startsOnModel = null,
    startsOnReasoningEffort = null,
    heldPromptRows = [],
    supportsSteer = false,
    fateNote = null,
    errorNote = null,
    placeholder = "Message the agent...",
    disabled = false,
    showRunPicker = true,
    onSend,
    onStop,
    onAnswer,
    onSubmitUserInput,
    onCancelTurn,
    onDiscardHeldPrompt,
    onPromoteHeldPrompt,
    onStopDrawingHeldPrompt,
    onSendHeldPromptAgain
  }: {
    /** The current conversation, or none before the first message creates it. */
    conversationId?: string | null;
    draftKey: string;
    uploadDestination?: UploadDestination | null;
    reconcileLocalSend?: (messageId: string) => Promise<boolean>;
    /** The backend this conversation runs on — or, before there is one, the backend a
     *  message sent from here would create it on. */
    backendKey?: ConversationBackendKey | null;
    /** Whether there is a conversation yet. It is what fixes the backend: one that exists
     *  has a live process behind it and cannot be moved to another. */
    conversationExists?: boolean;
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
    current?: RunValues;
    /** What this conversation's own backend offers. */
    canonicalRows?: readonly import("../../lib/conversation/modelResolutionWire").PickerRow[];
    validation?: import("../../lib/conversation/modelResolutionWire").CanonicalModelValidation | null;
    onRetryModelChoices?: () => Promise<void>;
    onPreviewRunSelection?: (selection: ComposerRunSelection, backend?: ConversationBackendKey) => void;
    models?: readonly BackendModel[];
    /** Every backend this machine reported, each with its own catalog. What the rail
     *  chooses from before a conversation exists; empty is a caller that has not read
     *  them, and then there is nothing to switch to. */
    backends?: readonly BackendSnapshot[];
    effortOptions?: readonly string[];
    /** The typed text shortcuts this conversation offers in the composer. */
    composerCatalog?: readonly ComposerCatalogEntry[];
    /** What a conversation started from here would run on, before there is one: the
     *  owner's own values, resolved by the same code that will create it. They are what
     *  the selectors show with nothing picked, and neither is ever offered as an option.
     *  The model rides out with the message that creates the conversation, because a
     *  conversation is created on a model somebody can name; the effort does not, because
     *  a model that takes none is a real answer and an untouched control has not given one. */
    startsOnModel?: string | null;
    startsOnReasoningEffort?: string | null;
    heldPromptRows?: readonly HeldPromptRow[];
    supportsSteer?: boolean;
    fateNote?: string | null;
    errorNote?: string | null;
    placeholder?: string;
    disabled?: boolean;
    /** False when an empty-state form owns the same start values on this screen. */
    showRunPicker?: boolean;
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
    onDiscardHeldPrompt?: (heldPromptId: string) => Promise<void> | void;
    onPromoteHeldPrompt?: (
      heldPromptId: string,
      mode: "send_now" | "steer"
    ) => Promise<void> | void;
    onStopDrawingHeldPrompt?: (senderMessageId: string) => Promise<void> | void;
    onSendHeldPromptAgain?: (senderMessageId: string) => Promise<void> | void;
  } = $props();

  // Restored before the first paint, so a rebuilt composer comes back with the words
  // in it rather than empty.
  let text = $state(untrack(() => readComposerDraft(draftKey)));
  let draftKeyOnScreen = untrack(() => draftKey);
  $effect(() => {
    const key = draftKey;
    if (key === untrack(() => draftKeyOnScreen)) return;
    draftKeyOnScreen = key;
    text = readComposerDraft(key);
  });
  $effect(() => { writeComposerDraft(draftKey, text); });
  let deliveryMode = $state<PromptDeliveryMode>("steer");
  let runSelection = $state<ComposerRunSelection>({
    pickedBackend: null,
    pickedModel: null,
    pickedReasoningEffort: null
  });
  let runSelectionDraftKey = untrack(() => draftKey);
  $effect(() => {
    const key = draftKey;
    if (key === untrack(() => runSelectionDraftKey)) return;
    runSelectionDraftKey = key;
    runSelection = {pickedBackend: null, pickedModel: null, pickedReasoningEffort: null};
  });
  let inputElement = $state<HTMLTextAreaElement | null>(null);
  // Counted rather than flagged: the box stays typeable through a send, so a second
  // message can be on its way before the first one has landed.
  let sendsInFlight = $state(0);
  /** Where the cursor is in the box, read back from it after anything that can have moved
   *  it. Which command is being written is a question about where the cursor is, and the
   *  text alone cannot answer it. */
  let cursorAt = $state(0);
  /** Whether the box has the cursor at all. Where it is is only a question while it is in
   *  here, so a person who has gone to read the thread is not writing a command and the
   *  menu is not floating over what they went to read. */
  let theCursorIsInTheBox = $state(false);
  /** Escape, on a catalog token still being written. Forgotten after the cursor leaves. */
  let menuWasDismissed = $state(false);
  let activeCatalogEntryIndex = $state(0);
  let pendingImages = $state<PendingConversationImage[]>([]);
  let pendingFiles = $state<PendingConversationFile[]>([]);
  let pendingVideos = $state.raw<PendingVideo[]>([]);
  let localSends = $state.raw<PendingComposerSend[]>([]);
  let restoredSendContext: ComposerSendContext | null = null;
  let nextVideoId = 1;
  let localStorageKey = untrack(() => draftKey);
  const transfers = new VideoTransfers(videoUploadAdapter, videoChanged);
  const makeSendQueue = () => new LocalComposerSends(async (entry, content) => {
    if (destroyed) return false;
    return onSend(content, entry.mode, entry.attempt.carriedRunValues, entry.context);
  }, videoChanged);
  let sendQueue = makeSendQueue();
  function videoChanged(): void {
    if (destroyed) return;
    const previousEntries = localSends;
    pendingVideos = [...pendingVideos];
    localSends = [...sendQueue.entries];
    rememberDraftVideo(localStorageKey, pendingVideos);
    rememberLocalSends(localStorageKey, localSends);
    releaseRemovedLocalSends(previousEntries, localSends);
    void sendQueue.pump();
  }
  async function restoreLocalState(): Promise<void> {
    const restoringKey = localStorageKey;
    const restoringQueue = sendQueue;
    pendingVideos = recallDraftVideo(localStorageKey);
    nextVideoId = Math.max(1, ...pendingVideos.map(video => video.id + 1));
    attachmentOrder = [...attachmentOrder.filter(item => item.kind !== "video"), ...pendingVideos.map(video => ({kind: "video" as const, id: video.id}))];
    sendQueue.entries = recallLocalSends(localStorageKey);
    for (const entry of sendQueue.entries) reserveLocalSend(entry);
    localSends = [...sendQueue.entries];
    for (const entry of [...sendQueue.entries]) {
      if (entry.phase !== "dispatching") continue;
      try {
        const alreadyDispatched = await reconcileLocalSend(entry.context.messageId);
        if (destroyed || restoringKey !== localStorageKey || restoringQueue !== sendQueue) return;
        if (alreadyDispatched) {
          sendQueue.entries = sendQueue.entries.filter(candidate => candidate !== entry);
        } else { entry.phase = "interrupted"; entry.needsReconciliation = false; }
      } catch {
        entry.phase = "interrupted"; entry.needsReconciliation = true;
        entry.error = "The earlier send could not be checked. Check the conversation before sending it again.";
      }
    }
    if (destroyed || restoringKey !== localStorageKey || restoringQueue !== sendQueue) return;
    for (const video of pendingVideos) if (video.uploadId && video.phase === "validating") void transfers.validate(video);
    videoChanged();
  }

  let attachmentOrder = $state<ComposerAttachmentReference[]>([]);
  let attachments = $derived(orderedComposerAttachments(attachmentOrder, pendingImages, pendingFiles, pendingVideos));
  let imageInput = $state<HTMLInputElement | null>(null);
  let fileInput = $state<HTMLInputElement | null>(null);
  $effect(() => {
    const key = draftKey;
    if (key === untrack(() => localStorageKey)) return;
    untrack(() => {
      rememberLocalSends(localStorageKey, sendQueue.entries);
      rememberDraftVideo(localStorageKey, pendingVideos);
      transfers.dispose(); sendQueue.dispose();
      releasePendingImages(pendingImages);
      pendingImages = []; pendingFiles = []; pendingVideos = []; attachmentOrder = [];
      localStorageKey = key; sendQueue = makeSendQueue();
      void restoreLocalState();
    });
  });
  let nextImageId = 1;
  let nextFileId = 1;
  let dragDepth = 0;
  let draggingAttachments = $state(false);
  let intakeError = $state<string | null>(null);
  let intakeTail: Promise<void> = Promise.resolve();
  let attachmentIntakesInFlight = $state(0);
  /** Changes only when the person composes something new. A refusal may restore its
   *  snapshot only while this is still the revision that was sent. */
  let compositionRevision = 0;
  let destroyed = false;

  // --- speaking instead of typing ----------------------------------------------------------
  /** Whether the person asked for the keyboard instead of voice-first, kept for the
   *  session so the composer does not snap back to the mic while they type. */
  const KEYBOARD_PREFERRED = "panels.composer.voiceKeyboardPreferred";
  let coarsePointer = $state(false);
  let voiceSupported = $state(false);
  let keyboardPreferred = $state(false);
  let voiceState = $state<VoiceCaptureState>({ phase: "idle" });
  let voice: VoiceCapture | null = null;

  let takenOver = $derived(ask !== null || userInput !== null);
  let inputDisabled = $derived(disabled || takenOver || attachmentIntakesInFlight > 0);
  /** Browser recording capability controls availability. The question panel and
   *  permission ask always outrank voice. */
  let voiceAvailable = $derived(voiceCaptureAvailable(voiceSupported, !takenOver));
  let voiceTakeover = $derived(voiceState.phase !== "idle");
  /** The empty composer on a phone leads with the mic. Anything composed, disabled, or
   *  remembered as keyboard-preferred yields to the normal composer. */
  let voiceFirstIdle = $derived(
    voiceFirstComposer({
      available: voiceAvailable,
      coarsePointer,
      active: voiceTakeover,
      keyboardPreferred,
      disabled: inputDisabled,
      empty: text === "" && pendingImages.length === 0 && pendingFiles.length === 0 && pendingVideos.length === 0
    })
  );
  let livePlaceholder = $derived(ask !== null ? askPlaceholder(ask) : placeholder);
  /** The Add menu, and the camera the design's third choice needs. */
  let addMenuOpen = $state(false);
  let addMenuElement = $state<HTMLDivElement | null>(null);
  let cameraInput = $state<HTMLInputElement | null>(null);

  /** Escape shuts this menu before it means anything else.
   *
   * The pane reads the same key to step the conversation back one height, and it stands
   * aside for a key something else has already answered. Both listen on the window, and
   * the pane is the older listener, so this one has to be in the capture phase to get
   * there first. */
  // The foot this menu lives in is replaced whole by a question or a permission ask.
  // A menu that is not on the screen must not still be holding the Escape key.
  $effect(() => {
    if (userInput !== null || takenOver || inputDisabled) addMenuOpen = false;
  });

  $effect(() => {
    if (!addMenuOpen) return;
    const shutTheMenu = (event: KeyboardEvent): void => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      addMenuOpen = false;
    };
    window.addEventListener("keydown", shutTheMenu, true);
    return () => window.removeEventListener("keydown", shutTheMenu, true);
  });

  let runControlsInput = $derived<ComposerRunControlsInput>({
    selection: runSelection,
    canonicalRows,
    validation,
    backendKey,
    conversationExists,
    running,
    current,
    models,
    backends,
    effortOptions,
    startsOnModel,
    startsOnReasoningEffort,
    inputDisabled,
    hasSendableContent: text.trim() !== "" || pendingImages.length > 0 || pendingFiles.length > 0 || pendingVideos.length > 0,
    sendsInFlight,
    deliveryMode
  });
  let runControlsView = $derived(resolveComposerRunControls(runControlsInput));
  let canonicalMode = $derived(canonicalRows !== undefined);
  $effect(() => {
    const selection = runSelection;
    if (canonicalMode) untrack(() => onPreviewRunSelection?.(selection));
  });
  /** A retained page must not reload while this composer owns work or a user decision. */
  let compositionActive = $derived(
    text !== ""
    || pendingImages.length > 0
    || pendingFiles.length > 0
    || pendingVideos.length > 0
    || localSends.length > 0
    || voiceState.phase !== "idle"
    || takenOver
    || draggingAttachments
    || attachmentIntakesInFlight > 0
    || runSelection.pickedBackend !== null
    || runSelection.pickedModel !== null
    || runSelection.pickedReasoningEffort !== null
    || runSelection.selectionKind !== undefined
    || runSelection.effortIsSet === true
    || deliveryMode !== "steer"
  );

  function publishCompositionState(active: boolean): void {
    document.dispatchEvent(new CustomEvent(COMPOSITION_STATE_EVENT, { detail: { active } }));
  }

  $effect(() => {
    publishCompositionState(compositionActive);
  });

  // --- the catalog token being written ----------------------------------------------------
  let catalogTokenUnderway = $derived(catalogTokenAtMessageStart(text, cursorAt));
  let typedCatalogText = $derived(catalogTokenUnderway?.typedSoFar ?? null);
  let catalogMenuIsOpen = $derived(
    !inputDisabled
    && theCursorIsInTheBox
    && typedCatalogText !== null
    && !menuWasDismissed
  );
  let eligibleCatalogEntries = $derived(
    catalogEntriesForTrigger(composerCatalog, catalogTokenUnderway?.trigger ?? null)
  );
  let matchingCatalogEntries = $derived(
    catalogEntriesMatching(
      eligibleCatalogEntries,
      typedCatalogText ?? "",
      catalogTokenUnderway?.trigger ?? null
    )
  );

  // A dismissal belongs to the active token. Leaving the token clears it.
  $effect(() => {
    if (typedCatalogText === null) menuWasDismissed = false;
  });

  /* The menu mounts and reflows under a pointer that nobody moved, and the browser
   * reports the pointer's resting place each time. That is geometry, not a person
   * choosing a row. The composer stays mounted while the menu comes and goes, so the
   * pointer's last place is remembered here and survives every opening. */
  let pointerSpot: { x: number; y: number } | null = null;
  let pointerReallyMoved = false;

  $effect(() => {
    function trackPointer(event: MouseEvent): void {
      pointerReallyMoved =
        pointerSpot !== null && (event.clientX !== pointerSpot.x || event.clientY !== pointerSpot.y);
      pointerSpot = { x: event.clientX, y: event.clientY };
    }
    document.addEventListener("mousemove", trackPointer, { capture: true, passive: true });
    return () => document.removeEventListener("mousemove", trackPointer, true);
  });

  // A different list is a different highlight, and it starts at the top.
  $effect(() => {
    matchingCatalogEntries;
    activeCatalogEntryIndex = 0;
  });

  $effect(() => {
    if (!inputDisabled) return;
    dragDepth = 0;
    draggingAttachments = false;
  });

  // Catalog changes may make a pending pick meaningless. That is reconciliation, not
  // something the person composed, so applying it never advances the draft revision.
  $effect(() => {
    const normalized = runControlsView.normalizedSelection;
    if (sameRunSelection(runSelection, normalized)) return;
    runSelection = normalized;
  });

  function sameRunSelection(
    one: ComposerRunSelection,
    other: ComposerRunSelection
  ): boolean {
    return (
      one.pickedBackend === other.pickedBackend
      && one.pickedModel === other.pickedModel
      && one.pickedReasoningEffort === other.pickedReasoningEffort
      && one.selectionKind === other.selectionKind
      && one.effortIsSet === other.effortIsSet
    );
  }

  /** Record one change the person made to the draft.
   *
   * Every composable value comes through here exactly once. Applying a send or a refusal
   * does not: both sides of that transaction preserve the revision they were given.
   */
  function recordDraftChange(): void {
    compositionRevision += 1;
  }

  function currentComposerDraft(): ComposerDraft {
    return {
      text,
      pendingImages,
      pendingFiles,
      pendingVideos,
      attachmentOrder,
      pickedModel: runSelection.pickedModel,
      pickedReasoningEffort: runSelection.pickedReasoningEffort,
      selectionKind: runSelection.selectionKind,
      effortIsSet: runSelection.effortIsSet,
      compositionRevision
    };
  }

  function applyComposerDraft(draft: ComposerDraft): void {
    text = draft.text;
    attachmentOrder = [...(draft.attachmentOrder ?? [])];
    pendingImages = [...draft.pendingImages];
    pendingFiles = [...draft.pendingFiles];
    pendingVideos = [...(draft.pendingVideos ?? [])];
    rememberDraftVideo(localStorageKey, pendingVideos);
    runSelection = {
      ...runSelection,
      pickedModel: draft.pickedModel,
      pickedReasoningEffort: draft.pickedReasoningEffort,
      selectionKind: draft.selectionKind,
      effortIsSet: draft.effortIsSet
    };
    compositionRevision = draft.compositionRevision;
  }

  function applyRunSelectionIntent(intent: ComposerRunSelectionIntent): void {
    const next = applyComposerRunSelectionIntent(runControlsInput, intent);
    const changed = !sameRunSelection(runSelection, next);
    if (changed) {
      runSelection = next;
      recordDraftChange();
    }
    if (
      intent.intent === "choose_model"
      || intent.intent === "choose_reasoning_effort"
    ) {
      inputElement?.focus();
    }
  }

  async function send(): Promise<void> {
    await intakeTail;
    const trimmed = text.trim();
    if ((!trimmed && pendingImages.length === 0 && pendingFiles.length === 0 && pendingVideos.length === 0) || inputDisabled) return;
    const capturedRetryContext = restoredSendContext;
    restoredSendContext = null;
    const capturedMode = deliveryMode;
    const capturedConversationId = conversationId;
    const capturedDestination = uploadDestination ? {...uploadDestination} : undefined;
    const attempt = beginComposerSend(
      currentComposerDraft(),
      runControlsView.carriedRunValues
    );
    applyComposerDraft(attempt.draftAfterSend);
    cursorAt = 0;
    releasePendingImages(attempt.draftBeforeSend.pendingImages);
    if (imageInput) imageInput.value = "";
    intakeError = null;
    if ((attempt.draftBeforeSend.pendingVideos?.length ?? 0) > 0 || sendQueue.entries.length > 0) {
      const messageId = capturedRetryContext?.messageId ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
      const entry: PendingComposerSend = {context: capturedRetryContext ?? {messageId, expectedConversationId: capturedConversationId, destination: capturedDestination}, attempt, mode: capturedMode, phase: "waiting"};
      if (!reserveLocalSend(entry)) { await restoreRefusedAttempt(attempt); return; }
      sendQueue.enqueue(entry);
      return;
    }
    sendsInFlight += 1;
    try {
      const delivered = await onSend(
        [...attempt.content],
        capturedMode,
        attempt.carriedRunValues,
        capturedRetryContext ?? undefined
      );
      if (!delivered) await restoreRefusedAttempt(attempt);
    } finally {
      sendsInFlight -= 1;
    }
  }

  const runControlIntents: ComposerRunControlIntents = {
    chooseBackend: (backendKey) => {
      if (canonicalRows !== undefined) onPreviewRunSelection?.(runSelection, backendKey);
      else applyRunSelectionIntent({ intent: "choose_backend", backendKey });
    },
    chooseModel: (model, reasoningEffort, row) =>
      applyRunSelectionIntent({ intent: "choose_model", model, reasoningEffort, row }),
    chooseReasoningEffort: (reasoningEffort) => applyRunSelectionIntent({
      intent: "choose_reasoning_effort",
      reasoningEffort
    }),
    chooseDeliveryMode: (mode) => { deliveryMode = mode; },
    send: () => void send(),
    stop: () => onStop?.()
  };

  /** The message got nowhere, so the person is put back where they were.
   *
   * Everything that was about to go comes back together — the content and the change it
   * was carrying — because that is the state they were in when they pressed Enter.
   */
  async function restoreRefusedAttempt(attempt: ComposerSendAttempt): Promise<void> {
    const restoration = restoreRefusedComposerSend(
      currentComposerDraft(),
      attempt,
      nextImageId,
      nextFileId,
      attachmentIntakesInFlight
    );
    if (!restoration.restored) return;
    applyComposerDraft(restoration.draft);
    nextImageId = restoration.nextImageId;
    nextFileId = restoration.nextFileId;
    const restoredText = restoration.draft.text;
    const restoredRevision = restoration.draft.compositionRevision;
    await tick();
    const input = inputElement;
    if (
      input === null
      || text !== restoredText
      || compositionRevision !== restoredRevision
    ) return;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
    cursorAt = input.value.length;
  }

  function intakeFiles(
    files: Iterable<File> | ArrayLike<File> | null | undefined
  ): Promise<void> {
    if (disabled || takenOver) return Promise.resolve();
    const chosen: File[] = [];
    for (const file of Array.from(files ?? [])) {
      if (!isVideoFile(file.name)) { chosen.push(file); continue; }
      if (!uploadDestination) { intakeError = "Video upload is unavailable for this conversation."; continue; }
      if (pendingVideos[0]?.phase === "interrupted" || (pendingVideos[0]?.phase === "failed" && !pendingVideos[0]?.file)) removeVideo(pendingVideos[0]);
      if (pendingVideos.length) { intakeError = "Attach one video per message."; continue; }
      try {
        const video = transfers.create(file, nextVideoId++, uploadDestination);
        pendingVideos = [video];
        attachmentOrder = [...attachmentOrder, {kind: "video", id: video.id}];
        recordDraftChange();
        void transfers.start(video);
      } catch (error) { intakeError = error instanceof Error ? error.message : "The video could not be attached."; }
    }
    if (!chosen.length) {
      if (fileInput) fileInput.value = "";
      if (imageInput) imageInput.value = "";
      return Promise.resolve();
    }
    intakeTail = intakeTail.then(() => intakeChosenFiles(chosen));
    return intakeTail;
  }

  async function intakeChosenFiles(files: readonly File[]): Promise<void> {
    attachmentIntakesInFlight += 1;
    try {
      const acceptedImages: PendingConversationImage[] = [];
      const acceptedFiles: PendingConversationFile[] = [];
      const acceptedOrder: ComposerAttachmentReference[] = [];
      let rejected = false;
      let published = false;
      // Intake follows the FileList, rather than grouping it by kind. Both adapters still
      // own their validation and aggregate byte limits.
      try {
        for (const file of files) {
          if (file.type.toLowerCase().startsWith("image/")) {
            const intake = await createPendingConversationImages(
              [file], nextImageId, pendingConversationImageBytes([...pendingImages, ...acceptedImages])
            );
            nextImageId = intake.nextId;
            rejected ||= intake.rejected.length > 0;
            acceptedImages.push(...intake.accepted);
            acceptedOrder.push(...intake.accepted.map((image) => ({ kind: "image" as const, id: image.id })));
          } else {
            const intake = await createPendingConversationFiles(
              [file], nextFileId, pendingConversationFileBytes([...pendingFiles, ...acceptedFiles])
            );
            nextFileId = intake.nextId;
            rejected ||= intake.rejected.length > 0;
            acceptedFiles.push(...intake.accepted);
            acceptedOrder.push(...intake.accepted.map((document) => ({ kind: "file" as const, id: document.id })));
          }
        }
        if (destroyed) return;
        if (acceptedOrder.length > 0) {
          recordDraftChange();
          pendingImages = [...pendingImages, ...acceptedImages];
          pendingFiles = [...pendingFiles, ...acceptedFiles];
          attachmentOrder = [...attachmentOrder, ...acceptedOrder];
        }
        published = true;
        intakeError = rejected
          ? "Choose PNG, JPEG, GIF or WebP images up to 3 MiB, or PDF, text, Markdown, CSV, TSV, JSON or JSONL files up to 10 MiB."
          : null;
      } finally {
        // Until published these URLs belong to this intake, including a later read failure.
        if (!published) {
          releasePendingImages(acceptedImages);
        }
      }
    } catch (error) {
      if (!destroyed) {
        intakeError = error instanceof Error ? error.message : "The attachment could not be read.";
      }
    } finally {
      if (!destroyed) {
        attachmentIntakesInFlight -= 1;
        if (imageInput) imageInput.value = "";
        if (fileInput) fileInput.value = "";
        // A camera hands back the same filename every time, so an unreset input is a
        // second capture that changes nothing and never fires.
        if (cameraInput) cameraInput.value = "";
      }
    }
  }

  function removeVideo(video: PendingVideo): void {
    transfers.remove(video);
    pendingVideos = pendingVideos.filter(item => item !== video);
    attachmentOrder = attachmentOrder.filter(item => item.kind !== "video" || item.id !== video.id);
    recordDraftChange(); videoChanged();
  }
  function reserveLocalSend(entry: PendingComposerSend): boolean {
    const message = {...mintOutgoingMessage({content: [...entry.attempt.content], senderLabel: "owner", mode: entry.mode}), messageId: entry.context.messageId};
    if (!reserveOutgoingMessageImages(message)) { intakeError = "Wait for an outstanding image message to reach the conversation."; return false; }
    if (!reserveOutgoingMessageFiles(message)) { releaseOutgoingMessageImages(message.messageId); intakeError = "Wait for an outstanding file message to reach the conversation."; return false; }
    return true;
  }
  async function recoverLocalSend(entry: PendingComposerSend, removeVideo = false): Promise<void> {
    if (entry.phase === "dispatching") return;
    if (entry.needsReconciliation) {
      try {
        if (await reconcileLocalSend(entry.context.messageId)) { sendQueue.remove(entry.context.messageId); return; }
        entry.needsReconciliation = false;
      } catch { entry.error = "The earlier send could not be checked. Wait for the conversation to reconnect before returning it to the composer."; videoChanged(); return; }
    }
    entry.phase = "interrupted"; // Explicitly removes the automatic-send intent.
    if (removeVideo) {
      for (const video of entry.attempt.draftBeforeSend.pendingVideos ?? []) transfers.remove(video);
      entry.attempt = {...entry.attempt,
        content: entry.attempt.content.filter(piece => piece.piece !== "video_upload"),
        draftBeforeSend: {...entry.attempt.draftBeforeSend, pendingVideos: [], attachmentOrder: entry.attempt.draftBeforeSend.attachmentOrder?.filter(item => item.kind !== "video")}};
    }
    const restoration = restoreRefusedComposerSend(currentComposerDraft(), entry.attempt, nextImageId, nextFileId, attachmentIntakesInFlight);
    // An earlier interrupted draft may be returned once the newer draft is empty.
    const empty = text === "" && !pendingImages.length && !pendingFiles.length && !pendingVideos.length;
    const result = restoration.restored ? restoration : empty
      ? restoreRefusedComposerSend({...entry.attempt.draftAfterSend}, entry.attempt, nextImageId, nextFileId, attachmentIntakesInFlight) : restoration;
    if (result.restored) {
      for (const video of result.draft.pendingVideos ?? []) {
        if (video.uploadId && video.phase === "ready") { video.phase = "validating"; void transfers.validate(video); }
      }
      restoredSendContext = {...entry.context};
      applyComposerDraft(result.draft); nextImageId = result.nextImageId; nextFileId = result.nextFileId;
      sendQueue.remove(entry.context.messageId);
      releaseOutgoingMessageImages(entry.context.messageId); releaseOutgoingMessageFiles(entry.context.messageId);
      void tick().then(() => inputElement?.focus());
    } else entry.error = "Your newer draft is kept. Finish it, then return this message to the composer.";
    videoChanged();
  }
  function videoProgress(video: PendingVideo): string {
    return video.phase === "uploading" ? `Uploading ${Math.floor(video.receivedBytes / video.byteCount * 100)}%`
      : video.phase === "validating" ? "Checking video…" : video.phase === "ready" ? "Ready"
      : video.error || "Select the video again.";
  }

  function removeImage(image: PendingConversationImage): void {
    recordDraftChange();
    pendingImages = pendingImages.filter((candidate) => candidate.id !== image.id);
    attachmentOrder = attachmentOrder.filter((reference) => reference.kind !== "image" || reference.id !== image.id);
    releasePendingImages([image]);
  }

  function removeFile(file: PendingConversationFile): void {
    recordDraftChange();
    pendingFiles = pendingFiles.filter((candidate) => candidate.id !== file.id);
    attachmentOrder = attachmentOrder.filter((reference) => reference.kind !== "file" || reference.id !== file.id);
  }

  function hasAttachmentTransfer(event: DragEvent): boolean {
    return Array.from(event.dataTransfer?.items ?? []).some((item) =>
      item.kind === "file"
    );
  }

  function onDragEnter(event: DragEvent): void {
    if (inputDisabled || !hasAttachmentTransfer(event)) return;
    event.preventDefault();
    dragDepth += 1;
    draggingAttachments = true;
  }

  function onDragOver(event: DragEvent): void {
    if (inputDisabled || !hasAttachmentTransfer(event)) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    draggingAttachments = true;
  }

  function onDragLeave(): void {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) draggingAttachments = false;
  }

  function onDrop(event: DragEvent): void {
    if (inputDisabled) return;
    event.preventDefault();
    dragDepth = 0;
    draggingAttachments = false;
    void intakeFiles(event.dataTransfer?.files);
  }

  function onPaste(event: ClipboardEvent): void {
    const files = event.clipboardData?.files;
    if (inputDisabled || !files || files.length === 0) return;
    event.preventDefault();
    void intakeFiles(files);
  }

  function onKeydown(event: KeyboardEvent): void {
    if (catalogMenuIsOpen) {
      if (event.key === "Escape") {
        event.preventDefault();
        menuWasDismissed = true;
        return;
      }
      // Only when there is something to move between or take. A menu that is open saying
      // there is nothing to offer is a sentence, not a list, and the keys stay the box's.
      if (matchingCatalogEntries.length > 0) {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          const step = event.key === "ArrowDown" ? 1 : matchingCatalogEntries.length - 1;
          activeCatalogEntryIndex =
            (activeCatalogEntryIndex + step) % matchingCatalogEntries.length;
          return;
        }
        const highlighted = matchingCatalogEntries[activeCatalogEntryIndex];
        if ((event.key === "Enter" || event.key === "Tab") && highlighted !== undefined) {
          event.preventDefault();
          void takeTheCatalogEntry(highlighted);
          return;
        }
      }
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send();
    }
  }

  /** Where the cursor is now, after the person typed or clicked.
   *
   * Read from the box rather than worked out, because every key that moves a cursor moves
   * it in its own way.
   */
  function readWhereTheCursorIs(): void {
    const input = inputElement;
    if (input === null) return;
    theCursorIsInTheBox = true;
    cursorAt = input.selectionStart ?? 0;
  }

  function textChanged(): void {
    recordDraftChange();
    readWhereTheCursorIs();
  }

  function catalogEntriesForTrigger(
    entries: readonly ComposerCatalogEntry[],
    trigger: "/" | "$" | "@" | null
  ): ComposerCatalogEntry[] {
    const kinds: readonly ComposerCatalogEntryKind[] = trigger === "/"
      ? ["command"]
      : trigger === "$"
        ? ["skill"]
        : trigger === "@"
          ? ["app", "plugin"]
          : [];
    return entries.filter((entry) => kinds.includes(entry.kind));
  }

  /** Prefix matches come before contains matches, alphabetically within each group. */
  function catalogEntriesMatching(
    entries: readonly ComposerCatalogEntry[],
    typed: string,
    trigger: "/" | "$" | "@" | null
  ): ComposerCatalogEntry[] {
    const wanted = typed.toLowerCase();
    const searchable = (entry: ComposerCatalogEntry): string => {
      const displayText = entry.display_text.toLowerCase();
      return trigger !== null && displayText.startsWith(trigger)
        ? displayText.slice(trigger.length)
        : displayText;
    };
    const byDisplayText = (one: ComposerCatalogEntry, other: ComposerCatalogEntry): number =>
      one.display_text.localeCompare(other.display_text);
    const startsWithIt = entries.filter((entry) =>
      searchable(entry).startsWith(wanted)
    );
    const containsIt = entries.filter(
      (entry) => !searchable(entry).startsWith(wanted) && searchable(entry).includes(wanted)
    );
    return [...startsWithIt.sort(byDisplayText), ...containsIt.sort(byDisplayText)];
  }

  /** Replace the active token with the entry's exact insertion text. */
  async function takeTheCatalogEntry(entry: ComposerCatalogEntry): Promise<void> {
    const underway = catalogTokenUnderway;
    if (underway === null) return;
    const written = entry.insertion_text;
    recordDraftChange();
    const rest = text.slice(underway.end);
    const preservedRest = written.endsWith(" ") && rest.startsWith(" ") ? rest.slice(1) : rest;
    text = text.slice(0, underway.start) + written + preservedRest;
    menuWasDismissed = true;
    const cursorGoes = underway.start + written.length;
    await tick();
    const input = inputElement;
    if (input === null) return;
    input.focus();
    input.setSelectionRange(cursorGoes, cursorGoes);
    theCursorIsInTheBox = true;
    cursorAt = cursorGoes;
  }

  /** Start writing a command at the front of this message.
   *
   * A command is written by starting the message with a slash, so this puts one there and
   * hands the box straight back with the cursor just after it — which is where the name
   * goes, and is exactly where a person who typed the slash themselves would be. So the
   * menu opens by the one rule that opens it, and what command, and everything after it,
   * is still theirs to type.
   */
  async function startWritingACommand(): Promise<void> {
    recordDraftChange();
    if (!text.startsWith("/")) text = `/${text}`;
    menuWasDismissed = false;
    await tick();
    const input = inputElement;
    if (input === null) return;
    input.focus();
    input.setSelectionRange(1, 1);
    theCursorIsInTheBox = true;
    cursorAt = 1;
  }

  /** A transcription landed. The words join the draft the way typing them would have, and
   *  the composer settles as the normal one with the cursor at the end — never back to the
   *  voice-first face over text that was just spoken. An empty transcript is a no-op. */
  async function landTranscript(transcript: string): Promise<void> {
    if (transcript === "") return;
    recordDraftChange();
    const settled = text.trim();
    text = settled === "" ? transcript : `${settled}\n\n${transcript}`;
    await tick();
    const input = inputElement;
    if (input === null) return;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
    cursorAt = input.value.length;
  }

  async function preferKeyboard(): Promise<void> {
    keyboardPreferred = true;
    try {
      window.sessionStorage.setItem(KEYBOARD_PREFERRED, "1");
    } catch {
      // A browser that keeps nothing for the tab still switches faces for now.
    }
    await tick();
    inputElement?.focus();
  }

  // A question panel or permission ask arriving mid-voice takes the composer: whatever
  // voice was doing is cancelled rather than left running under a surface it cannot show.
  $effect(() => {
    if (takenOver && voiceState.phase !== "idle") voice?.cancel();
  });

  let textareaMeasurement: ReturnType<typeof createComposerTextareaMeasurement> | null = null;

  function resizeWritingArea(): void {
    const input = inputElement;
    if (input === null || input.hidden || input.getClientRects().length === 0) return;
    const style = getComputedStyle(input);
    const lineHeight = Number.parseFloat(style.lineHeight);
    if (!Number.isFinite(lineHeight) || lineHeight <= 0) return;
    if (textareaMeasurement?.input !== input) {
      textareaMeasurement?.dispose();
      textareaMeasurement = createComposerTextareaMeasurement(input);
    }
    const size = textareaMeasurement.measure(style);
    const height = `${size.height}px`;
    const host = input.closest<HTMLElement>(".ticket-page, .manager-page") ?? input.closest<HTMLElement>("[data-conversation-pane]");
    host?.style.setProperty("--composer-growth", `${size.growth}px`);
    if (input.style.height !== height) input.style.height = height;
    if (input.style.overflowY !== size.overflowY) input.style.overflowY = size.overflowY;
  }

  // Effects run after Svelte has written values and hidden-state changes into the DOM.
  $effect(() => {
    text;
    userInput;
    voiceState.phase;
    voiceFirstIdle;
    resizeWritingArea();
  });

  $effect(() => {
    const input = inputElement;
    if (input === null) return;
    let measuredWidth = -1;
    let resizeFrame = 0;
    const observer = new ResizeObserver(() => {
      const width = input.clientWidth;
      // The textarea's own height is an output, never another reason to resize it.
      if (width === measuredWidth) return;
      measuredWidth = width;
      // Write on the next frame, outside observer delivery. Even one intentional height
      // change to an observed input can otherwise report undelivered observations.
      cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(resizeWritingArea);
    });
    observer.observe(input);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(resizeFrame);
      if (textareaMeasurement?.input === input) {
        textareaMeasurement.dispose();
        textareaMeasurement = null;
      }
    };
  });

  onMount(() => {
    void restoreLocalState();
    coarsePointer = window.matchMedia("(pointer: coarse)").matches;
    voiceSupported = voiceCaptureSupported();
    try {
      keyboardPreferred = window.sessionStorage.getItem(KEYBOARD_PREFERRED) === "1";
    } catch {
      keyboardPreferred = false;
    }
    voice = createVoiceCapture({
      onState: (state) => (voiceState = state),
      onTranscript: (transcript) => void landTranscript(transcript)
    });
    return () => {
      destroyed = true;
      transfers.dispose();
      sendQueue.dispose();
      publishCompositionState(false);
      releasePendingImages(pendingImages);
      voice?.dispose();
      voice = null;
    };
  });
</script>

<svelte:window
  onpointerdown={(event) => {
    if (!addMenuOpen) return;
    const pressed = event.target;
    if (pressed instanceof Node && addMenuElement?.contains(pressed)) return;
    addMenuOpen = false;
  }}
/>

<section
  class="c2-composer"
  data-conversation-composer
  data-conversation-composition-active={compositionActive ? "true" : undefined}
>
  <div class="chat-box-stack">
    {#each localSends as entry (entry.context.messageId)}
      <article class="chat-local-send" data-local-video-send={entry.context.messageId} aria-label="Local pending message">
        <p>{entry.attempt.draftBeforeSend.text}</p>
        {#each entry.attempt.draftBeforeSend.pendingVideos ?? [] as video}
          <div>{video.fileName} · <span role="status">{videoProgress(video)}</span></div>
          {#if video.phase === "failed" && video.file && entry.phase === "waiting"}
            <button type="button" onclick={() => void transfers.start(video)}>Retry</button>
          {/if}
        {/each}
        <div role="status">{entry.error || (entry.phase === "dispatching" ? "Sending…" : entry.phase === "interrupted" ? "Kept in this browser · review before sending" : "Pending in this browser · not sent yet")}</div>
        {#if entry.phase !== "dispatching"}
          {#if entry.phase === "failed" && !entry.needsReconciliation}
            <button type="button" onclick={() => {entry.phase = "waiting"; entry.error = undefined; videoChanged();}}>Retry send</button>
          {/if}
          <button type="button" onclick={() => recoverLocalSend(entry)}>{entry.phase === "interrupted" ? "Return to composer" : "Cancel send"}</button>
          {#if entry.attempt.draftBeforeSend.pendingVideos?.length}
            <button type="button" onclick={() => recoverLocalSend(entry, true)}>Remove video</button>
          {/if}
        {/if}
      </article>
    {/each}
    <HeldPromptStack
      rows={heldPromptRows}
      {conversationId}
      {supportsSteer}
      {running}
      onDiscard={onDiscardHeldPrompt}
      onPromote={onPromoteHeldPrompt}
      onStopDrawing={onStopDrawingHeldPrompt}
      onSendAgain={onSendHeldPromptAgain}
    />

    <div
      class="chat-box"
      class:drag={draggingAttachments}
      class:has-ask={takenOver}
      data-conversation-box
      data-conversation-taken-over={takenOver ? "true" : undefined}
      role="group"
      aria-label="Conversation composer"
      ondragenter={onDragEnter}
      ondragover={onDragOver}
      ondragleave={onDragLeave}
      ondrop={onDrop}
    >
      {#if userInput}
        <UserInputQuestionPanel
          request={userInput}
          busy={disabled}
          answerAvailable={userInputAvailable}
          note={userInputNote}
          onSubmit={(answers) => onSubmitUserInput?.(answers)}
          onCancelTurn={() => onCancelTurn?.()}
        />
      {:else if takenOver && ask}
        <PermissionAskCard
          ask={ask}
          answerAvailable={permissionAvailable}
          busy={disabled}
          note={askNote}
          onAnswer={(optionId) => onAnswer?.(optionId)}
        />
      {/if}

      {#if catalogMenuIsOpen}
        <ComposerCatalogMenu
          entries={matchingCatalogEntries}
          activeIndex={activeCatalogEntryIndex}
          anyEntriesAtAll={eligibleCatalogEntries.length > 0}
          onChoose={(entry) => void takeTheCatalogEntry(entry)}
          onHighlight={(index) => (activeCatalogEntryIndex = index)}
          pointerReallyMoved={() => pointerReallyMoved}
        />
      {/if}

      {#if userInput === null && attachments.length > 0}
        <div class="chat-attachments" data-conversation-attachments aria-label="Attached and waiting to send">
          {#each attachments as attachment (`${attachment.kind}-${attachment.value.id}`)}
            {@const item = attachment.value}
            <div
              class="chat-att"
              class:img={attachment.kind === "image"}
              class:file={attachment.kind === "file"}
              data-chat-image-preview={attachment.kind === "image" ? "" : undefined}
              data-chat-image-name={attachment.kind === "image" ? item.fileName : undefined}
              data-chat-file-preview={attachment.kind === "file" ? "" : undefined}
              data-chat-file-name={attachment.kind === "file" ? item.fileName : undefined}
              title={`${item.fileName || "pasted image"} · ${item.mediaType} · ${attachmentByteLabel(item.byteCount)}`}
            >
              {#if attachment.kind === "image"}
                <img src={attachment.value.previewUrl} alt="" />
              {:else if attachment.kind === "video"}
                <span class="chat-att-kind" aria-hidden="true">VIDEO</span>
                <span class="chat-att-words"><span class="chat-att-name">{item.fileName}</span>
                  <span class="chat-att-meta" role="status">{videoProgress(attachment.value)}</span>
                </span>
                {#if attachment.value.phase === "interrupted" || (attachment.value.phase === "failed" && !attachment.value.file)}
                  <button type="button" onclick={() => fileInput?.click()}>Choose video again</button>
                {/if}
                {#if attachment.value.phase === "failed" && attachment.value.file}
                  <button type="button" onclick={() => void transfers.start(attachment.value)}>Retry</button>
                {/if}
              {:else}
                <span class="chat-att-kind" aria-hidden="true">{attachmentExtension(item.fileName)}</span>
                <span class="chat-att-words">
                  <span class="chat-att-name">{item.fileName}</span>
                  <span class="chat-att-meta">{item.mediaType} · {attachmentByteLabel(item.byteCount)}</span>
                </span>
              {/if}
              <button
                type="button"
                class="chat-att-x"
                data-chat-image-remove={attachment.kind === "image" ? "" : undefined}
                data-chat-file-remove={attachment.kind === "file" ? "" : undefined}
                aria-label={`Remove ${attachment.kind} ${item.fileName || "pasted image"}`}
                title="Remove"
                disabled={inputDisabled}
                onclick={() => attachment.kind === "image" ? removeImage(attachment.value) : attachment.kind === "video" ? removeVideo(attachment.value) : removeFile(attachment.value)}
              >×</button>
            </div>
          {/each}
        </div>
      {/if}

      {#if userInput === null && voiceState.phase !== "idle"}
        <!-- Every voice state keeps the composer's shape: this centred line is the main
             area, and the actions stay down in the foot bar. -->
        <div class="chat-voice-mid" data-conversation-voice={voiceState.phase}>
          {#if voiceState.phase === "recording"}
            <span class="live-text-shimmer">recording</span>
            <span class="chat-voice-time">{formatVoiceTime(voiceState.elapsedMs)}</span>
          {:else if voiceState.phase === "transcribing"}
            <span class="live-text-shimmer">transcribing</span>
          {:else}
            <span class="chat-voice-fail">
              transcription failed · kept
              <span class="chat-voice-time">{formatVoiceTime(voiceState.keptMs)}</span>
            </span>
          {/if}
        </div>
      {:else if userInput === null && voiceFirstIdle}
        <button
          type="button"
          class="chat-voice-idle"
          data-conversation-voice="idle"
          data-voice-record
          aria-label="Tap to speak"
          disabled={inputDisabled}
          onclick={() => void voice?.startRecording()}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <rect x="9" y="3" width="6" height="11" rx="3" />
            <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
          </svg>
          <span>tap to speak</span>
        </button>
      {/if}

      <textarea
        class="chat-ta"
        data-conversation-input
        rows="1"
        placeholder={livePlaceholder}
        bind:this={inputElement}
        bind:value={text}
        disabled={inputDisabled}
        hidden={userInput !== null || voiceState.phase !== "idle" || voiceFirstIdle}
        onkeydown={onKeydown}
        oninput={textChanged}
        onkeyup={readWhereTheCursorIs}
        onclick={readWhereTheCursorIs}
        onfocus={readWhereTheCursorIs}
        onblur={() => (theCursorIsInTheBox = false)}
        onpaste={onPaste}
      ></textarea>

      {#if userInput === null}
      <div class="chat-foot" class:has-ask={takenOver}>
        {#if takenOver && ask}
          <PermissionAskActions
            ask={ask}
            answerAvailable={permissionAvailable}
            busy={disabled}
            onAnswer={(optionId) => onAnswer?.(optionId)}
            onCancelTurn={() => onCancelTurn?.()}
          />
        {:else if voiceState.phase !== "idle"}
          <!-- The X on the left undoes whatever voice is doing; the right-hand slot is the
               one going-forward action of the state: stop, or retry. -->
          <button
            type="button"
            class="chat-voice-cancel"
            data-voice-cancel
            aria-label={voiceState.phase === "recording"
              ? "Cancel recording"
              : voiceState.phase === "transcribing"
                ? "Cancel transcription"
                : "Discard recording"}
            onclick={() => voice?.cancel()}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5l14 14M19 5L5 19" /></svg>
          </button>
          {#if voiceState.phase === "recording"}
            <button
              type="button"
              class="chat-voice-stop"
              data-voice-stop
              aria-label="Stop recording and transcribe"
              onclick={() => voice?.stopRecording()}
            ><span class="chat-voice-square"></span></button>
          {:else if voiceState.phase === "failed"}
            <button
              type="button"
              class="chat-voice-retry"
              data-voice-retry
              onclick={() => voice?.retry()}
            >retry</button>
          {/if}
        {:else if voiceFirstIdle}
          <button
            type="button"
            class="chat-image chat-voice-kb"
            data-voice-keyboard
            aria-label="Type instead"
            title="Type instead"
            onclick={() => void preferKeyboard()}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <rect x="2.5" y="6" width="19" height="12" rx="2" />
              <path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M6 14h.01M18 14h.01M9 14h6" />
            </svg>
          </button>
        {:else}
          <!-- One place to add something, rather than a row of separate buttons. What it
               offers is still the same set of things, and each of them still does exactly
               what its own button did. -->
          <div class="chat-add" bind:this={addMenuElement}>
            <button
              type="button"
              class="chat-image chat-add-btn"
              class:on={pendingImages.length + pendingFiles.length + pendingVideos.length > 0}
              data-conversation-add
              aria-haspopup="menu"
              aria-expanded={addMenuOpen}
              aria-label="Add a picture, a file, a photo, or a command"
              title="Add a picture, a file, a photo, or a command"
              disabled={inputDisabled}
              onclick={() => (addMenuOpen = !addMenuOpen)}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 5v14M5 12h14" />
              </svg>
            </button>
            {#if addMenuOpen}
              <div class="chat-add-menu" role="menu">
                <button
                  type="button"
                  class="chat-add-item"
                  role="menuitem"
                  disabled={inputDisabled}
                  data-conversation-image
                  data-conversation-image-count={pendingImages.length || undefined}
                  onclick={() => { addMenuOpen = false; imageInput?.click(); }}
                >
                  <svg viewBox="0 0 16 16" aria-hidden="true">
                    <path d="M2.5 3.5h11v9h-11zM4 10l2.5-2.5 2 2 1.5-1.5 2 2M10.5 6h.01" />
                  </svg>
                  <span>Picture</span>
                  {#if pendingImages.length > 0}<span class="chat-add-count">{pendingImages.length}</span>{/if}
                </button>
                <button
                  type="button"
                  class="chat-add-item"
                  role="menuitem"
                  disabled={inputDisabled}
                  data-conversation-file
                  data-conversation-file-count={pendingFiles.length || undefined}
                  onclick={() => { addMenuOpen = false; fileInput?.click(); }}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M8 12.5l6.8-6.8a3 3 0 0 1 4.2 4.2l-8.2 8.2a5 5 0 0 1-7.1-7.1l8-8" />
                  </svg>
                  <span>File</span>
                  {#if pendingFiles.length > 0}<span class="chat-add-count">{pendingFiles.length}</span>{/if}
                </button>
                <button
                  type="button"
                  class="chat-add-item"
                  role="menuitem"
                  disabled={inputDisabled}
                  data-conversation-camera
                  onclick={() => { addMenuOpen = false; cameraInput?.click(); }}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M3 8.5h4l1.5-2h7L17 8.5h4v10H3zM12 16a3 3 0 1 0 0-6 3 3 0 0 0 0 6z" />
                  </svg>
                  <span>Take a picture</span>
                </button>
                <!-- The cursor stays in the box: everything this does is done to what is
                     being written there. -->
                <button
                  type="button"
                  class="chat-add-item"
                  role="menuitem"
                  disabled={inputDisabled}
                  data-conversation-slash
                  onmousedown={(event) => event.preventDefault()}
                  onclick={() => { addMenuOpen = false; void startWritingACommand(); }}
                >
                  <span class="chat-add-glyph" aria-hidden="true">/</span>
                  <span>Write a command</span>
                </button>
              </div>
            {/if}
          </div>
          <input
            bind:this={cameraInput}
            class="chat-image-input"
            data-conversation-camera-input
            type="file"
            accept="image/*"
            capture="environment"
            onchange={() => void intakeFiles(cameraInput?.files)}
          />
          <input
            bind:this={imageInput}
            class="chat-image-input"
            data-conversation-image-input
            type="file"
            accept="image/*"
            multiple
            onchange={() => void intakeFiles(imageInput?.files)}
          />
          <input
            bind:this={fileInput}
            class="chat-image-input"
            data-conversation-file-input
            type="file"
            accept={`${CONVERSATION_FILE_ACCEPT},${VIDEO_ACCEPT}`}
            multiple
            onchange={() => void intakeFiles(fileInput?.files)}
          />

          {#snippet microphone()}
            {#if voiceAvailable}
              <!-- Recording from a composer with words already in it appends to them. -->
              <button
                type="button"
                class="chat-image chat-voice-mic"
                data-voice-record
                aria-label="Record a voice message"
                title="Record a voice message"
                disabled={inputDisabled}
                onclick={() => void voice?.startRecording()}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <rect x="9" y="3" width="6" height="11" rx="3" />
                  <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
                </svg>
              </button>
            {/if}
          {/snippet}
          {#if showRunPicker}
            <ComposerRunControls
              view={runControlsView}
              onRetry={onRetryModelChoices}
              intents={runControlIntents}
              bind:snapshots={backends}
              beforeSubmit={microphone}
            />
          {:else}
            <span class="chat-submit">{@render microphone()}</span>
          {/if}
        {/if}
      </div>
      {/if}
      <div class="chat-drop-label" data-conversation-drop-label aria-hidden="true">
        Drop images or files to attach
      </div>
    </div>
  </div>

  {#if intakeError || errorNote}
    <div class="chat-receipt" role="alert" data-conversation-error>{intakeError ?? errorNote}</div>
  {:else if fateNote}
    <div class="c2-fate" data-conversation-fate>{fateNote}</div>
  {/if}
</section>

<style>
  /* One affordance where three used to be, at the size a finger can find. */
  .chat-add { position: relative; display: inline-flex; }
  .chat-add-menu {
    position: absolute;
    inset-block-end: calc(100% + var(--space-2));
    inset-inline-start: 0;
    z-index: 5;
    min-width: 224px;
    display: flex;
    flex-direction: column;
    gap: 1px;
    padding: var(--space-1);
    background: var(--surface-2);
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-md);
    box-shadow: 0 var(--space-2) var(--space-5) rgb(10 9 8 / 45%);
  }
  .chat-add-item {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    width: 100%;
    min-height: 32px;
    padding: var(--space-2) var(--space-3);
    background: none;
    border: none;
    border-radius: var(--radius-sm);
    color: var(--text-muted);
    font-family: var(--font-ui);
    font-size: var(--type-md);
    text-align: left;
    cursor: pointer;
  }
  .chat-add-item:hover,
  .chat-add-item:focus-visible { color: var(--text-strong); background: var(--surface-1); }
  .chat-add-item svg {
    width: 20px;
    height: 20px;
    flex: none;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.35;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .chat-add-glyph {
    width: 20px;
    flex: none;
    text-align: center;
    font-family: var(--font-mono);
    color: var(--text-faint);
  }
  .chat-add-count {
    margin-inline-start: auto;
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    color: var(--accent-bright);
  }

  .c2-composer { display: grid; gap: var(--space-2); }
  .chat-local-send { padding: var(--space-3); border: var(--border-hairline) solid var(--border-color); border-radius: var(--radius-sm); background: var(--surface-recessed); color: var(--text-muted); font-size: var(--type-sm); }
  .chat-local-send p { white-space: pre-wrap; margin: 0 0 var(--space-2); color: var(--text-strong); }
  .chat-local-send button { margin-top: var(--space-2); margin-right: var(--space-2); }

  /* An ask is a band on top of the box, not a replacement for it: the input keeps its
     resting height underneath, so the composer stays exactly the size it always was. */
  :global(.chat-box.has-ask) { padding-top: 0; }
  /* A message on its way out. The box is already empty and already typeable, so the arrow
     staying lit is the one thing on screen that says the send has not landed yet. */
  :global(.chat-send.is-sending:disabled) {
    background: var(--surface-2);
    color: var(--accent-bright);
    opacity: 1;
  }
  .c2-fate {
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
  }
</style>

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
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../../lib/queryCatalogue";
  import { videoUploadAdapter } from "../../lib/conversation/videoUploads/adapter";
  import { mintOutgoingMessage, reserveOutgoingMessageImages, reserveOutgoingMessageFiles, releaseOutgoingMessageImages, releaseOutgoingMessageFiles } from "../../lib/conversation/outgoing";
  import { VideoTransfers } from "../../lib/conversation/videoUploads/uploads";
  import { isVideoFile, VIDEO_ACCEPT } from "../../lib/conversation/videoUploads/config";
  import type { PendingVideo, UploadDestination, ComposerSendContext } from "../../lib/conversation/videoUploads/contracts";
  import { LocalComposerSends, releaseRemovedLocalSends, recallLocalSends, rememberLocalSends, recallDraftVideo, rememberDraftVideo, type PendingComposerSend } from "../../lib/conversation/videoUploads/localSends";

  import ComposerCatalogMenu from "./ComposerCatalogMenu.svelte";
  import {
    orderedComposerAttachments, attachmentByteLabel, attachmentKindLabel, attachmentDisplayName,
    type ComposerAttachmentReference
  } from "./composer/attachments";
  import { createComposerTextareaMeasurement } from "./composer/textareaMeasurement";
  import ComposerRunControls from "./composer/ComposerRunControls.svelte";
  import PermissionAskCard from "./PermissionAskCard.svelte";
  import UserInputQuestionPanel from "./UserInputQuestionPanel.svelte";
  import ConversationCredentialRequestCard from "./ConversationCredentialRequestCard.svelte";
  import type { CredentialRequestRowBody } from "../../lib/conversation/view/contracts";
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
    composerPlaceholderFor,
    resolveComposerRunControls
  } from "../../lib/conversation/runControls/logic/runSelection";
  import type {
    ComposerRunControlIntents,
    ComposerRunControlsInput,
    ComposerRunSelection,
    ComposerRunSelectionIntent
  } from "../../lib/conversation/runControls/contracts";
  import type { RunValues } from "../../lib/conversation/composer";
  import { askShape } from "../../lib/conversation/composer";
  import { catalogTokenAtMessageStart } from "../../lib/conversation/composerCatalog";
  import type { HeldPromptRow } from "../../lib/conversation/heldPrompts";
  import {
    createVoiceCapture,
    formatVoiceTime,
    voiceCaptureAvailable,
    voiceCaptureSupported,
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
    credentialRequest = null,
    askNote = null,
    userInputNote = null,
    permissionAvailable = true,
    userInputAvailable = true,
    current = { model: null, reasoningEffort: null },
    readError = null,
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
    onSendHeldPromptAgain,
    onFieldChosen
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
    /** A key the agent asked for and the person has not yet given or refused. It is asked
     *  where the other asks are, in the composer's place, and the box gives way to it. */
    credentialRequest?: CredentialRequestRowBody | null;
    askNote?: string | null;
    userInputNote?: string | null;
    permissionAvailable?: boolean;
    userInputAvailable?: boolean;
    current?: RunValues;
    /** What this conversation's own backend offers. */
    readError?: string | null;
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
    /** The person put the cursor in the field: focused it, or pressed it or the box around
     *  it. Focus the composer hands back after one of its own controls is not this. */
    onFieldChosen?: () => void;
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
  let providerSelections: Partial<Record<ConversationBackendKey, ComposerRunSelection>> = {};
  let runSelectionDraftKey = untrack(() => draftKey);
  $effect(() => {
    const key = draftKey;
    if (key === untrack(() => runSelectionDraftKey)) return;
    runSelectionDraftKey = key;
    providerSelections = {};
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
  let voiceSupported = $state(false);
  let voiceState = $state<VoiceCaptureState>({ phase: "idle" });
  let voice: VoiceCapture | null = null;

  let takenOver = $derived(ask !== null || userInput !== null || credentialRequest !== null);
  /** A pending key request is drawn in the box only when no question or permission ask
   *  outranks it; those are answered first, and the request waits its turn. */
  let credentialInTheBox = $derived(userInput === null && ask === null && credentialRequest !== null);
  /** The asks that bring their own actions, so the box draws none of its own under them. */
  // Every ask draws its own answers in its shell's footer, so the box's own foot gives way to it.
  let panelTakesTheBox = $derived(userInput !== null || ask !== null || credentialInTheBox);
  // v6: no waiting line above an ask: the ask card's own label is the one waiting label.
  /** The question on screen, when it takes an own answer: the composer is then the one place
   *  to write it, right under the question, and its send answers. */
  let questionPanel = $state<{ answerWith: (words: string) => boolean; commit: () => void } | null>(null);
  /** v10 (4): asks have no confirm pill: each option row is the action. The free-text well
   *  keeps its own round send. Only a question that takes several answers waits for that
   *  send to go on once something is chosen (questionCommitReady). */
  let questionCommitReady = $state(false);
  let questionTakesOwnAnswer = $state(false);
  /** What a waiting ask makes of the draft (runControls contracts). Words alone answer a
   *  question that takes an own answer; anything else waits for the blocked turn, or ends it. */
  let askWaiting = $derived<"answers" | "holds" | null>(
    !panelTakesTheBox
      ? null
      : userInput !== null && questionTakesOwnAnswer && attachments.length === 0
        ? "answers"
        : "holds"
  );
  /** One line until there is more to hold than one line: the add button, the draft, the run
   *  chips, the microphone and send/stop share a single row. Words that wrap, a line break,
   *  an attachment or a voice take grow it into its two rows; it goes back to one line only
   *  once the draft is empty again, so it never flickers between the two while typing. */
  let draftWraps = $state(false);
  let compact = $derived(!draftWraps && attachments.length === 0 && voiceState.phase === "idle");
  $effect(() => { if (text === "") draftWraps = false; });
  // The composer stays usable under a waiting ask (owner): the ask is answered in its own
  // shell, and a message still goes as the delivery chip says.
  let inputDisabled = $derived(disabled || attachmentIntakesInFlight > 0);
  /** Browser recording capability controls availability. The question panel and
   *  permission ask always outrank voice. */
  let voiceAvailable = $derived(voiceCaptureAvailable(voiceSupported, true));
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
    if (inputDisabled) addMenuOpen = false;
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

  const drivers = createQuery(() => queries.drivers());
  let runControlsInput = $derived<ComposerRunControlsInput>({
    selection: runSelection,
    readError,
    backendKey,
    conversationExists,
    running,
    current,
    models,
    backends,
    drivers: drivers.data?.drivers,
    effortOptions,
    startsOnModel,
    startsOnReasoningEffort,
    inputDisabled,
    hasSendableContent: text.trim() !== "" || pendingImages.length > 0 || pendingFiles.length > 0 || pendingVideos.length > 0,
    sendsInFlight,
    deliveryMode,
    supportsSteer,
    askWaiting
  });
  let runControlsView = $derived(resolveComposerRunControls(runControlsInput));
  let shownPlaceholder = $derived(composerPlaceholderFor({ askWaiting, mode: runControlsView.deliveryMode, running, base: placeholder }));
  $effect(() => {
    const selection = runSelection;
    if (onPreviewRunSelection) untrack(() => onPreviewRunSelection?.(selection));
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
      pickedBackend: runSelection.pickedBackend,
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
      pickedBackend: draft.pickedBackend ?? null,
      pickedReasoningEffort: draft.pickedReasoningEffort,
      effortIsSet: draft.effortIsSet
    };
    compositionRevision = draft.compositionRevision;
  }

  function applyRunSelectionIntent(intent: ComposerRunSelectionIntent): void {
    let next = applyComposerRunSelectionIntent(runControlsInput, intent);
    if (intent.intent === "choose_backend" && !conversationExists) {
      const previousBackend = runSelection.pickedBackend ?? backendKey;
      if (previousBackend) providerSelections[previousBackend] = { ...runSelection };
      next = providerSelections[intent.backendKey] ?? next;
      next = {...next, pickedBackend: intent.backendKey};
    }
    const changed = !sameRunSelection(runSelection, next);
    if (changed) {
      runSelection = next;
      recordDraftChange();
    }
    if (
      intent.intent === "choose_model"
      || intent.intent === "choose_reasoning_effort"
    ) {
      returnFocusToTheField();
    }
  }

  async function send(): Promise<void> {
    await intakeTail;
    // v10 (4): a question that takes several answers goes on from the well's send once
    // something is chosen and nothing is written.
    if (userInput !== null && questionPanel !== null && questionCommitReady && text.trim() === "" && attachments.length === 0) {
      questionPanel.commit();
      return;
    }
    // Words written under a question that takes an own answer are that answer: they go to
    // the question, not into the turn, and leave the box once taken.
    if (askWaiting === "answers" && questionPanel !== null && text.trim() !== "") {
      if (questionPanel.answerWith(text.trim())) {
        text = "";
        cursorAt = 0;
        return;
      }
    }
    const trimmed = text.trim();
    if ((!trimmed && pendingImages.length === 0 && pendingFiles.length === 0 && pendingVideos.length === 0) || inputDisabled) return;
    const capturedRetryContext = restoredSendContext;
    restoredSendContext = null;
    // The mode the controls show, which is the person's choice only while a turn runs.
    const capturedMode = runControlsView.deliveryMode;
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
      applyRunSelectionIntent({ intent: "choose_backend", backendKey });
    },
    chooseModel: (model, reasoningEffort) =>
      applyRunSelectionIntent({ intent: "choose_model", model, reasoningEffort }),
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
    returnFocusToTheField();
    input.setSelectionRange(input.value.length, input.value.length);
    cursorAt = input.value.length;
  }

  function intakeFiles(
    files: Iterable<File> | ArrayLike<File> | null | undefined
  ): Promise<void> {
    if (disabled) return Promise.resolve();
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
    // The box is disabled while files are read, and a disabled box drops focus to the page.
    const focusWasInComposer = boxElement?.contains(document.activeElement) ?? false;
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
          ? "Choose PNG, JPEG, GIF or WebP images up to 3 MiB, or PDF, text, log, Markdown, CSV, TSV, JSON or JSONL files up to 10 MiB."
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
        if (focusWasInComposer) void tick().then(() => { if (!destroyed && !inputDisabled) returnFocusToTheField(); });
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
      void tick().then(() => returnFocusToTheField());
    } else entry.error = "Your newer draft is kept. Finish it, then return this message to the composer.";
    videoChanged();
  }
  /** Shows the first frame of a video still held in memory; the browser draws it, nothing is uploaded. */
  function localFrame(node: HTMLVideoElement, file: File) {
    const url = URL.createObjectURL(file);
    node.src = `${url}#t=0.1`;
    return { destroy: () => URL.revokeObjectURL(url) };
  }
  function videoProgress(video: PendingVideo): string {
    return video.phase === "uploading" ? `Uploading ${Math.floor(video.receivedBytes / video.byteCount * 100)}%`
      : video.phase === "validating" ? "Checking video…" : video.phase === "ready" ? attachmentByteLabel(video.byteCount)
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
    // Enter that confirms an IME choice belongs to the IME, not to send.
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
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
    returnFocusToTheField();
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
    returnFocusToTheField();
    input.setSelectionRange(1, 1);
    theCursorIsInTheBox = true;
    cursorAt = 1;
  }

  /** A transcription landed. The words join the draft the way typing them would have, and
   *  the composer settles with the cursor at the end. An empty transcript is a no-op. */
  async function landTranscript(transcript: string): Promise<void> {
    if (transcript === "") return;
    recordDraftChange();
    const settled = text.trim();
    text = settled === "" ? transcript : `${settled}\n\n${transcript}`;
    await tick();
    const input = inputElement;
    if (input === null) return;
    returnFocusToTheField();
    input.setSelectionRange(input.value.length, input.value.length);
    cursorAt = input.value.length;
  }

  /** The recording's level bars, newest at the right: the meter's recent levels, with the
   *  start of a take (and a browser that cannot measure) drawn as a quiet flat line. */
  const VOICE_BAR_COUNT = 24;
  function voiceBars(levels: readonly number[] | undefined): number[] {
    const recent = (levels ?? []).slice(-VOICE_BAR_COUNT);
    return [...Array<number>(VOICE_BAR_COUNT - recent.length).fill(0), ...recent];
  }

  // A question panel or permission ask arriving mid-voice takes the composer: whatever
  // voice was doing is cancelled rather than left running under a surface it cannot show.

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
    if (!draftWraps && (size.growth > 0 || input.value.includes("\n"))) draftWraps = true;
    const height = `${size.height}px`;
    const host = input.closest<HTMLElement>(".ticket-page, .manager-page") ?? input.closest<HTMLElement>("[data-conversation-pane]");
    host?.style.setProperty("--composer-growth", `${size.growth}px`);
    if (input.style.height !== height) input.style.height = height;
    if (input.style.overflowY !== size.overflowY) input.style.overflowY = size.overflowY;
  }

  let boxElement = $state<HTMLDivElement | null>(null);

  /** The box morphs between its shapes (one line, two rows, an ask) rather than jumping: its
   *  height is read before the change and eased from there to its new natural height. The
   *  words inside are laid out at once; only the frame travels. */
  let morphFrom: number | null = null;
  let morphAnimation: Animation | null = null;
  $effect.pre(() => {
    compact; panelTakesTheBox; userInput; ask; credentialInTheBox;
    untrack(() => { morphFrom = boxElement?.isConnected ? boxElement.getBoundingClientRect().height : null; });
  });
  $effect(() => {
    compact; panelTakesTheBox; userInput; ask; credentialInTheBox;
    untrack(() => {
      const box = boxElement;
      const from = morphFrom;
      morphFrom = null;
      if (box === null || from === null || !box.isConnected) return;
      if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
      morphAnimation?.cancel();
      const to = box.getBoundingClientRect().height;
      if (Math.abs(to - from) < 1) return;
      box.style.overflow = "clip";
      const animation = box.animate([{ height: `${from}px` }, { height: `${to}px` }], { duration: 220, easing: "cubic-bezier(.2,.7,.3,1)" });
      morphAnimation = animation;
      const release = (): void => { if (morphAnimation === animation) { box.style.removeProperty("overflow"); morphAnimation = null; } };
      animation.onfinish = release;
      animation.oncancel = release;
    });
  });

  // --- popovers --------------------------------------------------------------------------------
  /** Every popover the composer opens (add, catalog, model, delivery) opens from the box's top
   *  edge, at its trigger, and floats over whatever stands above the box (the queue tray, the
   *  task pill, the transcript's last lines) on the raised plane, its ring and shadow setting it
   *  off. Nothing steps aside for it, so nothing jumps when it opens or closes. A composer that
   *  has had to scroll draws its popovers in its own flow (app.css). */
  const DISMISSIBLE_POPOVERS = ".listbox-picker-panel, .chat-add-menu";
  /** A press outside an open menu only closes it. Its click would otherwise land on whatever the
   *  menu was floating over (a queued message's Send now, the task pill, the resting dock's
   *  background that opens the conversation), so that one click is held back. The press is
   *  noted before the menu's own outside-press handler closes it (window capture runs first),
   *  and every press or key starts the note again, so a press that never became a click never
   *  holds back a later one. The catalog is left out: it follows the draft as it is typed. */
  let swallowNextClick = false;
  function notePressOutsideAPopover(event: PointerEvent): void {
    swallowNextClick = false;
    const box = boxElement;
    if (box === null || !(event.target instanceof Node)) return;
    const open = box.querySelector<HTMLElement>(DISMISSIBLE_POPOVERS);
    const owner = open?.parentElement;
    if (!open || !owner || owner.contains(event.target)) return;
    swallowNextClick = true;
  }
  function holdBackTheDismissingClick(event: MouseEvent): void {
    if (!swallowNextClick) return;
    swallowNextClick = false;
    event.preventDefault();
    event.stopImmediatePropagation();
  }

  // --- keyboard focus -----------------------------------------------------------------------------
  /** Whether the last thing the person did was on the keyboard rather than a pointer. A field
   *  reached with the keyboard draws the focus ring (a 2px accent edge); one pressed into keeps
   *  the quiet 1px edge, since the caret already shows where the pointer went. */
  let lastInputWasKeyboard = false;
  let keyboardFocus = $state(false);
  function inputModality(event: KeyboardEvent | PointerEvent): void {
    lastInputWasKeyboard = event.type === "keydown";
  }

  /** Whether focus now arriving in the field is the composer handing it back. */
  let returningFocus = false;
  function returnFocusToTheField(): void {
    returningFocus = true;
    try { inputElement?.focus(); } finally { returningFocus = false; }
  }
  // Answering an ask or a question removes the buttons that held focus, and the page keeps
  // none. Give it to the box, but never take it from something the person chose.
  let wasTakenOver = false;
  $effect(() => {
    const answered = wasTakenOver && !takenOver;
    wasTakenOver = takenOver;
    if (!answered) return;
    void tick().then(() => {
      const active = document.activeElement;
      if (!destroyed && !inputDisabled && (active === null || active === document.body)) returnFocusToTheField();
    });
  });
  /** v11 (4): a queued message taken back to edit: its words come into the field, after
   *  anything already written there, and the cursor waits at their end. */
  export function takeBackQueuedWords(words: string): void {
    const settled = text.trimEnd();
    text = settled === "" ? words : `${settled}\n\n${words}`;
    recordDraftChange();
    void tick().then(() => {
      const input = inputElement;
      if (destroyed || input === null) return;
      returnFocusToTheField();
      input.setSelectionRange(input.value.length, input.value.length);
      cursorAt = input.value.length;
    });
  }
  function fieldFocused(): void {
    keyboardFocus = lastInputWasKeyboard;
    readWhereTheCursorIs();
    if (!returningFocus) onFieldChosen?.();
  }
  function fieldPressed(): void {
    readWhereTheCursorIs();
    onFieldChosen?.();
  }

  /** The box's own surface, its padding and the gaps between its controls, belongs to the
   *  field: the line is 24px inside a taller box, and a press beside it should still write.
   *  Controls, attachments and panels in the box keep their own presses. */
  function pressOnTheBoxSurface(event: MouseEvent): boolean {
    const target = event.target;
    return target === boxElement
      || (target instanceof HTMLElement && target.matches(".chat-foot, .chat-submit") && boxElement?.contains(target) === true);
  }
  function keepTheFieldFocused(event: MouseEvent): void {
    if (pressOnTheBoxSurface(event) && document.activeElement === inputElement) event.preventDefault();
  }
  function focusTheFieldFromTheBox(event: MouseEvent): void {
    const input = inputElement;
    if (!pressOnTheBoxSurface(event) || input === null || input.hidden || input.disabled) return;
    input.focus();
    onFieldChosen?.();
  }
  // Effects run after Svelte has written values and hidden-state changes into the DOM.
  $effect(() => {
    text;
    userInput;
    credentialRequest;
    voiceState.phase;
    attachments.length;
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

  /** v7 motion: the composer grows into an ask (and back) over 220 ms ease-out, and the ask's
   *  contents fade in over 120 ms. Reduced motion skips both. */
  let heightBeforeTheAsk = 0;
  let askShownBefore = false;
  $effect.pre(() => {
    panelTakesTheBox;
    heightBeforeTheAsk = boxElement?.getBoundingClientRect().height ?? 0;
  });
  $effect(() => {
    const shown = panelTakesTheBox;
    const box = boxElement;
    if (box === null || shown === askShownBefore) { askShownBefore = shown; return; }
    askShownBefore = shown;
    if (typeof window === "undefined" || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const from = heightBeforeTheAsk;
    const to = box.getBoundingClientRect().height;
    if (from <= 0 || Math.abs(to - from) < 1 || typeof box.animate !== "function") return;
    box.animate([{ height: `${from}px`, overflow: "clip" }, { height: `${to}px`, overflow: "clip" }], { duration: 220, easing: "cubic-bezier(0.2, 0.7, 0.3, 1)" });
    if (shown) box.querySelector<HTMLElement>(".chat-ask")?.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 120, easing: "ease-out" });
  });

  /** v6: menus open with their bottom 8px above the composer's top edge. Each menu's anchor
   *  (+, the model chip, the Steer chip) learns how far it stands under that edge. */
  function liftMenus(): void {
    const box = boxElement;
    if (box === null) return;
    const top = box.getBoundingClientRect().top;
    for (const anchor of box.querySelectorAll<HTMLElement>(".chat-add, [data-conversation-model-picker], [data-conversation-send-mode]"))
      anchor.style.setProperty("--c2-menu-lift", `${Math.max(0, anchor.getBoundingClientRect().top - top)}px`);
  }
  $effect(() => {
    const box = boxElement;
    if (box === null) return;
    const observer = new ResizeObserver(() => liftMenus());
    observer.observe(box);
    const mutations = new MutationObserver(() => liftMenus());
    mutations.observe(box, { childList: true, subtree: true });
    liftMenus();
    return () => { observer.disconnect(); mutations.disconnect(); };
  });

  onMount(() => {
    void restoreLocalState();
    voiceSupported = voiceCaptureSupported();
    voice = createVoiceCapture({
      onState: (state) => (voiceState = state),
      onTranscript: (transcript) => void landTranscript(transcript)
    });
    return () => {
      destroyed = true;
      transfers.dispose();
      sendQueue.dispose();
      releasePendingImages(pendingImages);
      voice?.dispose();
      voice = null;
    };
  });
</script>

<svelte:window
  onkeydowncapture={(event) => { swallowNextClick = false; inputModality(event); }}
  onpointerdowncapture={notePressOutsideAPopover}
  onclickcapture={holdBackTheDismissingClick}
  onpointerdown={(event) => {
    inputModality(event);
    if (!addMenuOpen) return;
    const pressed = event.target;
    if (pressed instanceof Node && addMenuElement?.contains(pressed)) return;
    addMenuOpen = false;
  }}
/>

<section
  class="c2-composer"
  data-conversation-composer
>
  <div class="chat-box-stack">
    {#each localSends as entry (entry.context.messageId)}
      <article class="chat-local-send" data-local-video-send={entry.context.messageId} aria-label="Local pending message">
        {#if entry.attempt.draftBeforeSend.text}<p class="chat-local-send-text">{entry.attempt.draftBeforeSend.text}</p>{/if}
        {#each entry.attempt.draftBeforeSend.pendingVideos ?? [] as video}
          <div class="chat-local-send-file">{video.fileName} · <span role="status">{videoProgress(video)}</span></div>
          {#if video.phase === "failed" && video.file && entry.phase === "waiting"}
            <button type="button" class="cv-btn cv-btn--ghost" onclick={() => void transfers.start(video)}>Retry</button>
          {/if}
        {/each}
        <div class="chat-local-send-state" role="status">{entry.error || (entry.phase === "dispatching" ? "Sending…" : entry.phase === "interrupted" ? "Kept in this browser · review before sending" : "Pending in this browser · not sent yet")}</div>
        {#if entry.phase !== "dispatching"}
          <div class="chat-local-send-actions">
            {#if entry.phase === "failed" && !entry.needsReconciliation}
              <button type="button" class="cv-btn cv-btn--secondary" onclick={() => {entry.phase = "waiting"; entry.error = undefined; videoChanged();}}>Retry send</button>
            {/if}
            <button type="button" class="cv-btn cv-btn--ghost" onclick={() => recoverLocalSend(entry)}>{entry.phase === "interrupted" ? "Return to composer" : "Cancel send"}</button>
            {#if entry.attempt.draftBeforeSend.pendingVideos?.length}
              <button type="button" class="cv-btn cv-btn--ghost" onclick={() => recoverLocalSend(entry, true)}>Remove video</button>
            {/if}
          </div>
        {/if}
      </article>
    {/each}
    <!-- The field takes the keyboard on its own; the box only widens where a press reaches it. -->
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
    <div
      class="chat-box"
      bind:this={boxElement}
      class:drag={draggingAttachments}
      class:has-attachments={attachments.length > 0}
      class:keyboard-focus={keyboardFocus}
      class:is-voice={voiceState.phase !== "idle"}
      class:is-compact={compact}
      class:has-ask={panelTakesTheBox}
      data-conversation-box
      data-conversation-box-shape={compact ? "line" : "grown"}
      role="group"
      aria-label="Conversation composer"
      ondragenter={onDragEnter}
      ondragover={onDragOver}
      ondragleave={onDragLeave}
      ondrop={onDrop}
      onmousedown={keepTheFieldFocused}
      onclick={focusTheFieldFromTheBox}
    >
      <!-- A waiting ask is the composer while it waits: the same frame takes the ask's title,
           body and answers, and the composer's own line stays under them, so the person can
           still write (an own answer to a question, or a message that waits for the turn). -->
      {#if panelTakesTheBox}
      <div class="chat-ask" data-conversation-ask-dock data-conversation-taken-over="true">
        {#if userInput}
          <UserInputQuestionPanel
            bind:this={questionPanel}
            bind:acceptsOwnAnswer={questionTakesOwnAnswer}
            bind:commitReady={questionCommitReady}
            framed={false}
            ownAnswer={attachments.length === 0 ? text : ""}
            onOwnAnswerUsed={() => { text = ""; cursorAt = 0; }}
            onWriteOwnAnswer={() => inputElement?.focus()}
            request={userInput}
            onStop={undefined}
            busy={disabled}
            answerAvailable={userInputAvailable}
            note={userInputNote}
            onSubmit={(answers) => onSubmitUserInput?.(answers)}
          />
        {:else if takenOver && ask}
          <PermissionAskCard
            docked
            ask={ask}
            framed={false}
            answerAvailable={permissionAvailable}
            busy={disabled}
            note={askNote}
            onStop={undefined}
            onAnswer={(optionId) => onAnswer?.(optionId)}
          />
        {:else if credentialInTheBox && credentialRequest}
          <ConversationCredentialRequestCard request={credentialRequest} framed={false} onStop={undefined} />
        {/if}
      </div>
      {/if}
      {#if attachments.length > 0}
        <div class="chat-attachments" data-conversation-attachments aria-label="Attached and waiting to send">
          {#each attachments as attachment (`${attachment.kind}-${attachment.value.id}`)}
            {@const item = attachment.value}
            {@const videoFailed = attachment.kind === "video" && (attachment.value.phase === "failed" || attachment.value.phase === "interrupted")}
            <div
              class="chat-att"
              class:img={attachment.kind === "image"}
              class:file={attachment.kind === "file" || attachment.kind === "video"}
              class:video={attachment.kind === "video"}
              class:failed={videoFailed}
              data-chat-image-preview={attachment.kind === "image" ? "" : undefined}
              data-chat-image-name={attachment.kind === "image" ? item.fileName : undefined}
              data-chat-file-preview={attachment.kind === "file" ? "" : undefined}
              data-chat-file-name={attachment.kind === "file" ? item.fileName : undefined}
              title={`${item.fileName || "pasted image"} · ${attachmentKindLabel(item.fileName, item.mediaType)} · ${attachmentByteLabel(item.byteCount)}`}
            >
              {#if attachment.kind === "image"}
                <img src={attachment.value.previewUrl} alt="" />
              {:else if attachment.kind === "video"}
                <span class="chat-att-thumb" aria-hidden="true">
                  {#if attachment.value.file}<video use:localFrame={attachment.value.file} muted playsinline preload="metadata" tabindex="-1"></video>{/if}
                  <svg viewBox="0 0 24 24"><path d="m8 5 11 7-11 7z" /></svg>
                </span>
                <span class="chat-att-words"><span class="chat-att-name">{attachmentDisplayName(item.fileName)}</span>
                  <span class="chat-att-meta" role="status"><span class="chat-att-type">{videoProgress(attachment.value)}</span></span>
                </span>
                {#if attachment.value.phase === "interrupted" || (attachment.value.phase === "failed" && !attachment.value.file)}
                  <button type="button" class="cv-btn cv-btn--ghost cv-btn--small chat-att-again" onclick={() => fileInput?.click()}>Choose again</button>
                {/if}
                {#if attachment.value.phase === "failed" && attachment.value.file}
                  <button type="button" class="cv-btn cv-btn--ghost cv-btn--small chat-att-again" onclick={() => void transfers.start(attachment.value)}>Retry</button>
                {/if}
                {#if attachment.value.phase === "uploading"}
                  <span
                    class="chat-att-progress"
                    role="progressbar"
                    aria-label={`Uploading ${item.fileName}`}
                    aria-valuemin="0"
                    aria-valuemax="100"
                    aria-valuenow={Math.floor(attachment.value.receivedBytes / Math.max(1, attachment.value.byteCount) * 100)}
                    style:--chat-att-progress={`${Math.min(100, attachment.value.receivedBytes / Math.max(1, attachment.value.byteCount) * 100)}%`}
                  ></span>
                {/if}
              {:else}
                <span class="chat-att-kind" aria-hidden="true">
                  <svg viewBox="0 0 24 24"><path d="M14 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8zM14 3.5V8h4.5M9 12.5h6M9 16h4" /></svg>
                </span>
                <span class="chat-att-words">
                  <span class="chat-att-name">{attachmentDisplayName(item.fileName)}</span>
                  <span class="chat-att-meta"><span class="chat-att-type">{attachmentKindLabel(item.fileName, item.mediaType)}</span><span class="chat-att-size">&nbsp;· {attachmentByteLabel(item.byteCount)}</span></span>
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
              ><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17" /></svg></button>
            </div>
          {/each}
        </div>
      {/if}

      <!-- A voice take stands where the words go: its level and its clock, on the draft's row,
           at the draft's height, so the composer keeps its size. -->
      {#if voiceState.phase !== "idle"}
        <div class="chat-voice-line" data-conversation-voice={voiceState.phase}>
          {#if voiceState.phase === "recording"}
            <span class="chat-voice-word cv-visually-hidden" role="status">Recording</span>
            <span class="chat-voice-bars" aria-hidden="true">
              {#each voiceBars(voiceState.levels) as level, index (index)}
                <span class="chat-voice-bar" style:--voice-level={level}></span>
              {/each}
            </span>
            <span class="chat-voice-clock">{formatVoiceTime(voiceState.elapsedMs)}</span>
          {:else if voiceState.phase === "transcribing"}
            <span class="chat-voice-word chat-voice-shimmer" role="status">Transcribing…</span>
            <span class="chat-voice-clock">{formatVoiceTime(voiceState.keptMs)}</span>
          {:else}
            <span class="chat-voice-word chat-voice-failed" role="status">Transcription failed. The recording is kept.</span>
            <span class="chat-voice-clock">{formatVoiceTime(voiceState.keptMs)}</span>
          {/if}
        </div>
      {/if}

      <textarea
        class="chat-ta"
        data-conversation-input
        rows="1"
        placeholder={shownPlaceholder}
        bind:this={inputElement}
        bind:value={text}
        disabled={inputDisabled}
        hidden={voiceState.phase !== "idle"}
        onkeydown={onKeydown}
        oninput={textChanged}
        onkeyup={readWhereTheCursorIs}
        onclick={fieldPressed}
        onfocus={fieldFocused}
        onblur={() => { theCursorIsInTheBox = false; keyboardFocus = false; }}
        onpaste={onPaste}
      ></textarea>

      <div class="chat-foot">
        <!-- The catalog floats over the box from inside its foot, so opening it never changes
             what the box holds and the box keeps its one layout. -->
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
        {#if voiceState.phase !== "idle"}
          <!-- A voice take keeps the controls' row: Cancel where the + is, and its one
               going-forward action where send is. Cancelling is not a danger, so it is a ghost. -->
          <button
            type="button"
            class="cv-btn cv-btn--ghost chat-voice-x"
            data-voice-cancel
            aria-label={voiceState.phase === "recording"
              ? "Cancel recording"
              : voiceState.phase === "transcribing"
                ? "Cancel transcription"
                : "Discard recording"}
            title={voiceState.phase === "recording" ? "Cancel recording" : voiceState.phase === "transcribing" ? "Cancel transcription" : "Discard recording"}
            onclick={() => voice?.cancel()}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17" /></svg>
            <span>{voiceState.phase === "failed" ? "Discard" : "Cancel"}</span>
          </button>
          <span class="chat-voice-go">
            {#if voiceState.phase === "recording"}
              <button
                type="button"
                class="chat-send on"
                data-voice-stop
                aria-label="Stop recording and transcribe"
                title="Stop recording and transcribe"
                onclick={() => voice?.stopRecording()}
              ><span class="chat-send-fill" aria-hidden="true"></span></button>
            {:else if voiceState.phase === "failed"}
              <button
                type="button"
                class="cv-btn cv-btn--primary chat-voice-again"
                data-voice-retry
                onclick={() => voice?.retry()}
              >Retry</button>
            {/if}
          </span>
        {:else}
          <!-- One place to add something, rather than a row of separate buttons. What it
               offers is still the same set of things, and each of them still does exactly
               what its own button did. -->
          <!-- v14 (2): an ask has no Stop of its own. Its bottom row is the answer well only (pen,
               draft, microphone, send); while a turn is open the header's "..." menu leads
               with "Stop the agent". -->
          {#if panelTakesTheBox}
            <!-- Under an ask the line is where an own answer is written: a pen marks it. -->
            <span class="chat-ask-pen" aria-hidden="true">
              <svg viewBox="0 0 24 24"><path d="M15.2 5.3a2.1 2.1 0 0 1 3 3L8.6 17.9l-4.1 1.1 1.1-4.1z" /></svg>
            </span>
          {/if}
          <div class="chat-add" bind:this={addMenuElement}>
            <button
              type="button"
              class="chat-image chat-add-btn"
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
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M4.5 6.5a2 2 0 0 1 2-2h11a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2zM4.5 16l4.2-4.2a1.5 1.5 0 0 1 2.1 0l4.7 4.7M13.5 14.5l1.2-1.2a1.5 1.5 0 0 1 2.1 0l2.7 2.7M14.5 9h.01" />
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
                    <path d="M3.5 8.5a2 2 0 0 1 2-2h2l1.5-2h6l1.5 2h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2zM12 16.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z" />
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
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14.5 4.5l-5 15" /></svg>
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
                  <rect x="9" y="3.5" width="6" height="11" rx="3" />
                  <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v2.5" />
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
              asking={panelTakesTheBox}
              askCommitReady={userInput !== null && questionCommitReady && text.trim() === "" && attachments.length === 0}
            />
          {:else}
            <span class="chat-submit">{@render microphone()}</span>
          {/if}
        {/if}
      </div>
      <!-- While something is dragged over it, the whole box becomes the place to drop: one
           opaque face over everything in it, so no word of the draft or the controls shows
           through the instruction, and the box keeps its size. -->
      <div class="chat-drop-label" data-conversation-drop-label aria-hidden="true">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M8 12.5l6.8-6.8a3 3 0 0 1 4.2 4.2l-8.2 8.2a5 5 0 0 1-7.1-7.1l8-8" />
        </svg>
        <span>Drop to attach</span>
      </div>
    </div>
  </div>

  {#if intakeError || errorNote}
    <div class="chat-receipt" role="alert" data-conversation-error>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8v5M12 16.5h.01M10.3 4.2 2.8 17.5A2 2 0 0 0 4.5 20.5h15a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0z" /></svg>
      <span>{intakeError ?? errorNote}</span>
    </div>
  {:else if fateNote}
    <div class="c2-fate" data-conversation-fate>{fateNote}</div>
  {/if}
</section>

<style>
  /* --- the stack ----------------------------------------------------------------------------- */
  .c2-composer { display: grid; gap: var(--cv-s2); }

  /* --- one button set (§2.7), for the few buttons the composer draws itself ------------------ */
  .cv-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    height: 32px;
    padding: 0 var(--cv-s3);
    border: 0;
    border-radius: var(--cv-r-md);
    font-family: var(--cv-font);
    font-size: var(--cv-type-meta);
    line-height: var(--cv-leading-meta);
    font-weight: var(--cv-weight-label);
    white-space: nowrap;
    cursor: pointer;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out), color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .cv-btn--small { height: 28px; padding: 0 10px; }
  /* v4.2: the conversation's one primary style. */
  .cv-btn--primary { border-radius: 18px; background: #f2f2f2; color: #121212; }
  .cv-btn--primary:hover { background: #ffffff; }
  .cv-btn--secondary { background: var(--cv-control); color: var(--cv-t1); }
  .cv-btn--secondary:hover { background: var(--cv-control-hover); }
  .cv-btn--ghost { background: transparent; color: var(--cv-t2); }
  .cv-btn--ghost:hover { background: var(--cv-control); color: var(--cv-t1); }
  .cv-btn svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; }
  .cv-btn:focus-visible,
  .chat-add-item:focus-visible,
  .chat-att-x:focus-visible {
    outline: var(--cv-focus-width) solid var(--cv-accent);
    outline-offset: var(--cv-focus-offset);
  }
  .cv-visually-hidden {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0;
  }

  /* A message only this browser holds (a video still uploading, a send to be checked): a quiet
     raised note over the composer, its words in the body tone, its state a step quieter. */
  .chat-local-send {
    display: grid;
    gap: var(--cv-s1);
    padding: var(--cv-s3) var(--cv-s4);
    border-radius: var(--cv-r-lg);
    background: var(--cv-well);
    color: var(--cv-t3);
    font-family: var(--cv-font);
    font-size: var(--cv-type-meta);
    line-height: var(--cv-leading-meta);
  }
  .chat-local-send-text { margin: 0; white-space: pre-wrap; color: var(--cv-t1); font-size: var(--cv-type-ui); line-height: var(--cv-leading-ui); }
  .chat-local-send-file { color: var(--cv-t2); }
  .chat-local-send-actions { display: flex; flex-wrap: wrap; gap: var(--cv-s1); margin: var(--cv-s1) 0 0 calc(var(--cv-s3) * -1); }

  /* --- the add menu (§3.14): the floating plane, upward from the + ---------------------------- */
  .chat-add { position: relative; display: inline-flex; }
  .chat-add-menu {
    position: absolute;
    z-index: 40;
    min-width: 220px;
    display: flex;
    flex-direction: column;
    /* v7: a menu is a Surface with the float shadow; its rows are quiet Buttons. */
    padding: 6px;
    background: #1c1c1c;
    border: 1px solid #2a2a2a;
    border-radius: 20px;
    box-shadow: var(--cv-shadow-float);
    animation: chat-pop-in var(--cv-dur-base) var(--cv-ease-out);
  }
  @keyframes chat-pop-in {
    from { opacity: 0; transform: translateY(4px); }
    to { opacity: 1; transform: none; }
  }
  .chat-add-item {
    display: flex;
    align-items: center;
    width: 100%;
    gap: 6px;
    min-height: 32px;
    padding: 6px 14px;
    background: none;
    border: none;
    border-radius: 999px;
    color: #bdbdbd;
    font-family: var(--cv-font);
    font-size: 13px;
    font-weight: var(--cv-weight-label);
    line-height: 20px;
    text-align: left;
    cursor: pointer;
  }
  .chat-add-item:hover:not(:disabled),
  .chat-add-item:focus-visible { color: #ececec; background: #2a2a2a; }
  .chat-add-item:disabled { color: var(--cv-t4); cursor: default; }
  .chat-add-item svg {
    width: 16px;
    height: 16px;
    flex: none;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
    color: #bdbdbd;
  }
  .chat-add-item:hover svg { color: #ececec; }
  .chat-add-count {
    margin-inline-start: auto;
    font-size: var(--cv-type-caption);
    font-variant-numeric: tabular-nums;
    color: var(--cv-t3);
  }

  /* --- drag over (§3.13): the whole shell becomes the place to drop -------------------------- */
  /* The shell takes the accent wash and the words "Drop to attach" stand alone in its middle;
     what was in it is covered, at the same size, so nothing moves and no edge changes colour. */
  .chat-box.drag { border-color: transparent; box-shadow: none; }
  .chat-drop-label {
    inset: -1px;
    gap: var(--cv-s2);
    border-radius: inherit;
    background: linear-gradient(var(--cv-accent-wash), var(--cv-accent-wash)), var(--cv-raised);
    color: var(--cv-t1);
    font-family: var(--cv-font);
    font-size: var(--cv-type-ui);
    line-height: var(--cv-leading-ui);
    font-weight: var(--cv-weight-label);
    z-index: 45;
  }
  .chat-drop-label svg {
    width: 18px;
    height: 18px;
    flex: none;
    fill: none;
    stroke: var(--cv-accent);
    stroke-width: 1.6;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  /* --- a voice take (§3.13): level and clock on the draft's row, Cancel and Stop on the controls' */
  .chat-voice-line {
    display: flex;
    align-items: center;
    gap: var(--cv-s3);
    min-width: 0;
    height: 24px;
    padding: 0 var(--cv-s2);
    font-family: var(--cv-font);
    font-size: var(--cv-type-meta);
    line-height: var(--cv-leading-meta);
    color: var(--cv-t2);
  }
  .chat-voice-word { flex: none; white-space: nowrap; }
  .chat-voice-failed { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; color: var(--cv-danger); }
  /* Transcribing is the one moving word: a slow light passing along it. */
  .chat-voice-shimmer {
    background: linear-gradient(90deg, var(--cv-t2) 0%, var(--cv-t2) 35%, #fafafa 50%, var(--cv-t2) 65%, var(--cv-t2) 100%);
    background-size: 200% 100%;
    -webkit-background-clip: text;
            background-clip: text;
    color: transparent;
    animation: chat-voice-shimmer var(--cv-shimmer) linear infinite;
  }
  @keyframes chat-voice-shimmer { from { background-position: 100% 0; } to { background-position: -100% 0; } }
  .chat-voice-bars {
    flex: 1;
    min-width: 0;
    height: 24px;
    display: flex;
    align-items: center;
    justify-content: flex-start;
    gap: 3px;
    overflow: hidden;
  }
  .chat-voice-bar {
    flex: none;
    width: 2px;
    height: max(2px, calc(var(--voice-level, 0) * 22px));
    border-radius: 1px;
    background: var(--cv-t2);
    transition: height var(--cv-dur-fast) linear;
  }
  .chat-voice-clock {
    flex: none;
    margin-inline-start: auto;
    font-variant-numeric: tabular-nums;
    color: var(--cv-t3);
  }
  .chat-voice-x { margin-inline-start: calc(var(--cv-s1) * -1); padding-inline: var(--cv-s2) var(--cv-s3); }
  .chat-voice-go { flex: none; margin-inline-start: auto; display: flex; align-items: center; }
  @media (prefers-reduced-motion: reduce) {
    .chat-voice-bar { transition: none; }
    .chat-voice-shimmer { animation: none; color: var(--cv-t2); background: none; }
    .chat-add-menu { animation: none; }
  }

  /* --- one line, two rows, an ask: the box's three shapes ------------------------------------ */
  /* At rest the composer is one line: add, the draft, the run chips, the microphone and
     send/stop on a single row inside the one frame. Its foot gives its controls to that row
     (display: contents), so nothing is drawn twice and nothing is re-made between shapes. */
  .chat-box.is-compact {
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
    column-gap: var(--cv-s1);
    /* v7: a 52px Surface line: 9 + 32 + 9 inside the 1px edge, radius 20 like every Surface. */
    min-height: 52px;
    box-sizing: border-box;
    padding: 9px;
    border-radius: 20px;
  }
  .chat-box.is-compact > .chat-ta { flex: 1 1 0; order: 1; min-width: 0; padding-inline: var(--cv-s1) var(--cv-s2); }
  .chat-box.is-compact > .chat-foot { display: contents; }
  .chat-box.is-compact > .chat-foot > :global(*) { order: 2; }
  .chat-box.is-compact > .chat-foot > :is(.chat-add, .chat-ask-pen) { order: 0; }
  .chat-box.is-compact > .chat-foot > :global([data-conversation-model-picker]) { margin-inline-start: 0; }
  /* v11 (12): 8px between each control on the right (model, Steer, mic, Send/Stop), on the
     line and grown alike: the model chip adds 4 to the row's 4px gap. */
  .chat-box:not(.has-ask) > .chat-foot > :global(.chat-submit) { gap: var(--cv-s2); }
  .chat-box:not(.has-ask) > .chat-foot > :global([data-conversation-model-picker]) { margin-inline-end: var(--cv-s1); }
  .chat-box.is-compact .chat-ta::placeholder { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  /* An ask is the composer while it waits (v4): the same object, grown upward, radius 20.
     The ask's header, question and tiles stand in the frame on a 16px inset, and the
     composer's own line is its last row: a pen, the draft, the microphone and the ask's
     primary pill in the send slot. No add, no model, no mode chip and no Stop under it. */
  .chat-box.has-ask, .chat-box.has-ask.is-compact { border-radius: 20px; padding: var(--cv-s1) var(--cv-s2) var(--cv-s2); }
  .chat-box > .chat-ask { flex: 1 0 100%; order: -1; min-width: 0; margin-block-end: var(--cv-s3); }
  .chat-box > .chat-ask :global(.cv-ask-body) { padding: var(--cv-s4) var(--cv-s2) 0; }
  .chat-box > .chat-ask :global(.cv-ask-footer) { padding: var(--cv-s3) var(--cv-s2) 0; }
  .chat-box.has-ask > .chat-foot :is(.chat-add, :global([data-conversation-model-picker])) { display: none; }
  .chat-ask-pen { flex: none; display: grid; place-items: center; width: 32px; height: 32px; color: var(--cv-quiet-ink, #bdbdbd); }
  .chat-ask-pen svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; }
  /* v4.2: the ask's own-answer line sits in its own well (#141414, radius 12, 44 high) so it
     reads as an input: the pen, the draft and the microphone share one fill, joined edge to
     edge; the primary pill stands outside it, on its right, 8px off. The send slot gives its
     two pieces to the line (display: contents) so the microphone can sit inside the well. */
  /* The line stands on the tiles' edges (8px further in than the box's own padding) and as
     far off the box's foot as the tiles are off its sides. */
  .chat-box.has-ask.is-compact { column-gap: 0; padding-block-end: var(--cv-s4); }
  .chat-box.has-ask.is-compact > .chat-foot > :global(.chat-submit) { display: contents; }
  .chat-box.has-ask.is-compact > .chat-foot :global(.chat-submit > *) { order: 2; }
  /* v11 (3): the answer well is the composer's line: pen, draft, microphone and the round
     send all inside it; the send is its last piece (40 wide, the 32 round drawn 4px in from
     the well's edges). The round Stop follows, outside, 8px off. */
  .chat-box.has-ask.is-compact > .chat-foot :global(.chat-submit > .chat-ask-send) { order: 3; }
  /* v8 (7): the free-text well wears a 1px #333 edge. It is up to four pieces (pen, draft,
     mic, send), so each draws its share of the edge; the last piece closes it. */
  .chat-box.has-ask.is-compact > .chat-foot > .chat-ask-pen { box-shadow: inset 1px 0 0 #333, inset 0 1px 0 #333, inset 0 -1px 0 #333; }
  .chat-box.has-ask.is-compact > .chat-ta { box-shadow: inset 0 1px 0 #333, inset 0 -1px 0 #333; }
  .chat-box.has-ask.is-compact:not(:has(.chat-voice-mic, .chat-ask-send)) > .chat-ta { box-shadow: inset -1px 0 0 #333, inset 0 1px 0 #333, inset 0 -1px 0 #333; }
  .chat-box.has-ask.is-compact > .chat-foot :global(.chat-submit > .chat-voice-mic) { box-shadow: inset 0 1px 0 #333, inset 0 -1px 0 #333; }
  .chat-box.has-ask.is-compact:not(:has(.chat-ask-send)) > .chat-foot :global(.chat-submit > .chat-voice-mic) { box-shadow: inset -1px 0 0 #333, inset 0 1px 0 #333, inset 0 -1px 0 #333; }
  .chat-box.has-ask:not(.is-compact) > .chat-ta { box-shadow: inset 0 0 0 1px #333; }
  .chat-box.has-ask.is-compact > .chat-foot > .chat-ask-pen {
    align-self: stretch;
    width: 40px;
    height: auto;
    min-height: 40px;
    box-sizing: border-box;
    margin-inline-start: var(--cv-s2);
    padding-inline-start: 4px;
    border-radius: 20px 0 0 20px;
    background: #141414;
  }
  .chat-box.has-ask.is-compact > .chat-ta {
    align-self: stretch;
    padding-block: 8px;
    padding-inline: var(--cv-s1) var(--cv-s2);
    background: #141414;
  }
  .chat-box.has-ask.is-compact > .chat-foot :global(.chat-submit > .chat-voice-mic) {
    align-self: stretch;
    width: 32px;
    height: auto;
    min-height: 40px;
    border-radius: 0;
    background: #141414;
  }
  .chat-box.has-ask.is-compact:not(:has(.chat-ask-send)) > .chat-foot :global(.chat-submit > .chat-voice-mic) { width: 40px; padding-inline-end: 4px; border-radius: 0 20px 20px 0; }
  .chat-box.has-ask.is-compact > .chat-foot :global(.chat-submit > .chat-voice-mic:hover:not(:disabled)) { background: #141414; color: var(--cv-t1); }
  .chat-box.has-ask.is-compact:not(:has(.chat-voice-mic, .chat-ask-send)) > .chat-ta { border-radius: 0 20px 20px 0; }
  /* The send, the well's last piece: the well's fill and its closing edge; the round inside. */
  .chat-box.has-ask.is-compact > .chat-foot :global(.chat-submit > .chat-ask-send),
  .chat-box.has-ask.is-compact > .chat-foot :global(.chat-submit > .chat-ask-send:hover:not(:disabled)) {
    align-self: stretch;
    width: 40px;
    height: auto;
    min-height: 40px;
    margin: 0 var(--cv-s2) 0 0;
    padding: 0 4px 0 0;
    border-radius: 0 20px 20px 0;
    background: #141414;
    box-shadow: inset -1px 0 0 #333, inset 0 1px 0 #333, inset 0 -1px 0 #333;
  }
  /* Grown to two rows, the draft is the well and the send stands on its own. */
  .chat-box.has-ask:not(.is-compact) :global(.chat-ask-send) { background: none; }
  :global(.chat-ask-send-round) {
    display: grid;
    place-items: center;
    width: 32px;
    height: 32px;
    border-radius: 50%;
    /* v14 (4): disabled send everywhere: #2a2a2a with a #9a9a9a arrow. */
    background: #2a2a2a;
    color: #9a9a9a;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out), color var(--cv-dur-fast) var(--cv-ease-out);
  }
  :global(.chat-ask-send.on .chat-ask-send-round) { background: #f2f2f2; color: #121212; }
  :global(.chat-ask-send.on:hover:not(:disabled) .chat-ask-send-round) { background: #ffffff; }
  :global(.chat-ask-send-round svg) { display: block; width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
  /* A draft that wraps grows the box to two rows: the draft itself is then the well. */
  .chat-box.has-ask:not(.is-compact) > .chat-ta { padding: 10px var(--cv-s3); border-radius: 20px; background: #141414; }
  .chat-box.has-ask:not(.is-compact) > .chat-foot > .chat-ask-pen { display: none; }

  /* A message on its way out. The box is already empty and already typeable, so the arrow
     staying lit is the one thing on screen that says the send has not landed yet. */
  :global(.chat-send.is-sending:disabled) {
    background: var(--cv-control);
    color: var(--cv-t1);
    opacity: 1;
  }
  .c2-fate {
    padding-inline: var(--cv-s2);
    color: var(--cv-t3);
    font-family: var(--cv-font);
    font-size: var(--cv-type-caption);
    line-height: var(--cv-leading-caption);
  }

  /* --- keyboard focus -------------------------------------------------------------------------- */
  /* A field reached with the keyboard wears the app's focus ring: 2px of accent, 2px out. */
  /* v6: focused (by keyboard too) or expanded, the composer wears one 1px #3a3a3a border and
     no outer ring. */
  .chat-box.keyboard-focus, .chat-box:not(.is-compact) { border-color: #3a3a3a; outline: none; }

  /* --- popovers -------------------------------------------------------------------------------- */
  /* A menu opens from the control that opened it, as a native dropdown does: anchored on its
     own button, 8px off it (v5), its edge on the button's edge (the add menu from +'s left, the
     model and delivery pickers from their chips' right). It overlays what is above and never
     moves it. Their height is capped by the room over the composer (app.css). */
  .chat-box :global(:is(.listbox-picker-panel, .chat-menu)) { z-index: 40; }
  .chat-add { position: relative; }
  /* v6: every menu opens with its bottom 8px above the composer's top edge, not above its chip:
     --c2-menu-lift is how far each anchor stands under the composer's top edge (measured). */
  .chat-box .chat-foot :global([data-conversation-model-picker] > .listbox-picker-panel) {
    left: auto;
    right: 0;
    bottom: calc(100% + 8px + var(--c2-menu-lift, 0px));
  }
  .chat-add-menu { inset-block-end: calc(100% + 8px + var(--c2-menu-lift, 0px)); inset-inline-start: 0; }

  /* --- pending attachments (§3.9) -------------------------------------------------------------- */
  /* A picture is a 56px square that its thumbnail fills; a file is a 56px tile up to 240px wide
     on the control fill: a 32px glyph tile, its name, and its kind and size in words. */
  /* v7: a file waiting to go is an attachment card, a Surface (#1c1c1c, a 1px #2a2a2a edge,
     radius 20); a picture is the picture, radius 12 with the same 1px edge, as in messages. */
  .chat-att {
    height: 56px;
    border: 1px solid #2a2a2a;
    border-radius: 20px;
    background: #1c1c1c;
  }
  .chat-att.img { width: 76px; height: 76px; border: 0; border-radius: 12px; background: #1c1c1c; }
  .chat-att.img img { border-radius: 12px; }
  /* v10 (9): a thumbnail's edge is 1px rgba(255,255,255,0.12). */
  .chat-att.img::after { content: ""; position: absolute; inset: 0; border-radius: inherit; box-shadow: inset 0 0 0 1px rgb(255 255 255 / 16%); pointer-events: none; }
  /* Pass 11: a file tile sits on #242424, one step under the #2a2a2a remove circle in its
     corner, so the circle reads as on a picture (on #2a2a2a it vanished and the x floated). */
  /* p12c: a file chip is as tall as a picture chip (76), its content centred. */
  .chat-att.file {
    height: 76px;
    align-items: center;
    gap: 10px;
    max-width: 240px;
    padding: 0 16px 0 12px;
    background: #1c1c1c;
  }
  .chat-att.video { max-width: 280px; padding-inline-start: var(--cv-s1); }
  .chat-att-kind {
    /* v7: the file's 16px icon alone, #bdbdbd, as on the attachment card in the transcript. */
    width: 32px;
    height: 32px;
    background: transparent;
    color: #bdbdbd;
  }
  .chat-att-kind svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; }
  .chat-att-thumb {
    position: relative; flex: none; display: grid; place-items: center; overflow: hidden;
    width: 64px; height: 48px; border-radius: var(--cv-r-sm); background: var(--cv-raised);
  }
  .chat-att-thumb video { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
  .chat-att-thumb svg {
    position: relative; width: 20px; height: 20px; padding: 4px; fill: var(--cv-primary-ink);
    border-radius: 50%; background: var(--cv-primary-fill);
  }
  .chat-att-words { gap: 0; }
  .chat-att-name {
    color: #ececec;
    font-family: var(--cv-font);
    font-size: 14px;
    line-height: 20px;
    font-weight: var(--cv-weight-label);
  }
  .chat-att-meta {
    color: #b0b0b0;
    font-family: var(--cv-font);
    font-size: 13px;
    line-height: 20px;
    font-variant-numeric: tabular-nums;
  }
  /* A failed upload says so in its own words, in the danger tone, with Retry beside it. */
  .chat-att.failed .chat-att-meta { color: var(--cv-danger); }
  .chat-att-again { flex: none; margin-inline-start: auto; }
  /* Upload progress: a 2px bar along the tile's foot in the body tone, never the accent. */
  .chat-att-progress {
    position: absolute;
    inset: auto var(--cv-s2) 4px var(--cv-s2);
    height: 2px;
    border-radius: 1px;
    background: rgb(255 255 255 / 10%);
    overflow: hidden;
  }
  .chat-att-progress::after {
    content: "";
    display: block;
    width: var(--chat-att-progress, 0%);
    height: 100%;
    background: var(--cv-t2);
    transition: width var(--cv-dur-base) linear;
  }
  /* v10 (11): the remove button sits inside the chip's top-right corner, 6px in: a 20px
     rgba(0,0,0,0.6) circle with a white x. Nothing overhangs the chip, so the row needs no
     room around it; chips are 8px apart. */
  .chat-attachments { gap: var(--cv-s2); padding-block-start: 4px; padding-inline-end: 0; }
  .chat-att-x {
    top: 6px;
    right: 6px;
    width: 20px;
    height: 20px;
    padding: 0;
    border: 0;
    border-radius: 50%;
    background: rgb(0 0 0 / 60%);
    box-shadow: none;
    color: #ffffff;
    z-index: 1;
    transition: color var(--cv-dur-fast) var(--cv-ease-out), background-color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .chat-att-x:hover:not(:disabled), .chat-att-x:focus-visible { background: rgb(0 0 0 / 80%); background-image: none; color: #ffffff; }
  .chat-att-x svg {
    display: block;
    width: 20px;
    height: 20px;
    padding: 5px;
    box-sizing: border-box;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.5;
    stroke-linecap: round;
  }
  .chat-att-x:disabled { color: var(--cv-t3); cursor: default; }
  /* v10 (11): the circle is inside the tile's corner, so a file tile's words stop short of it. */
  .chat-att.file { padding-inline-end: 32px; }
  /* v13 (6): the picture chip and the file chip are one shape: 76 high, radius 10, the remove
     circle 6px inside the top-right corner of both. A file chip is as wide as its words plus
     the circle: the words stop 8px before the circle (6 + 20 + 8 = 34), no wide empty end.
     A picture crops from its top, so a chart's or a screen's title shows. */
  .chat-att, .chat-att.img, .chat-att.file { height: 76px; border-radius: 10px; }
  .chat-att.img img, .chat-att.img::after { border-radius: 10px; }
  .chat-att.img img { object-position: left top; }
  .chat-att.file { padding-inline-end: 34px; }
  /* The file chip's 1px edge is part of its box: its circle sits 5px inside the edge, so it is
     6px from the chip's outer corner, as on the picture (which has no edge). */
  .chat-att.file > .chat-att-x { top: 5px; right: 5px; }
  /* v15 (2): the remove button is a 20px rgba(20,20,20,0.85) circle with a 1px
     rgba(255,255,255,0.24) ring and an #ececec x, inside the chip's top-right corner, 6px in, on
     both chips (v14 (9)'s white badge half outside the chip is withdrawn). Nothing overhangs, so
     the row needs no room around it. The file chip has 12px before its icon; after its words it
     leaves room for the button only (6 + 20 + 6 = 32). */
  .chat-attachments { gap: var(--cv-s2); padding-block-start: 0; margin-block-start: 0; padding-inline-end: 0; }
  .chat-att-x, .chat-att-x:hover:not(:disabled), .chat-att-x:focus-visible {
    top: 6px;
    right: 6px;
    background: rgb(20 20 20 / 85%);
    box-shadow: inset 0 0 0 1px rgb(255 255 255 / 24%);
    color: #ececec;
  }
  .chat-att-x:hover:not(:disabled) { background: rgb(36 36 36 / 92%); color: #ffffff; }
  .chat-att.file > .chat-att-x { top: 5px; right: 5px; }
  .chat-att-x svg { stroke-width: 1.5; }
  .chat-att.file { padding-inline: 12px 32px; }
  .chat-att.file > .chat-att-kind { width: 16px; }
  /* v15 (7): 16px between an ask's last option and its answer well (options stay 8 apart). */
  .chat-box.has-ask > .chat-ask { margin-block-end: var(--cv-s4); }
  /* v15 (1): in the grown composer (attachments or several lines) the chips and the draft start at
     the composer's inner left padding, the add button's left edge (v14 (10) withdrawn). The
     one-line composer keeps the add button, then the draft. */
</style>

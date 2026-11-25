<script lang="ts">
  import { messageNotDelivered } from "../../lib/conversation/transcript";
  /** The one place text goes in, and the one place a change to the run rides out on.
   *
   * What is typed here is this component's own and nothing arriving from the server ever
   * touches it. A model or effort picked here changes nothing until the next message
   * carries it; closing the picker without sending leaves the conversation exactly as it
   * was. When an ask is waiting the whole box gives way to it, because until it is
   * answered there is nothing else this composer could usefully do.
   *
   * Pressing Enter puts the current draft into the conversation and leaves the box open
   * for the next message. Uploads can keep running after the draft leaves the box.
   * Edit can take back the outgoing text and attachments when the next draft is empty.
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
  import { AttachmentTransfers, pendingUploadsAsPieces, releaseLocalPreview, restoredPendingUploads } from "../../lib/conversation/videoUploads/uploads";
  import { rememberSentAttachments } from "../../lib/conversation/localSentMedia";
  import type { PendingVideo, UploadDestination, ComposerSendContext } from "../../lib/conversation/videoUploads/contracts";
  import { LocalComposerSends, releaseRemovedLocalSends, recallLocalSends, rememberLocalSends, recallDraftVideo, rememberDraftVideo, type PendingComposerSend } from "../../lib/conversation/videoUploads/localSends";

  import { catalogEntriesForTrigger, catalogEntriesMatching } from "./composer/catalog";
  import { attachmentSurface } from "./composer/attachmentSurface";
  import ComposerAttachments from "./composer/ComposerAttachments.svelte";
  import ComposerAddMenu from "./composer/ComposerAddMenu.svelte";
  import ComposerCatalogMenu from "./ComposerCatalogMenu.svelte";
  import SystemLine from "./SystemLine.svelte";
  import {
    orderedComposerAttachments, attachmentByteLabel,
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
  import { pendingAnswerAttachments, userInputUploadDraftKey } from "../../lib/conversation/userInput";
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
    releasePendingImages,
    restoredPendingImages,
    type PendingConversationImage
  } from "../../lib/conversation/pendingImages";
  import {
    restoredPendingFiles,
    type PendingConversationFile
  } from "../../lib/conversation/pendingFiles";
  import type {
    ComposerCatalogEntry,
    BackendModel,
    BackendSnapshot,
    ConversationBackendKey,
    PermissionAskOption,
    PromptDeliveryMode,
    SentMessagePiece,
    UserInputAnswers,
    UserInputAttachment,
    UserInputQuestion
  } from "../../lib/conversation/wire";

  let {
    conversationId = null,
    draftKey,
    uploadDestination = null,
    reconcileLocalSend = async () => false,
    onPendingSends,
    onMessageComposed,
    backendKey = null,
    conversationExists = false,
    running = false,
    animateAskEntrance = false,
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
    undeliveredReason = null,
    placeholder = "Message the agent...",
    disabled = false,
    sendAvailable = true,
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
    /** The transcript draws the browser-owned send until transport takes over. */
    onPendingSends?: (entries: readonly PendingComposerSend[]) => void;
    /** A valid message left the draft. Opens a closed conversation before any upload settles. */
    onMessageComposed?: () => void;
    /** The backend this conversation runs on — or, before there is one, the backend a
     *  message sent from here would create it on. */
    backendKey?: ConversationBackendKey | null;
    /** Whether there is a conversation yet. It is what fixes the backend: one that exists
     *  has a live process behind it and cannot be moved to another. */
    conversationExists?: boolean;
    running?: boolean;
    /** Only an ask that arrives live in the visible pane earns an entrance. */
    animateAskEntrance?: boolean;
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
    /** Why the words in the field were not delivered: one system line under the box. */
    undeliveredReason?: string | null;
    placeholder?: string;
    disabled?: boolean;
    sendAvailable?: boolean;
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
    onSubmitUserInput?: (answers: UserInputAnswers) => Promise<boolean> | boolean | void;
    onCancelTurn?: () => void;
    onDiscardHeldPrompt?: (heldPromptId: string) => Promise<unknown> | void;
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
  let pendingVideos = $state.raw<PendingVideo[]>(untrack(() => recallDraftVideo(draftKey)));
  let localSends: PendingComposerSend[] = [];
  let restoredSendContext: ComposerSendContext | null = null;
  let nextVideoId = untrack(() => Math.max(1, ...pendingVideos.map((attachment) => attachment.id + 1)));
  let localStorageKey = untrack(() => draftKey);
  const transfers = new AttachmentTransfers(videoUploadAdapter, videoChanged);
  const makeSendQueue = () => new LocalComposerSends(async (entry, content) => {
    if (destroyed) return false;
    return onSend(content, entry.mode, entry.attempt.carriedRunValues, {
      ...entry.context,
      sentAtUnixMilliseconds: entry.sentAtUnixMilliseconds ?? entry.context.sentAtUnixMilliseconds,
      attachmentNames: (entry.attempt.draftBeforeSend.pendingVideos ?? []).map(video => video.fileName)
    });
  }, videoChanged);
  let sendQueue = makeSendQueue();
  function rememberCurrentUploadDraft(): void {
    const key = userInput && activeQuestionId ? userInputUploadDraftKey(localStorageKey, conversationId ?? uploadDestination?.expected_conversation_id ?? "", userInput.requestId, activeQuestionId) : localStorageKey;
    rememberDraftVideo(key, pendingVideos);
  }
  function videoChanged(): void {
    if (destroyed) return;
    const previousEntries = localSends;
    pendingVideos = [...pendingVideos];
    localSends = [...sendQueue.entries];
    rememberCurrentUploadDraft();
    rememberLocalSends(localStorageKey, localSends);
    releaseRemovedLocalSends(previousEntries, localSends);
    onPendingSends?.(localSends);
    void sendQueue.pump();
  }
  async function restoreLocalState(): Promise<void> {
    const restoringKey = localStorageKey;
    const restoringQueue = sendQueue;
    if (userInput === null) pendingVideos = recallDraftVideo(localStorageKey);
    nextVideoId = Math.max(1, ...pendingVideos.map(video => video.id + 1));
    attachmentOrder = [...attachmentOrder.filter(item => item.kind !== "video"), ...pendingVideos.map(video => ({kind: "video" as const, id: video.id}))];
    sendQueue.entries = recallLocalSends(localStorageKey);
    for (const entry of sendQueue.entries) reserveLocalSend(entry);
    videoChanged();
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
      restoredSendContext = null;
      localStorageKey = key; sendQueue = makeSendQueue();
      void restoreLocalState();
    });
  });
  let nextImageId = 1;
  let nextFileId = 1;
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
  let questionPanel = $state<{ answerWith: (words: string, attached?: UserInputAttachment[]) => boolean; commit: () => void } | null>(null);
  /** v10 (4): asks have no confirm pill: each option row is the action. The free-text well
   *  keeps its own round send. Only a question that takes several answers waits for that
   *  send to go on once something is chosen (questionCommitReady). */
  let questionCommitReady = $state(false);
  let questionTakesOwnAnswer = $state(false);
  let activeQuestionId = $state<string | null>(null);
  $effect(() => { userInput?.requestId; activeQuestionId; pendingVideos; untrack(() => { if (userInput && activeQuestionId) rememberCurrentUploadDraft(); }); });
  let questionAttachments = $derived(pendingAnswerAttachments(pendingImages, pendingFiles, pendingVideos));
  let questionUploadBusy = $derived(userInput !== null && pendingVideos.some((attachment) => attachment.phase !== "ready"));
  let questionForbidsAttachments = $derived(userInput !== null && !questionTakesOwnAnswer);

  function clearQuestionDraft(requestId: string, questionIds: string[]): void {
    if (userInput !== null && userInput.requestId !== requestId) return;
    releasePendingImages(pendingImages);
    for (const attachment of pendingVideos) releaseLocalPreview(attachment);
    pendingImages = []; pendingFiles = []; pendingVideos = []; attachmentOrder = [];
    rememberDraftVideo(localStorageKey, []);
    for (const questionId of questionIds) rememberDraftVideo(userInputUploadDraftKey(localStorageKey, conversationId ?? uploadDestination?.expected_conversation_id ?? "", requestId, questionId), []);
    text = ""; cursorAt = 0;
    recordDraftChange();
  }

  function restoreQuestionDraft(questionId: string, words: string, attached: UserInputAttachment[], preservePendingUploads = false): void {
    activeQuestionId = questionId;
    releasePendingImages(pendingImages);
    const images = restoredPendingImages(attached, nextImageId);
    const files = restoredPendingFiles(attached, nextFileId);
    nextImageId = images.nextId; nextFileId = files.nextId;
    pendingImages = images.images; pendingFiles = files.files;
    if (!preservePendingUploads) for (const attachment of pendingVideos) releaseLocalPreview(attachment);
    const destination = uploadDestination ?? { kind: "conversation" as const, id: conversationId ?? "", expected_conversation_id: conversationId };
    const interrupted = preservePendingUploads && userInput ? recallDraftVideo(userInputUploadDraftKey(localStorageKey, conversationId ?? uploadDestination?.expected_conversation_id ?? "", userInput.requestId, questionId)).filter((attachment) => attachment.destination.kind === destination.kind && attachment.destination.id === destination.id && attachment.destination.expected_conversation_id === destination.expected_conversation_id) : [];
    pendingVideos = restoredPendingUploads(attached, destination, interrupted);
    nextVideoId = Math.max(1, ...pendingVideos.map((attachment) => attachment.id + 1));
    attachmentOrder = [...pendingImages.map((image) => ({ kind: "image" as const, id: image.id })), ...pendingFiles.map((file) => ({ kind: "file" as const, id: file.id })), ...pendingVideos.map((attachment) => ({ kind: "video" as const, id: attachment.id }))];
    for (const attachment of pendingVideos) if (attachment.phase === "validating") void transfers.validate(attachment);
    videoChanged();
    text = words; cursorAt = words.length;
    recordDraftChange();
  }
  /** A free-text question uses this draft, including files, as its answer. Other asks keep
   *  their own actions. */
  let askWaiting = $derived<"answers" | "holds" | null>(
    !panelTakesTheBox
      ? null
      : userInput !== null && questionTakesOwnAnswer
        ? "answers"
        : "holds"
  );
  /** One line until there is more to hold than one line: the add button, the draft, the run
   *  chips, the microphone and send/stop share a single row. Words that wrap, a line break,
   *  an attachment or a voice take grow it into its two rows; it goes back to one line only
   *  once the draft is empty again, so it never flickers between the two while typing. */
  let draftWraps = $state(false);
  let compact = $derived(!draftWraps && attachments.length === 0 && voiceState.phase === "idle");
  // The composer stays usable under a waiting ask (owner): the ask is answered in its own
  // shell, and a message still goes as the delivery chip says.
  let inputDisabled = $derived(disabled || attachmentIntakesInFlight > 0);
  /** Browser recording capability controls availability. The question panel and
   *  permission ask always outrank voice. */
  let voiceAvailable = $derived(voiceCaptureAvailable(voiceSupported, true));
  /** The Add menu, and the camera the design's third choice needs. */
  let addMenuOpen = $state(false);
  let addMenuElement = $state<HTMLDivElement | null>(null);

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
    inputDisabled: inputDisabled || questionUploadBusy,
    hasSendableContent: sendAvailable && (text.trim() !== "" || pendingImages.length > 0 || pendingFiles.length > 0 || pendingVideos.length > 0),
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
    if (questionUploadBusy) return;
    if (userInput !== null && questionPanel !== null) {
      if (questionTakesOwnAnswer && (text.trim() || questionAttachments.length)) {
        questionPanel.answerWith(text, questionAttachments);
        return;
      }
      if (questionCommitReady) { questionPanel.commit(); return; }
      // A pending question keeps this send on the answer path.
      return;
    }
    if (!sendAvailable) return;
    const trimmed = text.trim();
    if ((!trimmed && pendingImages.length === 0 && pendingFiles.length === 0 && pendingVideos.length === 0) || inputDisabled) return;
    const capturedRetryContext = restoredSendContext;
    const capturedMode = runControlsView.deliveryMode;
    const attempt = beginComposerSend(currentComposerDraft(), runControlsView.carriedRunValues);
    let pending: PendingComposerSend | null = null;
    if ((attempt.draftBeforeSend.pendingVideos?.length ?? 0) > 0 || sendQueue.entries.length > 0) {
      const sentAtUnixMilliseconds = Date.now();
      const messageId = capturedRetryContext?.messageId ?? mintOutgoingMessage({
        content: [...attempt.content], mode: capturedMode, senderLabel: "owner", sentAtUnixMilliseconds
      }).messageId;
      pending = {
        context: capturedRetryContext ?? {
          messageId,
          queueWhilePending: capturedMode === "queue" && running,
          expectedConversationId: conversationId,
          destination: uploadDestination ? {...uploadDestination} : undefined
        },
        attempt,
        mode: capturedMode,
        phase: "waiting",
        sentAtUnixMilliseconds
      };
      if (!reserveLocalSend(pending)) return;
      rememberSentAttachments(pending.context.messageId, attempt.draftBeforeSend.pendingVideos ?? []);
    }
    restoredSendContext = null;
    applyComposerDraft(attempt.draftAfterSend);
    cursorAt = 0;
    releasePendingImages(attempt.draftBeforeSend.pendingImages);
    intakeError = null;
    onMessageComposed?.();
    void tick().then(() => {
      if (!destroyed && !inputDisabled) returnFocusToTheField();
    });
    if (pending) {
      sendQueue.enqueue(pending);
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
    intakeError = null;
    const destination = uploadDestination ?? (conversationId ? { kind: "conversation" as const, id: conversationId, expected_conversation_id: conversationId } : null);
    if (!destination) { intakeError = "File upload is unavailable until this conversation has a destination."; return Promise.resolve(); }
    for (const file of Array.from(files ?? [])) {
      try {
        for (const previous of pendingVideos.filter((entry) => entry.fileName === file.name && (entry.phase === "interrupted" || (entry.phase === "failed" && !entry.file)))) removeVideo(previous);
        const attachment = transfers.create(file, nextVideoId++, destination);
        pendingVideos = [...pendingVideos, attachment];
        attachmentOrder = [...attachmentOrder, { kind: "video", id: attachment.id }];
        recordDraftChange();
        void transfers.start(attachment);
        void transfers.measure(attachment);
      } catch (error) { intakeError = error instanceof Error ? error.message : "The file could not be attached."; }
    }
    if (fileInput) fileInput.value = "";
    return Promise.resolve();
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
  async function pendingSendForAction(messageId: string): Promise<PendingComposerSend | null> {
    const queue = sendQueue;
    const entry = queue.entries.find(candidate => candidate.context.messageId === messageId);
    if (!entry || entry.phase === "dispatching") return null;
    if (!entry.needsReconciliation) return entry;
    try {
      const alreadyDispatched = await reconcileLocalSend(messageId);
      if (destroyed || sendQueue !== queue || !queue.entries.includes(entry)) return null;
      if (alreadyDispatched) { queue.remove(messageId); return null; }
      entry.needsReconciliation = false;
      return entry;
    } catch {
      if (!destroyed && sendQueue === queue) {
        entry.error = "Panels could not check the earlier send. Try again when the connection returns.";
        videoChanged();
      }
      return null;
    }
  }

  export async function retryPendingSend(messageId: string): Promise<boolean> {
    const queue = sendQueue;
    const entry = await pendingSendForAction(messageId);
    if (!entry || destroyed || sendQueue !== queue || !queue.entries.includes(entry) || entry.phase === "dispatching") return false;
    const videos = entry.attempt.draftBeforeSend.pendingVideos ?? [];
    if (entry.needsReattachment || videos.some(video => (video.phase === "failed" || video.phase === "interrupted") && !video.file))
      return restorePendingSend(entry);

    const restoringTokens = entry.phase === "interrupted";
    entry.phase = "waiting";
    entry.error = undefined;
    for (const video of videos) {
      if ((video.phase === "failed" || video.phase === "interrupted") && video.file) void transfers.start(video);
      else if (restoringTokens && video.uploadId && video.phase === "ready") {
        video.phase = "validating";
        void transfers.validate(video);
      }
    }
    videoChanged();
    return true;
  }

  export async function editPendingSend(messageId: string): Promise<boolean> {
    const queue = sendQueue;
    const entry = await pendingSendForAction(messageId);
    if (!entry || destroyed || sendQueue !== queue || !queue.entries.includes(entry) || entry.phase === "dispatching") return false;
    return restorePendingSend(entry);
  }

  function restorePendingSend(entry: PendingComposerSend): boolean {
    const empty = text === "" && !attachments.length && voiceState.phase === "idle";
    if (!empty || inputDisabled || takenOver) {
      entry.error = "Your newer draft is kept. Finish it before editing this message.";
      videoChanged();
      return false;
    }
    const restored = restoreRefusedComposerSend(entry.attempt.draftAfterSend, entry.attempt, nextImageId, nextFileId, attachmentIntakesInFlight);
    if (!restored.restored) return false;
    entry.phase = "interrupted";
    for (const video of restored.draft.pendingVideos ?? []) {
      if (video.uploadId && video.phase === "ready") {
        video.phase = "validating";
        void transfers.validate(video);
      }
    }
    // Edit starts a new send and keeps the original destination for the retained upload tokens.
    const { messageId } = mintOutgoingMessage({
      content: [...entry.attempt.content], senderLabel: "owner", mode: entry.mode
    });
    const { expectedConversationId, destination } = entry.context;
    restoredSendContext = {messageId, expectedConversationId, destination};
    deliveryMode = entry.mode;
    applyComposerDraft(restored.draft);
    nextImageId = restored.nextImageId;
    nextFileId = restored.nextFileId;
    nextVideoId = Math.max(nextVideoId, ...(restored.draft.pendingVideos ?? []).map(video => video.id + 1));
    if (entry.needsReattachment || restored.draft.pendingVideos?.some(video => !video.file && (video.phase === "interrupted" || video.phase === "failed")))
      intakeError = "Select the missing attachments again before sending.";
    sendQueue.remove(entry.context.messageId);
    void tick().then(() => returnFocusToTheField());
    return true;
  }
  function videoProgress(video: PendingVideo): string {
    return video.phase === "uploading" ? `Uploading ${Math.floor(video.receivedBytes / Math.max(1, video.byteCount) * 100)}%`
      : video.phase === "validating" ? "Checking file…" : video.phase === "ready" ? attachmentByteLabel(video.byteCount)
      : video.error || "Select the file again.";
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

  /** The draft's shape is decided here, before Svelte writes the new text and layout to the
   *  page, so the old positions can still be read when the move is drawn. Words that wrap
   *  or break grow the box; it returns to one line only when the draft is empty. A change of
   *  width alone is still found by resizeWritingArea. */
  $effect.pre(() => {
    const draft = text;
    untrack(() => {
      if (draft === "") { draftWraps = false; return; }
      const input = inputElement;
      if (draftWraps || input === null || input.hidden || input.getClientRects().length === 0) return;
      const style = getComputedStyle(input);
      if (!Number.isFinite(Number.parseFloat(style.lineHeight))) return;
      if (textareaMeasurement?.input !== input) {
        textareaMeasurement?.dispose();
        textareaMeasurement = createComposerTextareaMeasurement(input);
      }
      if (draft.includes("\n") || textareaMeasurement.measure(style, draft).growth > 0) draftWraps = true;
    });
  });

  /** The box morphs between its shapes (one line, two rows, an ask) rather than jumping: its
   *  height, the draft and every control are read before the change and eased from there to
   *  where they land, over one duration. The draft's height is settled in the new layout
   *  before the landing place is read, so nothing moves a second time. */
  const MORPH_MS = 220;
  const MORPH_EASING = "cubic-bezier(.2,.7,.3,1)";
  let morphFrom: number | null = null;
  let moversFrom = new Map<Element, DOMRect>();
  let morphAnimations: Animation[] = [];
  function movers(box: HTMLElement): HTMLElement[] {
    const foot = box.querySelector<HTMLElement>(":scope > .chat-foot");
    return [inputElement, ...Array.from(foot?.children ?? [])].filter(
      (element): element is HTMLElement => element instanceof HTMLElement && element.getClientRects().length > 0
    );
  }
  $effect.pre(() => {
    compact; panelTakesTheBox; userInput; ask; credentialInTheBox;
    untrack(() => {
      const box = boxElement?.isConnected ? boxElement : null;
      morphFrom = box?.getBoundingClientRect().height ?? null;
      moversFrom = new Map(box === null ? [] : movers(box).map((element) => [element, element.getBoundingClientRect()]));
    });
  });
  $effect(() => {
    compact; panelTakesTheBox; userInput; ask; credentialInTheBox;
    untrack(() => {
      const box = boxElement;
      const from = morphFrom;
      const rects = moversFrom;
      morphFrom = null;
      moversFrom = new Map();
      for (const animation of morphAnimations) animation.cancel();
      morphAnimations = [];
      resizeWritingArea();
      if (box === null || from === null || !box.isConnected) return;
      if (panelTakesTheBox && !animateAskEntrance) return;
      if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
      const to = box.getBoundingClientRect().height;
      // The box's own animation starts first, so each landing place is read with the box at
      // its starting height. A draft centred in a shrinking box, or controls hung from its
      // bottom, are then drawn from exactly where they stood.
      box.style.overflow = "clip";
      const grow = Math.abs(to - from) < 1 ? null
        : box.animate([{ height: `${from}px` }, { height: `${to}px` }], { duration: MORPH_MS, easing: MORPH_EASING });
      const slides = movers(box).flatMap((element) => {
        const before = rects.get(element);
        if (before === undefined) return [];
        const after = element.getBoundingClientRect();
        const dx = before.left - after.left;
        const dy = before.top - after.top;
        return Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5 ? [] : [element.animate(
          [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }],
          { duration: MORPH_MS, easing: MORPH_EASING }
        )];
      });
      const running = grow === null ? slides : [grow, ...slides];
      morphAnimations = running;
      const release = (): void => {
        if (morphAnimations !== running) return;
        box.style.removeProperty("overflow");
        morphAnimations = [];
      };
      if (running.length === 0) release();
      else void Promise.all(running.map((animation) => animation.finished)).then(release, release);
    });
  });

  // --- popovers --------------------------------------------------------------------------------
  /** Every popover the composer opens (add, catalog, model, delivery) floats over whatever
   *  stands near it on the raised plane, its ring and shadow setting it off. Nothing steps
   *  aside for it, so nothing jumps when it opens or closes. */
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
  export function takeBackUnsentMessage(content: readonly SentMessagePiece[]): boolean {
    if (text !== "" || attachmentIntakesInFlight > 0 || attachments.length > 0 || voiceState.phase !== "idle") return false;
    if (content.some(piece => piece.piece === "video_upload" || piece.piece === "attachment_upload")) return false;
    const images = restoredPendingImages(content, nextImageId);
    const files = restoredPendingFiles(content, nextFileId);
    let imageIndex = 0;
    let fileIndex = 0;
    const order: ComposerAttachmentReference[] = [];
    for (const piece of content) {
      if (piece.piece === "image") order.push({kind: "image", id: images.images[imageIndex++]!.id});
      if (piece.piece === "file") order.push({kind: "file", id: files.files[fileIndex++]!.id});
    }
    pendingImages = images.images;
    pendingFiles = files.files;
    nextImageId = images.nextId;
    nextFileId = files.nextId;
    attachmentOrder = order;
    takeBackQueuedWords(content.flatMap(piece => piece.piece === "text" ? [piece.text] : []).join("\n\n"));
    return true;
  }

  /** v11 (4): a queued message taken back to edit: its words come into the field, after
   *  anything already written there, and the cursor waits at their end. They were queued,
   *  so the composer sends them with Queue again unless the person picks another mode. */
  export function takeBackQueuedWords(words: string): void {
    deliveryMode = "queue";
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
    if (shown && !animateAskEntrance) return;
    if (typeof window === "undefined" || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const from = heightBeforeTheAsk;
    const to = box.getBoundingClientRect().height;
    if (from <= 0 || Math.abs(to - from) < 1 || typeof box.animate !== "function") return;
    box.animate([{ height: `${from}px`, overflow: "clip" }, { height: `${to}px`, overflow: "clip" }], { duration: 220, easing: "cubic-bezier(0.2, 0.7, 0.3, 1)" });
    if (shown) box.querySelector<HTMLElement>(".chat-ask")?.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 120, easing: "ease-out" });
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
      onPendingSends?.([]);
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
      class:question-forbids-attachments={questionForbidsAttachments}
      class:has-key-request={credentialInTheBox}
      data-conversation-box
      data-conversation-box-shape={compact ? "line" : "grown"}
      role="group"
      aria-label="Conversation composer"
      use:attachmentSurface={{ disabled: inputDisabled || questionForbidsAttachments, onFiles: (chosen) => void intakeFiles(chosen), onDragChange: (active) => draggingAttachments = active }}
      onmousedown={keepTheFieldFocused}
      onclick={focusTheFieldFromTheBox}
    >
      <!-- A waiting ask is the composer while it waits: the same frame takes the ask's title,
           body and answers. Questions and permission asks keep a reply line. Key requests
           end at their Decline and Save key actions. -->
      {#if panelTakesTheBox}
      <div class="chat-ask" class:animate-ask={animateAskEntrance} data-conversation-ask-dock data-conversation-taken-over="true">
        {#if userInput}
          <UserInputQuestionPanel
            bind:this={questionPanel}
            bind:acceptsOwnAnswer={questionTakesOwnAnswer}
            bind:activeQuestionId
            bind:commitReady={questionCommitReady}
            framed={false}
            ownAnswer={text}
            ownAttachments={questionAttachments}
            onQuestionDraftChange={restoreQuestionDraft}
            onOwnAnswerUsed={clearQuestionDraft}
            onWriteOwnAnswer={() => inputElement?.focus()}
            request={userInput}
            onStop={undefined}
            busy={inputDisabled || questionUploadBusy}
            uploadDestination={uploadDestination ?? { kind: "conversation", id: conversationId ?? "", expected_conversation_id: conversationId }}
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
      {#if !credentialInTheBox}
      <ComposerAttachments {attachments} {inputDisabled} {videoProgress}
        onChooseAgain={() => fileInput?.click()}
        onRetry={(video) => void transfers.start(video)}
        onRemove={(attachment) => attachment.kind === "image" ? removeImage(attachment.value) : attachment.kind === "video" ? removeVideo(attachment.value) : removeFile(attachment.value)}
      />

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
          <!-- An ask has no Stop of its own. Its bottom row shares the draft and attachment
               controls; while a turn is open the header's "..." menu leads with
               "Stop the agent". -->
          <ComposerAddMenu bind:open={addMenuOpen} bind:root={addMenuElement}
            disabled={inputDisabled || questionForbidsAttachments} fileCount={pendingFiles.length + pendingVideos.length + pendingImages.length}
            onFile={() => fileInput?.click()} onCommand={() => void startWritingACommand()}
          />
          <input bind:this={fileInput} class="chat-image-input" data-conversation-file-input type="file" multiple onchange={() => void intakeFiles(fileInput?.files)} />

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
      {/if}
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
  {:else if undeliveredReason}
    <SystemLine mark="failure" role="alert" data-conversation-undelivered-note="">{messageNotDelivered(undeliveredReason)}</SystemLine>
  {:else if fateNote}
    <div class="c2-fate" data-conversation-fate>{fateNote}</div>
  {/if}
</section>

<style>
  /* --- the stack ----------------------------------------------------------------------------- */
  .c2-composer { display: grid; gap: var(--cv-s2); }

  /* --- the few buttons the composer draws itself -------------------------------------------- */
  :global(.c2-composer .cv-btn) {
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
  :global(.c2-composer .cv-btn--small) { height: 28px; padding: 0 10px; }
  /* The conversation's one primary style (app.css, .cv-button--primary). */
  :global(.c2-composer .cv-btn--primary) { border-radius: 18px; background: #f2f2f2; color: #121212; }
  :global(.c2-composer .cv-btn--primary:hover) { background: #ffffff; }
  :global(.c2-composer .cv-btn--secondary) { background: var(--cv-control); color: var(--cv-t1); }
  :global(.c2-composer .cv-btn--secondary:hover) { background: var(--cv-control-hover); }
  :global(.c2-composer .cv-btn--ghost) { background: transparent; color: var(--cv-t2); }
  :global(.c2-composer .cv-btn--ghost:hover) { background: var(--cv-control); color: var(--cv-t1); }
  :global(.c2-composer .cv-btn svg) { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; }
  :global(.c2-composer .cv-btn:focus-visible),
  :global(.c2-composer .chat-att-x:focus-visible) {
    outline: var(--cv-focus-width) solid var(--cv-accent);
    outline-offset: var(--cv-focus-offset);
  }
  .cv-visually-hidden {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0;
  }

  /* --- drag over: the whole shell becomes the place to drop ---------------------------------- */
  /* The shell takes the accent wash and the words "Drop to attach" stand alone in its middle;
     what it holds is covered, at the same size, so nothing moves and no edge changes colour. */
  .chat-box.drag { border-color: transparent; }
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

  /* --- a voice take: level and clock on the draft's row, Cancel and Stop on the controls' ---- */
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
  }

  /* --- one line, two rows, an ask: the box's three shapes ------------------------------------ */
  /* At rest the composer is one line, 52 tall (9 + 32 + 9 inside its 1px edge): add, the
     draft, the run chips, the microphone and send/stop on a single row inside the one frame.
     Its foot gives its controls to that row (display: contents), so nothing is drawn twice and
     nothing is re-made between shapes. Grown to two rows, or wearing the keyboard's focus, the
     box has the lighter edge and no outer ring. */
  .chat-box.is-compact {
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
    column-gap: var(--cv-s1);
    min-height: 52px;
    box-sizing: border-box;
    padding: 9px;
  }
  .chat-box.keyboard-focus, .chat-box:not(.is-compact) { border-color: var(--cv-control-edge); outline: none; }
  .chat-box.is-compact > .chat-ta { flex: 1 1 0; order: 1; min-width: 0; padding-inline: var(--cv-s1) var(--cv-s2); }
  .chat-box.is-compact > .chat-foot { display: contents; }
  .chat-box.is-compact > .chat-foot > :global(*) { order: 2; }
  .chat-box.is-compact > .chat-foot > :global(.chat-add) { order: 0; }
  .chat-box.is-compact > .chat-foot > :global([data-conversation-model-picker]) { margin-inline-start: 0; }
  /* 8px between each control on the right (model, delivery, microphone, send/stop), on the
     line and grown alike: the model chip adds 4 to the row's 4px gap. */
  .chat-box:not(.has-ask) > .chat-foot > :global(.chat-submit) { gap: var(--cv-s2); }
  .chat-box:not(.has-ask) > .chat-foot > :global([data-conversation-model-picker]) { margin-inline-end: var(--cv-s1); }
  .chat-box.is-compact .chat-ta::placeholder { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  /* An ask is the composer while it waits: the same object, grown upward. The ask's header,
     question and tiles stand in the frame on a 16px inset, and the composer's own line is its
     last row: the draft, microphone and the ask's send. Add follows the same attachment
     capability as drop and paste. Model and mode controls stay hidden under asks. */
  .chat-box.has-ask, .chat-box.has-ask.is-compact { padding: var(--cv-s1) var(--cv-s2) var(--cv-s2); }
  .chat-ask:not(.animate-ask) :global(.cv-ask) { animation: none; }
  /* The ask fills a row only in the compact, wrapping layout. In the grown column its
     natural height leaves room for attachments, the measured draft and the send row. */
  .chat-box > .chat-ask { flex: none; order: -1; min-width: 0; margin-block-end: var(--cv-s4); }
  .chat-box.is-compact > .chat-ask { flex: 1 0 100%; }
  /* The enclosing composer owns overflow. Its column must not squeeze measured controls
     or attachment tiles to fit beside a tall question. */
  .chat-box:not(.is-compact) > :is(.chat-ta, .chat-foot),
  .chat-box:not(.is-compact) > :global(.chat-attachments) { flex-shrink: 0; }
  .chat-box > .chat-ask :global(.cv-ask-body) { padding: var(--cv-s4) var(--cv-s2) 0; }
  .chat-box > .chat-ask :global(.cv-ask-footer) { padding: var(--cv-s3) var(--cv-s2) 0; }
  .chat-box.has-ask > .chat-foot :global([data-conversation-model-picker]) { display: none; }
  .chat-box.question-forbids-attachments > .chat-foot :global(.chat-add) { display: none; }
  /* The ask's own-answer line is one well (#141414, radius 20, at least 40 high) that reads as
     an input: the draft, the microphone and the round send share its fill, joined edge to
     edge, each drawing its share of the well's 1px edge and the last piece closing it. The send
     slot gives its pieces to the line (display: contents) so they sit inside the well; the
     send is its last piece (40 wide, the 32 round drawn 4px in from the well's edges). The line
     stands on the tiles' edges (8px further in than the box's own padding) and as far off the
     box's foot as the tiles are off its sides. */
  .chat-box.has-ask.is-compact { column-gap: 0; padding-block-end: var(--cv-s4); }
  .chat-box.has-key-request, .chat-box.has-key-request.is-compact { padding-block-end: var(--cv-s4); }
  .chat-box.has-key-request > .chat-ask { margin-block-end: 0; }
  .chat-box.has-ask.is-compact > .chat-foot > :global(.chat-submit) { display: contents; }
  .chat-box.has-ask.is-compact > .chat-foot :global(.chat-submit > *) { order: 2; }
  .chat-box.has-ask.is-compact > .chat-foot :global(.chat-submit > .chat-ask-send) { order: 3; }
  .chat-box.has-ask.is-compact > .chat-ta {
    align-self: stretch;
    padding-block: var(--cv-s2);
    margin-inline-start: var(--cv-s2);
    padding-inline: var(--cv-s3) var(--cv-s2);
    border-radius: 20px 0 0 20px;
    background: #141414;
    box-shadow: inset 1px 0 0 #333, inset 0 1px 0 #333, inset 0 -1px 0 #333;
  }
  .chat-box.has-ask.is-compact:not(:has(.chat-voice-mic, .chat-ask-send)) > .chat-ta { border-radius: 0 20px 20px 0; box-shadow: inset -1px 0 0 #333, inset 0 1px 0 #333, inset 0 -1px 0 #333; }
  .chat-box.has-ask.is-compact > .chat-foot :global(.chat-submit > .chat-voice-mic) {
    align-self: stretch;
    width: 32px;
    height: auto;
    min-height: 40px;
    border-radius: 0;
    background: #141414;
    box-shadow: inset 0 1px 0 #333, inset 0 -1px 0 #333;
  }
  .chat-box.has-ask.is-compact:not(:has(.chat-ask-send)) > .chat-foot :global(.chat-submit > .chat-voice-mic) { width: 40px; padding-inline-end: var(--cv-s1); border-radius: 0 20px 20px 0; box-shadow: inset -1px 0 0 #333, inset 0 1px 0 #333, inset 0 -1px 0 #333; }
  .chat-box.has-ask.is-compact > .chat-foot :global(.chat-submit > .chat-voice-mic:hover:not(:disabled)) { background: #141414; color: var(--cv-t1); }
  .chat-box.has-ask.is-compact > .chat-foot :global(.chat-submit > .chat-ask-send),
  .chat-box.has-ask.is-compact > .chat-foot :global(.chat-submit > .chat-ask-send:hover:not(:disabled)) {
    align-self: stretch;
    width: 40px;
    height: auto;
    min-height: 40px;
    margin: 0 var(--cv-s2) 0 0;
    padding: 0 var(--cv-s1) 0 0;
    border-radius: 0 20px 20px 0;
    background: #141414;
    box-shadow: inset -1px 0 0 #333, inset 0 1px 0 #333, inset 0 -1px 0 #333;
  }
  /* Grown to two rows, the draft itself is the well and the send stands on its own. */
  .chat-box.has-ask:not(.is-compact) > .chat-ta { padding: 10px var(--cv-s3); border-radius: 20px; background: #141414; box-shadow: inset 0 0 0 1px #333; }
  .chat-box.has-ask:not(.is-compact) :global(.chat-ask-send) { background: none; }
  /* The ask's send round: the composer's send, with nothing to send a quiet control fill. */
  :global(.chat-ask-send-round) {
    display: grid;
    place-items: center;
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: var(--cv-control);
    color: #9a9a9a;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out), color var(--cv-dur-fast) var(--cv-ease-out);
  }
  :global(.chat-ask-send.on .chat-ask-send-round) { background: #f2f2f2; color: #121212; }
  :global(.chat-ask-send.on:hover:not(:disabled) .chat-ask-send-round) { background: #ffffff; }
  :global(.chat-ask-send-round svg) { display: block; width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }

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

  /* --- popovers -------------------------------------------------------------------------------- */
  /* The add menu and the model and delivery pickers float in the top layer from their own
     buttons (lib/floatingPanel). The command catalog stays over the box. */
  .chat-box :global(.chat-menu) { z-index: 40; }
</style>

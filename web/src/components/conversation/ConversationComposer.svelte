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
  import ComposerCatalogMenu from "./ComposerCatalogMenu.svelte";
  import ConversationFileCard from "./ConversationFileCard.svelte";
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
    backendKey = null,
    conversationExists = false,
    running = false,
    ask = null,
    userInput = null,
    askNote = null,
    current = { model: null, reasoningEffort: null },
    models = [],
    backends = [],
    effortOptions = [],
    composerCatalog = [],
    startsOnModel = null,
    startsOnReasoningEffort = null,
    heldPromptRows = [],
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
    onPromoteHeldPrompt
  }: {
    /** The current conversation, or none before the first message creates it. */
    conversationId?: string | null;
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
    current?: RunValues;
    /** What this conversation's own backend offers. */
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
    fateNote?: string | null;
    errorNote?: string | null;
    placeholder?: string;
    disabled?: boolean;
    /** False when an empty-state form owns the same start values on this screen. */
    showRunPicker?: boolean;
    onSend: (
      content: SentMessagePiece[],
      mode: PromptDeliveryMode,
      picked: RunValues
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
  } = $props();

  let text = $state("");
  let runSelection = $state<ComposerRunSelection>({
    pickedBackend: null,
    pickedModel: null,
    pickedReasoningEffort: null
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
  let imageInput = $state<HTMLInputElement | null>(null);
  let fileInput = $state<HTMLInputElement | null>(null);
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
      empty: text === "" && pendingImages.length === 0 && pendingFiles.length === 0
    })
  );
  let livePlaceholder = $derived(ask !== null ? askPlaceholder(ask) : placeholder);
  let runControlsInput = $derived<ComposerRunControlsInput>({
    selection: runSelection,
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
    hasSendableContent: text.trim() !== "" || pendingImages.length > 0 || pendingFiles.length > 0,
    sendsInFlight
  });
  let runControlsView = $derived(resolveComposerRunControls(runControlsInput));
  /** A retained page must not reload while this composer owns work or a user decision. */
  let compositionActive = $derived(
    text !== ""
    || pendingImages.length > 0
    || pendingFiles.length > 0
    || voiceState.phase !== "idle"
    || takenOver
    || draggingAttachments
    || attachmentIntakesInFlight > 0
    || runSelection.pickedBackend !== null
    || runSelection.pickedModel !== null
    || runSelection.pickedReasoningEffort !== null
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
      pickedModel: runSelection.pickedModel,
      pickedReasoningEffort: runSelection.pickedReasoningEffort,
      compositionRevision
    };
  }

  function applyComposerDraft(draft: ComposerDraft): void {
    text = draft.text;
    pendingImages = [...draft.pendingImages];
    pendingFiles = [...draft.pendingFiles];
    runSelection = {
      ...runSelection,
      pickedModel: draft.pickedModel,
      pickedReasoningEffort: draft.pickedReasoningEffort
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
    if ((!trimmed && pendingImages.length === 0 && pendingFiles.length === 0) || inputDisabled) return;
    const attempt = beginComposerSend(
      currentComposerDraft(),
      runControlsView.carriedRunValues,
      "run_when_free"
    );
    applyComposerDraft(attempt.draftAfterSend);
    cursorAt = 0;
    releasePendingImages(attempt.draftBeforeSend.pendingImages);
    if (imageInput) imageInput.value = "";
    intakeError = null;
    sendsInFlight += 1;
    try {
      const delivered = await onSend(
        [...attempt.content],
        "run_when_free",
        attempt.carriedRunValues
      );
      if (!delivered) await restoreRefusedAttempt(attempt);
    } finally {
      sendsInFlight -= 1;
    }
  }

  const runControlIntents: ComposerRunControlIntents = {
    chooseBackend: (backendKey) =>
      applyRunSelectionIntent({ intent: "choose_backend", backendKey }),
    chooseModel: (model, reasoningEffort) =>
      applyRunSelectionIntent({ intent: "choose_model", model, reasoningEffort }),
    chooseReasoningEffort: (reasoningEffort) => applyRunSelectionIntent({
      intent: "choose_reasoning_effort",
      reasoningEffort
    }),
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
    const chosen = Array.from(files ?? []);
    intakeTail = intakeTail.then(() => intakeChosenFiles(chosen));
    return intakeTail;
  }

  async function intakeChosenFiles(files: readonly File[]): Promise<void> {
    attachmentIntakesInFlight += 1;
    try {
      const imageFiles = files.filter((file) => file.type.toLowerCase().startsWith("image/"));
      const documentFiles = files.filter((file) => !file.type.toLowerCase().startsWith("image/"));
      const intake = await createPendingConversationImages(
        imageFiles,
        nextImageId,
        pendingConversationImageBytes(pendingImages)
      );
      const fileIntake = await createPendingConversationFiles(
        documentFiles,
        nextFileId,
        pendingConversationFileBytes(pendingFiles)
      );
      if (destroyed) {
        releasePendingImages(intake.accepted);
        return;
      }
      nextImageId = intake.nextId;
      nextFileId = fileIntake.nextId;
      if (intake.accepted.length > 0) {
        recordDraftChange();
        pendingImages = [...pendingImages, ...intake.accepted];
      }
      if (fileIntake.accepted.length > 0) {
        recordDraftChange();
        pendingFiles = [...pendingFiles, ...fileIntake.accepted];
      }
      intakeError = intake.rejected.length > 0 || fileIntake.rejected.length > 0
        ? "Choose PNG, JPEG, GIF or WebP images up to 3 MiB, or PDF, text, Markdown, CSV, TSV, JSON or JSONL files up to 10 MiB."
        : null;
    } catch (error) {
      if (!destroyed) {
        intakeError = error instanceof Error ? error.message : "The attachment could not be read.";
      }
    } finally {
      if (!destroyed) {
        attachmentIntakesInFlight -= 1;
        if (imageInput) imageInput.value = "";
        if (fileInput) fileInput.value = "";
      }
    }
  }

  function removeImage(image: PendingConversationImage): void {
    recordDraftChange();
    pendingImages = pendingImages.filter((candidate) => candidate.id !== image.id);
    releasePendingImages([image]);
  }

  function removeFile(file: PendingConversationFile): void {
    recordDraftChange();
    pendingFiles = pendingFiles.filter((candidate) => candidate.id !== file.id);
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

  onMount(() => {
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
      publishCompositionState(false);
      releasePendingImages(pendingImages);
      voice?.dispose();
      voice = null;
    };
  });
</script>

<section
  class="c2-composer"
  data-conversation-composer
  data-conversation-composition-active={compositionActive ? "true" : undefined}
>
  <div class="chat-box-stack">
    <HeldPromptStack
      rows={heldPromptRows}
      {conversationId}
      hermes={backendKey === "hermes"}
      {running}
      onDiscard={onDiscardHeldPrompt}
      onPromote={onPromoteHeldPrompt}
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
          note={askNote}
          onSubmit={(answers) => onSubmitUserInput?.(answers)}
          onCancelTurn={() => onCancelTurn?.()}
        />
      {:else if takenOver && ask}
        <PermissionAskCard
          ask={ask}
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
        />
      {/if}

      {#if userInput === null && pendingImages.length > 0}
        <div class="chat-image-previews" data-chat-image-previews aria-label="Pending images">
          {#each pendingImages as image, index (image.id)}
            <div
              class="chat-image-preview"
              data-chat-image-preview
              data-chat-image-name={image.fileName}
            >
              <img src={image.previewUrl} alt="" />
              <button
                type="button"
                class="chat-image-remove"
                data-chat-image-remove
                aria-label={`Remove image ${index + 1}: ${image.fileName || "pasted image"}`}
                title="Remove image"
                disabled={inputDisabled}
                onclick={() => removeImage(image)}
              >×</button>
            </div>
          {/each}
        </div>
      {/if}

      {#if userInput === null && pendingFiles.length > 0}
        <div class="chat-file-previews" data-chat-file-previews aria-label="Pending files">
          {#each pendingFiles as file, index (file.id)}
            <div class="chat-file-preview" data-chat-file-preview data-chat-file-name={file.fileName}>
              <ConversationFileCard
                href={`data:${file.mediaType};base64,${file.data}`}
                fileName={file.fileName}
                mediaType={file.mediaType}
                byteCount={file.byteCount}
              />
              <button
                type="button"
                class="chat-file-remove"
                data-chat-file-remove
                aria-label={`Remove file ${index + 1}: ${file.fileName}`}
                title="Remove file"
                disabled={inputDisabled}
                onclick={() => removeFile(file)}
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
      <div class="chat-foot">
        {#if takenOver && ask}
          <PermissionAskActions
            ask={ask}
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
          <!-- Pressing this leaves the cursor in the box rather than taking it, because
               everything it does is done to what is being written there. -->
          <button
            type="button"
            class="chat-slash"
            data-conversation-slash
            aria-label="Write a command"
            title="Write a command"
            disabled={inputDisabled}
            onmousedown={(event) => event.preventDefault()}
            onclick={() => void startWritingACommand()}
          >/</button>

          <button
            type="button"
            class="chat-image"
            class:on={pendingImages.length > 0}
            data-conversation-image
            data-conversation-image-count={pendingImages.length || undefined}
            aria-label={pendingImages.length > 0 ? "Attach more images" : "Attach images"}
            title={pendingImages.length > 0
              ? `${pendingImages.length} image${pendingImages.length === 1 ? "" : "s"} selected`
              : "Attach images"}
            disabled={inputDisabled}
            onclick={() => imageInput?.click()}
          >
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M2.5 3.5h11v9h-11zM4 10l2.5-2.5 2 2 1.5-1.5 2 2M10.5 6h.01" />
            </svg>
          </button>
          <button
            type="button"
            class="chat-image"
            class:on={pendingFiles.length > 0}
            data-conversation-file
            data-conversation-file-count={pendingFiles.length || undefined}
            aria-label={pendingFiles.length > 0 ? "Attach more files" : "Attach files"}
            title={pendingFiles.length > 0
              ? `${pendingFiles.length} file${pendingFiles.length === 1 ? "" : "s"} selected`
              : "Attach files"}
            disabled={inputDisabled}
            onclick={() => fileInput?.click()}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M8 12.5l6.8-6.8a3 3 0 0 1 4.2 4.2l-8.2 8.2a5 5 0 0 1-7.1-7.1l8-8" />
            </svg>
          </button>
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
            accept={CONVERSATION_FILE_ACCEPT}
            multiple
            onchange={() => void intakeFiles(fileInput?.files)}
          />

          {#if showRunPicker}
            <ComposerRunControls view={runControlsView} intents={runControlIntents} />
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
  .c2-composer { display: grid; gap: var(--space-2); }
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
  .chat-file-previews { display: grid; gap: var(--space-2); padding: var(--space-2); }
  .chat-file-preview { position: relative; min-width: 0; }
  .chat-file-remove {
    position: absolute;
    inset-block-start: var(--space-3);
    inset-inline-end: var(--space-3);
    z-index: 1;
    width: 1.5rem;
    height: 1.5rem;
    border: 0;
    border-radius: var(--radius-pill);
    background: var(--surface-2);
    color: var(--text-muted);
    cursor: pointer;
  }
</style>

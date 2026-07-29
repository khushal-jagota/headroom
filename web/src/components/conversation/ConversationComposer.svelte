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
   * Writing a command is writing a line that starts with a slash, and while the cursor is
   * still inside that first word the agent's own commands are offered under it. Choosing
   * one writes the words a person would have typed and nothing else: the message goes as
   * ordinary text, and the agent reads its own command name back out of it.
   */
  import { onMount, tick } from "svelte";
  import AgentCommandMenu from "./AgentCommandMenu.svelte";
  import ComposerRunControls from "./composer/ComposerRunControls.svelte";
  import PermissionAskActions from "./PermissionAskActions.svelte";
  import PermissionAskCard from "./PermissionAskCard.svelte";
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
  import {
    createPendingConversationImages,
    pendingConversationImageBytes,
    releasePendingImages,
    type PendingConversationImage
  } from "../../lib/conversation/pendingImages";
  import type {
    AgentCommand,
    BackendModel,
    BackendSnapshot,
    ConversationBackendKey,
    PermissionAskOption,
    PromptDeliveryMode,
    SentMessagePiece
  } from "../../lib/conversation/wire";

  let {
    backendKey = null,
    conversationExists = false,
    running = false,
    ask = null,
    askNote = null,
    current = { model: null, reasoningEffort: null },
    models = [],
    backends = [],
    effortOptions = [],
    availableCommands = [],
    startsOnModel = null,
    startsOnReasoningEffort = null,
    heldPromptCount = 0,
    fateNote = null,
    errorNote = null,
    placeholder = "Message the agent...",
    disabled = false,
    onSend,
    onStop,
    onAnswer,
    onCancelTurn
  }: {
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
    askNote?: string | null;
    current?: RunValues;
    /** What this conversation's own backend offers. */
    models?: readonly BackendModel[];
    /** Every backend this machine reported, each with its own catalog. What the rail
     *  chooses from before a conversation exists; empty is a caller that has not read
     *  them, and then there is nothing to switch to. */
    backends?: readonly BackendSnapshot[];
    effortOptions?: readonly string[];
    /** The commands this conversation's agent reports. An agent that reports none, and an
     *  agent that has not been asked yet, are both an empty list. */
    availableCommands?: readonly AgentCommand[];
    /** What a conversation started from here would run on, before there is one: the
     *  owner's own values, resolved by the same code that will create it. They are what
     *  the selectors show with nothing picked, and neither is ever offered as an option.
     *  The model rides out with the message that creates the conversation, because a
     *  conversation is created on a model somebody can name; the effort does not, because
     *  a model that takes none is a real answer and an untouched control has not given one. */
    startsOnModel?: string | null;
    startsOnReasoningEffort?: string | null;
    heldPromptCount?: number;
    fateNote?: string | null;
    errorNote?: string | null;
    placeholder?: string;
    disabled?: boolean;
    onSend: (
      content: SentMessagePiece[],
      mode: PromptDeliveryMode,
      picked: RunValues
    ) => Promise<boolean>;
    onStop?: () => void;
    onAnswer?: (optionId: string) => void;
    onCancelTurn?: () => void;
  } = $props();

  let text = $state("");
  let runSelection = $state<ComposerRunSelection>({
    deliveryMode: "run_when_free",
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
  /** Escape, on a command still being written. Forgotten as soon as the cursor leaves it,
   *  so coming back to the command offers the menu again. */
  let menuWasDismissed = $state(false);
  let activeCommandIndex = $state(0);
  let pendingImages = $state<PendingConversationImage[]>([]);
  let imageInput = $state<HTMLInputElement | null>(null);
  let nextImageId = 1;
  let dragDepth = 0;
  let draggingImages = $state(false);
  let intakeError = $state<string | null>(null);
  let intakeTail: Promise<void> = Promise.resolve();
  let imageIntakesInFlight = $state(0);
  /** Changes only when the person composes something new. A refusal may restore its
   *  snapshot only while this is still the revision that was sent. */
  let compositionRevision = 0;
  let destroyed = false;

  let takenOver = $derived(ask !== null);
  let inputDisabled = $derived(disabled || takenOver || imageIntakesInFlight > 0);
  let livePlaceholder = $derived(takenOver ? askPlaceholder(ask) : placeholder);
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
    hasSendableContent: text.trim() !== "" || pendingImages.length > 0,
    sendsInFlight
  });
  let runControlsView = $derived(resolveComposerRunControls(runControlsInput));

  // --- the command being written -----------------------------------------------------------
  let commandUnderway = $derived(commandOnTheCursorsLine(text, cursorAt));
  /** What has been typed of the command's name, which is what the list is narrowed by.
   *  Null when the cursor is not inside the name, and then the writing rule offers
   *  nothing. */
  let typedCommandName = $derived(commandUnderway?.typedSoFar ?? null);
  let commandMenuIsOpen = $derived(
    !inputDisabled
    && theCursorIsInTheBox
    && typedCommandName !== null
    && !menuWasDismissed
  );
  let matchingCommands = $derived(commandsMatching(availableCommands, typedCommandName ?? ""));

  // A dismissal belongs to the command it was made on: once the cursor is out of the name,
  // there is nothing left to have dismissed.
  $effect(() => {
    if (typedCommandName === null) menuWasDismissed = false;
  });

  // A different list is a different highlight, and it starts at the top.
  $effect(() => {
    matchingCommands;
    activeCommandIndex = 0;
  });

  $effect(() => {
    if (!inputDisabled) return;
    dragDepth = 0;
    draggingImages = false;
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
      one.deliveryMode === other.deliveryMode
      && one.pickedBackend === other.pickedBackend
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
      pickedModel: runSelection.pickedModel,
      pickedReasoningEffort: runSelection.pickedReasoningEffort,
      compositionRevision
    };
  }

  function applyComposerDraft(draft: ComposerDraft): void {
    text = draft.text;
    pendingImages = [...draft.pendingImages];
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
      if (intent.intent !== "choose_delivery_mode") recordDraftChange();
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
    if ((!trimmed && pendingImages.length === 0) || inputDisabled) return;
    const modeForAttempt = runControlsView.effectiveDeliveryMode;
    const attempt = beginComposerSend(
      currentComposerDraft(),
      runControlsView.carriedRunValues,
      modeForAttempt
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
        modeForAttempt,
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
    chooseModel: (model) =>
      applyRunSelectionIntent({ intent: "choose_model", model }),
    chooseReasoningEffort: (reasoningEffort) => applyRunSelectionIntent({
      intent: "choose_reasoning_effort",
      reasoningEffort
    }),
    chooseDeliveryMode: (deliveryMode) => applyRunSelectionIntent({
      intent: "choose_delivery_mode",
      deliveryMode
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
      imageIntakesInFlight
    );
    if (!restoration.restored) return;
    applyComposerDraft(restoration.draft);
    nextImageId = restoration.nextImageId;
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
    imageIntakesInFlight += 1;
    try {
      const intake = await createPendingConversationImages(
        files,
        nextImageId,
        pendingConversationImageBytes(pendingImages)
      );
      if (destroyed) {
        releasePendingImages(intake.accepted);
        return;
      }
      nextImageId = intake.nextId;
      if (intake.accepted.length > 0) {
        recordDraftChange();
        pendingImages = [...pendingImages, ...intake.accepted];
      }
      intakeError = intake.rejected.length > 0
        ? "Choose PNG, JPEG, GIF or WebP images totaling up to 3 MiB."
        : null;
    } catch (error) {
      if (!destroyed) {
        intakeError = error instanceof Error ? error.message : "The image could not be read.";
      }
    } finally {
      if (!destroyed) {
        imageIntakesInFlight -= 1;
        if (imageInput) imageInput.value = "";
      }
    }
  }

  function removeImage(image: PendingConversationImage): void {
    recordDraftChange();
    pendingImages = pendingImages.filter((candidate) => candidate.id !== image.id);
    releasePendingImages([image]);
  }

  function hasImageTransfer(event: DragEvent): boolean {
    return Array.from(event.dataTransfer?.items ?? []).some((item) =>
      item.type.toLowerCase().startsWith("image/")
    );
  }

  function onDragEnter(event: DragEvent): void {
    if (inputDisabled || !hasImageTransfer(event)) return;
    event.preventDefault();
    dragDepth += 1;
    draggingImages = true;
  }

  function onDragOver(event: DragEvent): void {
    if (inputDisabled || !hasImageTransfer(event)) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    draggingImages = true;
  }

  function onDragLeave(): void {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) draggingImages = false;
  }

  function onDrop(event: DragEvent): void {
    if (inputDisabled) return;
    event.preventDefault();
    dragDepth = 0;
    draggingImages = false;
    void intakeFiles(event.dataTransfer?.files);
  }

  function onPaste(event: ClipboardEvent): void {
    const files = event.clipboardData?.files;
    if (inputDisabled || !files || files.length === 0) return;
    event.preventDefault();
    void intakeFiles(files);
  }

  function onKeydown(event: KeyboardEvent): void {
    if (commandMenuIsOpen) {
      if (event.key === "Escape") {
        event.preventDefault();
        menuWasDismissed = true;
        return;
      }
      // Only when there is something to move between or take. A menu that is open saying
      // there is nothing to offer is a sentence, not a list, and the keys stay the box's.
      if (matchingCommands.length > 0) {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          const step = event.key === "ArrowDown" ? 1 : matchingCommands.length - 1;
          activeCommandIndex = (activeCommandIndex + step) % matchingCommands.length;
          return;
        }
        const highlighted = matchingCommands[activeCommandIndex];
        if ((event.key === "Enter" || event.key === "Tab") && highlighted !== undefined) {
          event.preventDefault();
          void takeTheCommand(highlighted);
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

  /** Where the command being written on this line is, and how much of its name is typed.
   *
   * A command is a line that starts with a slash — the line the cursor is on, not the
   * message, so a command can be written under something already written. Its name runs to
   * the first space, and the menu is offered while the cursor is still inside that name:
   * a space between the slash and the cursor means the person has moved on to what they
   * are asking for.
   */
  function commandOnTheCursorsLine(
    written: string,
    at: number
  ): { start: number; end: number; typedSoFar: string | null } | null {
    const lineStart = at === 0 ? 0 : written.lastIndexOf("\n", at - 1) + 1;
    if (written[lineStart] !== "/") return null;
    let end = lineStart + 1;
    while (end < written.length && !/\s/.test(written[end] ?? "")) end += 1;
    return {
      start: lineStart,
      end,
      typedSoFar: at > lineStart && at <= end ? written.slice(lineStart + 1, at) : null
    };
  }

  /** The commands a typed name reaches, best first: the ones that start with it, then the
   *  ones that merely contain it, alphabetically within each. Nothing typed reaches them
   *  all. */
  function commandsMatching(
    commands: readonly AgentCommand[],
    typed: string
  ): AgentCommand[] {
    const wanted = typed.toLowerCase();
    const byName = (one: AgentCommand, other: AgentCommand): number =>
      one.name.localeCompare(other.name);
    const startsWithIt = commands.filter((command) =>
      command.name.toLowerCase().startsWith(wanted)
    );
    const containsIt = commands.filter(
      (command) =>
        !command.name.toLowerCase().startsWith(wanted)
        && command.name.toLowerCase().includes(wanted)
    );
    return [...startsWithIt.sort(byName), ...containsIt.sort(byName)];
  }

  /** Take the highlighted command: its name goes in, and the message is the person's again.
   *
   * What is written is exactly what they would have typed — the slash, the name, and one
   * space after it — over the command that was being written. Nothing about the message is
   * structured by this: it is sent as the words it is, and the agent reads its own command
   * name back out of them.
   */
  async function takeTheCommand(command: AgentCommand): Promise<void> {
    const underway = commandUnderway;
    if (underway === null) return;
    const written = `/${command.name} `;
    recordDraftChange();
    // The space the name is followed by is the one already there, where there is one,
    // rather than a second one after it.
    const rest = text.slice(underway.end);
    text = text.slice(0, underway.start) + written + (rest.startsWith(" ") ? rest.slice(1) : rest);
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
   * A command is written by starting the line with a slash, so this puts one there and
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

  onMount(() => {
    return () => {
      destroyed = true;
      releasePendingImages(pendingImages);
    };
  });
</script>

<section class="c2-composer" data-conversation-composer>
  <div class="chat-box-stack">
    {#if heldPromptCount > 0}
      <ol class="chat-queue-tray" aria-label="Messages waiting">
        <li class="chat-qrow">
          <span class="chat-qrow-n">{heldPromptCount}</span>
          <span class="chat-qrow-txt">waiting for the agent to be free</span>
        </li>
      </ol>
    {/if}

    <div
      class="chat-box"
      class:drag={draggingImages}
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
      {#if takenOver && ask}
        <PermissionAskCard
          ask={ask}
          busy={disabled}
          note={askNote}
          onAnswer={(optionId) => onAnswer?.(optionId)}
        />
      {/if}

      {#if commandMenuIsOpen}
        <AgentCommandMenu
          commands={matchingCommands}
          activeIndex={activeCommandIndex}
          anyCommandsAtAll={availableCommands.length > 0}
          onChoose={(command) => void takeTheCommand(command)}
          onHighlight={(index) => (activeCommandIndex = index)}
        />
      {/if}

      {#if pendingImages.length > 0}
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

      <textarea
        class="chat-ta"
        data-conversation-input
        rows="1"
        placeholder={livePlaceholder}
        bind:this={inputElement}
        bind:value={text}
        disabled={inputDisabled}
        onkeydown={onKeydown}
        oninput={textChanged}
        onkeyup={readWhereTheCursorIs}
        onclick={readWhereTheCursorIs}
        onfocus={readWhereTheCursorIs}
        onblur={() => (theCursorIsInTheBox = false)}
        onpaste={onPaste}
      ></textarea>

      <div class="chat-foot">
        {#if takenOver && ask}
          <PermissionAskActions
            ask={ask}
            busy={disabled}
            onAnswer={(optionId) => onAnswer?.(optionId)}
            onCancelTurn={() => onCancelTurn?.()}
          />
        {:else}
          <!-- Pressing this leaves the cursor in the box rather than taking it, because
               everything it does is done to what is being written there. -->
          <button
            type="button"
            class="chat-slash"
            data-conversation-slash
            aria-label="Aim this message at a skill"
            title="Aim this message at a skill"
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
          <input
            bind:this={imageInput}
            class="chat-image-input"
            data-conversation-image-input
            type="file"
            accept="image/*"
            multiple
            onchange={() => void intakeFiles(imageInput?.files)}
          />

          <ComposerRunControls view={runControlsView} intents={runControlIntents} />
        {/if}
      </div>
      <div class="chat-drop-label" data-conversation-drop-label aria-hidden="true">
        Drop images to attach
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
    background: var(--surface-overlay);
    color: var(--accent-bright);
    opacity: 1;
  }
  .c2-fate {
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
  }
</style>

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
  import BackendRail from "./BackendRail.svelte";
  import PermissionAskActions from "./PermissionAskActions.svelte";
  import PermissionAskCard from "./PermissionAskCard.svelte";
  import RunValuePicker from "./RunValuePicker.svelte";
  import {
    askPlaceholder,
    deliveryOptionsFor,
    effortOptionsFor,
    modelDetail,
    modelDisplayName,
    preselectedValue
  } from "../../lib/conversation/composer";
  import type { RunValues } from "../../lib/conversation/composer";
  import {
    createPendingConversationImages,
    pendingImagesAsPieces,
    releasePendingImages,
    restoredPendingImages,
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
  let mode = $state<PromptDeliveryMode>("run_when_free");
  let pickedModel = $state<string | null>(null);
  let pickedEffort = $state<string | null>(null);
  /** The backend taken off the rail, which only a conversation that does not exist yet can
   *  have: null is nobody having said, and then what it would be created on is whatever
   *  the caller says is in force. */
  let pickedBackend = $state<ConversationBackendKey | null>(null);
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

  let deliveryOptions = $derived(deliveryOptionsFor(backendKey));
  let effectiveMode = $derived<PromptDeliveryMode>(running ? mode : "run_when_free");
  let takenOver = $derived(ask !== null);
  let inputDisabled = $derived(disabled || takenOver || imageIntakesInFlight > 0);
  let sendIsInFlight = $derived(sendsInFlight > 0 && !running);
  let livePlaceholder = $derived(takenOver ? askPlaceholder(ask) : placeholder);
  // The backend the rail shows. A conversation that exists shows its own and nothing else,
  // so a choice made before it existed cannot be left standing over it.
  let shownBackend = $derived<ConversationBackendKey | null>(
    conversationExists ? backendKey : (pickedBackend ?? backendKey)
  );
  // Whether the rail has been used to leave the backend the caller handed the catalog for.
  let switchedBackend = $derived(!conversationExists && pickedBackend !== null);
  let switchedTo = $derived(
    backends.find((snapshot) => snapshot.backend_key === pickedBackend) ?? null
  );
  // What the pickers offer: the catalog of the backend now showing. Until the rail is used
  // that is the one the caller handed, which is the conversation's own — so a caller with
  // one backend and no list of them is served exactly as it always was. A backend switched
  // to that this machine reported nothing for offers nothing, rather than the last one's.
  let modelsOnOffer = $derived(
    switchedBackend ? (switchedTo?.available_models ?? []) : models
  );
  let effortOptionsOnOffer = $derived(
    switchedBackend ? (switchedTo?.reasoning_effort_options ?? []) : effortOptions
  );
  // What a conversation started from here would run on, kept true through a rail switch.
  // Taking a backend off the rail is saying create it on that one, and a model belongs to
  // the backend that named it — so nothing carries over and it would start on the new
  // backend's own, which is what that backend's card says it runs when nobody names one.
  let modelItWouldStartOn = $derived(
    switchedBackend ? (switchedTo?.default_model_id ?? null) : startsOnModel
  );
  let effortItWouldStartOn = $derived(
    switchedBackend ? (switchedTo?.default_reasoning_effort ?? null) : startsOnReasoningEffort
  );
  let picked = $derived<RunValues>({
    // Into a conversation that exists, only a pick is anything: it is a change, and there
    // is nothing to change when nobody touched the picker. A message that has to create
    // one is the other way round — it has to say which model to create it on, and the
    // answer is the one on the face of the picker, whether a person put it there or the
    // owner's own values did. Nothing on the face means nothing to name, and the server
    // says so rather than this quietly leaving the backend to pick for itself.
    model: conversationExists ? pickedModel : (pickedModel ?? modelItWouldStartOn),
    // The effort is not the model's equal here: a model that takes none is a real answer,
    // so an untouched control has nothing to say and says nothing.
    reasoningEffort: pickedEffort,
    // Only a message that has to create a conversation says what to create it on, and only
    // when somebody took one off the rail. Showing a backend is not choosing it: what is
    // showing before that is what this caller believes is in force, and a belief sent as
    // an instruction would overrule the stored defaults it was guessing at.
    backendKey: conversationExists ? null : pickedBackend
  });
  // What each selector shows with nothing picked: the concrete value already in force.
  let shownModel = $derived(
    pickedModel ?? preselectedValue(current.model, modelItWouldStartOn) ?? ""
  );
  let shownEffort = $derived(
    pickedEffort ?? preselectedValue(current.reasoningEffort, effortItWouldStartOn) ?? ""
  );
  // A value the catalog does not list is still the value being run, so it is offered as
  // itself rather than silently dropped off the face of the selector.
  let modelOptions = $derived(
    shownModel !== "" && !modelsOnOffer.some((model) => model.model_id === shownModel)
      ? [{ model_id: shownModel, display_name: null }, ...modelsOnOffer]
      : modelsOnOffer
  );
  // Effort belongs to the model that will actually run, not to the backend in general.
  let modelEffortOptions = $derived(
    effortOptionsFor(modelsOnOffer, shownModel === "" ? null : shownModel, effortOptionsOnOffer)
  );
  let effortChoices = $derived(
    shownEffort !== "" && !modelEffortOptions.includes(shownEffort)
      ? [shownEffort, ...modelEffortOptions]
      : modelEffortOptions
  );
  // Nothing to show and nothing wide: an effort is still pickable, so the control shrinks
  // to the affordance that says so and nothing more.
  let effortIsBare = $derived(shownEffort === "");
  // The catalog as the picker reads it: a name to pick by, and the quieter line under it.
  let modelPickerChoices = $derived(
    modelOptions.map((model) => ({
      value: model.model_id,
      name: model.display_name ?? model.model_id,
      detail: modelSecondLine(model)
    }))
  );
  // An effort is one word and is its own name. There is nothing quieter to say about it.
  let effortPickerChoices = $derived(
    effortChoices.map((effort) => ({ value: effort, name: effort, detail: null }))
  );

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

  // What the chosen model really is, when the catalog says — an alias and the version it
  // reaches. Its row says it out loud; the pill is only as wide as the name, so on the
  // pill it is the tooltip.
  let modelTitle = $derived.by(() => {
    const value = shownModel === "" ? null : shownModel;
    const name = modelDisplayName(modelsOnOffer, value) ?? "the backend's own model";
    const detail = modelDetail(modelsOnOffer, value);
    return detail === null ? name : `${name} — ${detail}`;
  });

  // A pick belongs to the catalog it was made from. Change backend — or change to a model
  // that takes no effort — and a value the new catalog does not offer is not a pending
  // change any more, it is a value nothing would accept. It goes.
  $effect(() => {
    if (pickedModel !== null && modelsOnOffer.length > 0
        && !modelsOnOffer.some((model) => model.model_id === pickedModel)) {
      pickedModel = null;
    }
  });

  $effect(() => {
    if (pickedEffort !== null && !modelEffortOptions.includes(pickedEffort)) {
      pickedEffort = null;
    }
  });

  /** What a model's row says under its name.
   *
   * The catalog's own second line where it has one — claude says which concrete model an
   * alias reaches — and otherwise the value itself, which is what a display name like
   * "GPT-5.5 Codex" is standing in for. A model already shown under its own value has
   * nothing left to say twice.
   */
  function modelSecondLine(model: BackendModel): string | null {
    const said = model.detail ?? null;
    if (said !== null && said !== "") return said;
    const name = model.display_name;
    return name === null || name === model.model_id ? null : model.model_id;
  }

  /** A value was taken, so the box takes the keyboard back.
   *
   * What a person does after picking a model is carry on writing, and the draft and the
   * cursor are exactly where they left them — this only moves the keyboard.
   */
  function handTheBoxTheKeyboard(): void {
    inputElement?.focus();
  }

  /** Take a backend off the rail: what the next message would create this conversation on.
   *
   * The model and effort go with it. They were picked out of the old backend's catalog and
   * mean nothing to this one — a model id belongs to the backend that named it.
   *
   * The panel stays open and the keyboard stays in it, because switching is how a person
   * gets to the list they came to read.
   */
  function takeTheBackend(key: ConversationBackendKey): void {
    if (conversationExists || key === shownBackend) return;
    pickedBackend = key;
    pickedModel = null;
    pickedEffort = null;
  }

  async function send(): Promise<void> {
    await intakeTail;
    const trimmed = text.trim();
    if ((!trimmed && pendingImages.length === 0) || inputDisabled) return;
    const carried = picked;
    // What goes back if it gets nowhere is what the person had picked, which is not
    // everything the message carried: a message that creates a conversation also carries
    // the value the picker was only showing, and showing is not picking.
    const theirs: RunValues = { ...carried, model: pickedModel, reasoningEffort: pickedEffort };
    const sentImages = pendingImages;
    const content: SentMessagePiece[] = [
      ...(trimmed === "" ? [] : [{ piece: "text" as const, text: trimmed }]),
      ...pendingImagesAsPieces(sentImages)
    ];
    text = "";
    cursorAt = 0;
    pendingImages = [];
    releasePendingImages(sentImages);
    if (imageInput) imageInput.value = "";
    intakeError = null;
    // The change rode out with the message, so it is no longer pending: the selects
    // fall back to showing what the conversation now runs on.
    pickedModel = null;
    pickedEffort = null;
    sendsInFlight += 1;
    try {
      const delivered = await onSend(content, effectiveMode, carried);
      if (!delivered) await giveTheMessageBack(content, theirs);
    } finally {
      sendsInFlight -= 1;
    }
  }

  /** The message got nowhere, so the person is put back where they were.
   *
   * Everything that was about to go comes back together — the content and the change it
   * was carrying — because that is the state they were in when they pressed Enter.
   */
  async function giveTheMessageBack(
    content: readonly SentMessagePiece[],
    carried: RunValues
  ): Promise<void> {
    if (
      text !== ""
      || pendingImages.length > 0
      || pickedModel !== null
      || pickedEffort !== null
      || imageIntakesInFlight > 0
    ) return;
    const sent = content
      .filter((piece): piece is Extract<SentMessagePiece, { piece: "text" }> =>
        piece.piece === "text"
      )
      .map((piece) => piece.text)
      .join("");
    const restored = restoredPendingImages(content, nextImageId);
    text = sent;
    pendingImages = restored.images;
    nextImageId = restored.nextId;
    pickedModel = carried.model;
    pickedEffort = carried.reasoningEffort;
    await tick();
    const input = inputElement;
    if (input === null || text !== sent) return;
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
      const intake = await createPendingConversationImages(files, nextImageId);
      nextImageId = intake.nextId;
      if (intake.accepted.length > 0) pendingImages = [...pendingImages, ...intake.accepted];
      intakeError = intake.rejected.length > 0 ? "Choose image files only." : null;
    } catch (error) {
      intakeError = error instanceof Error ? error.message : "The image could not be read.";
    } finally {
      imageIntakesInFlight -= 1;
      if (imageInput) imageInput.value = "";
    }
  }

  function removeImage(image: PendingConversationImage): void {
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
    return () => releasePendingImages(pendingImages);
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
        oninput={readWhereTheCursorIs}
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

          <!-- Which backend is a question about the conversation rather than about this
               message, so it is drawn beside the models it decides rather than as a third
               pill in the footer. -->
          {#snippet backendRail()}
            <BackendRail
              showing={shownBackend}
              locked={conversationExists}
              onChoose={takeTheBackend}
            />
          {/snippet}

          <RunValuePicker
            label="Model"
            choices={modelPickerChoices}
            value={shownModel}
            searchable
            disabled={inputDisabled}
            title={modelTitle}
            attributes={{ "data-conversation-picker-model": "" }}
            rail={shownBackend === null ? undefined : backendRail}
            onChoose={(model) => {
              pickedModel = model;
              handTheBoxTheKeyboard();
            }}
          />

          {#if effortChoices.length > 0}
            <RunValuePicker
              label="Reasoning effort"
              choices={effortPickerChoices}
              value={shownEffort}
              disabled={inputDisabled}
              title="Reasoning effort"
              attributes={{
                "data-conversation-picker-effort": "",
                "data-conversation-picker-effort-bare": effortIsBare ? "true" : undefined
              }}
              onChoose={(effort) => {
                pickedEffort = effort;
                handTheBoxTheKeyboard();
              }}
            />
          {/if}

          {#if running}
            <div class="chat-seg" data-conversation-delivery role="group" aria-label="Delivery">
              {#each deliveryOptions as option (option.mode)}
                <button
                  type="button"
                  class:on={mode === option.mode}
                  data-conversation-delivery-mode={option.mode}
                  aria-pressed={mode === option.mode}
                  title={option.description}
                  onclick={() => (mode = option.mode)}
                >{option.label}</button>
              {/each}
            </div>
          {/if}

          <button
            type="button"
            class={`chat-send${running ? " stop" : text.trim() || pendingImages.length ? " on" : ""}`}
            class:is-sending={sendIsInFlight}
            data-conversation-send={running ? undefined : true}
            data-conversation-stop={running ? true : undefined}
            data-conversation-sending={sendIsInFlight ? true : undefined}
            aria-busy={sendIsInFlight ? "true" : undefined}
            disabled={running ? false : inputDisabled || (!text.trim() && pendingImages.length === 0)}
            title={running
              ? "Stop the turn — press Enter to send instead"
              : sendIsInFlight
                ? "On its way"
                : "Send"}
            aria-label={running ? "Stop the turn" : sendIsInFlight ? "On its way" : "Send"}
            onclick={() => (running ? onStop?.() : void send())}
          >{running ? "■" : "↑"}</button>
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

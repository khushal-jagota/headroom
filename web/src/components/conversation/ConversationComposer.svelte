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
   * anything is still happening. If the send turns out not to have gone anywhere, the text
   * comes back exactly as it was written, with the cursor at the end; unless something else
   * has been typed in the meantime, in which case that draft is what matters and the error
   * under the box is the whole of the news.
   *
   * Writing a command is writing a line that starts with a slash, and while the cursor is
   * still inside that first word the agent's own commands are offered under it. Choosing
   * one writes the words a person would have typed and nothing else: the message goes as
   * ordinary text, and the agent reads its own command name back out of it.
   */
  import { tick } from "svelte";
  import AgentCommandMenu from "./AgentCommandMenu.svelte";
  import PermissionAskActions from "./PermissionAskActions.svelte";
  import PermissionAskCard from "./PermissionAskCard.svelte";
  import {
    askPlaceholder,
    deliveryOptionsFor,
    effortOptionsFor,
    hasArmedChange,
    modelDetail,
    modelDisplayName,
    preselectedValue
  } from "../../lib/conversation/composer";
  import type { RunValues } from "../../lib/conversation/composer";
  import type {
    AgentCommand,
    BackendModel,
    ConversationBackendKey,
    PermissionAskOption,
    PromptDeliveryMode
  } from "../../lib/conversation/wire";

  let {
    backendKey = null,
    running = false,
    ask = null,
    askNote = null,
    current = { model: null, reasoningEffort: null },
    models = [],
    effortOptions = [],
    availableCommands = [],
    defaultModelId = null,
    defaultReasoningEffort = null,
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
    backendKey?: ConversationBackendKey | null;
    running?: boolean;
    ask?: {
      askId: string;
      title: string;
      detail: string | null;
      options: readonly PermissionAskOption[];
    } | null;
    askNote?: string | null;
    current?: RunValues;
    models?: readonly BackendModel[];
    effortOptions?: readonly string[];
    /** The commands this conversation's agent reports. An agent that reports none, and an
     *  agent that has not been asked yet, are both an empty list. */
    availableCommands?: readonly AgentCommand[];
    /** The concrete values this backend runs when nobody names one. They are what the
     *  selectors show before anybody picks; they are never offered as an option. */
    defaultModelId?: string | null;
    defaultReasoningEffort?: string | null;
    heldPromptCount?: number;
    fateNote?: string | null;
    errorNote?: string | null;
    placeholder?: string;
    disabled?: boolean;
    onSend: (text: string, mode: PromptDeliveryMode, picked: RunValues) => Promise<boolean>;
    onStop?: () => void;
    onAnswer?: (optionId: string) => void;
    onCancelTurn?: () => void;
  } = $props();

  let text = $state("");
  let mode = $state<PromptDeliveryMode>("run_when_free");
  let pickedModel = $state<string | null>(null);
  let pickedEffort = $state<string | null>(null);
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

  let deliveryOptions = $derived(deliveryOptionsFor(backendKey));
  let effectiveMode = $derived<PromptDeliveryMode>(running ? mode : "run_when_free");
  let picked = $derived<RunValues>({ model: pickedModel, reasoningEffort: pickedEffort });
  let armed = $derived(hasArmedChange(current, picked, effectiveMode));
  let takenOver = $derived(ask !== null);
  let inputDisabled = $derived(disabled || takenOver);
  let sendIsInFlight = $derived(sendsInFlight > 0 && !running);
  let livePlaceholder = $derived(takenOver ? askPlaceholder(ask) : placeholder);
  // What each selector shows with nothing picked: the concrete value already in force.
  let shownModel = $derived(
    pickedModel ?? preselectedValue(current.model, defaultModelId) ?? ""
  );
  let shownEffort = $derived(
    pickedEffort ?? preselectedValue(current.reasoningEffort, defaultReasoningEffort) ?? ""
  );
  // A value the catalog does not list is still the value being run, so it is offered as
  // itself rather than silently dropped off the face of the selector.
  let modelOptions = $derived(
    shownModel !== "" && !models.some((model) => model.model_id === shownModel)
      ? [{ model_id: shownModel, display_name: null }, ...models]
      : models
  );
  // Effort belongs to the model that will actually run, not to the backend in general.
  let modelEffortOptions = $derived(
    effortOptionsFor(models, shownModel === "" ? null : shownModel, effortOptions)
  );
  let effortChoices = $derived(
    shownEffort !== "" && !modelEffortOptions.includes(shownEffort)
      ? [shownEffort, ...modelEffortOptions]
      : modelEffortOptions
  );
  // Nothing to show and nothing wide: an effort is still pickable, so the control shrinks
  // to the affordance that says so and nothing more.
  let effortIsBare = $derived(shownEffort === "");

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

  // What the chosen model really is, when the catalog says — an alias and the version it
  // reaches. A native select has nowhere to put a second line, so it is the tooltip.
  let modelTitle = $derived.by(() => {
    const value = shownModel === "" ? null : shownModel;
    const name = modelDisplayName(models, value) ?? "the backend's own model";
    const detail = modelDetail(models, value);
    return detail === null ? name : `${name} — ${detail}`;
  });

  // A pick belongs to the catalog it was made from. Change backend — or change to a model
  // that takes no effort — and a value the new catalog does not offer is not a pending
  // change any more, it is a value nothing would accept. It goes.
  $effect(() => {
    if (pickedModel !== null && models.length > 0
        && !models.some((model) => model.model_id === pickedModel)) {
      pickedModel = null;
    }
  });

  $effect(() => {
    if (pickedEffort !== null && !modelEffortOptions.includes(pickedEffort)) {
      pickedEffort = null;
    }
  });

  async function send(): Promise<void> {
    const trimmed = text.trim();
    if (!trimmed || inputDisabled) return;
    const carried = picked;
    text = "";
    cursorAt = 0;
    // The change rode out with the message, so it is no longer pending: the selects
    // fall back to showing what the conversation now runs on.
    pickedModel = null;
    pickedEffort = null;
    sendsInFlight += 1;
    try {
      const delivered = await onSend(trimmed, effectiveMode, carried);
      if (!delivered) await giveTheMessageBack(trimmed, carried);
    } finally {
      sendsInFlight -= 1;
    }
  }

  /** The message got nowhere, so the person is put back where they were.
   *
   * Everything that was about to go comes back together — the text and the change it was
   * carrying — because that is the state they were in when they pressed Enter.
   */
  async function giveTheMessageBack(sent: string, carried: RunValues): Promise<void> {
    if (text !== "") return;
    text = sent;
    pickedModel = carried.model;
    pickedEffort = carried.reasoningEffort;
    await tick();
    const input = inputElement;
    if (input === null || text !== sent) return;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
    cursorAt = input.value.length;
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
      class:has-ask={takenOver}
      data-conversation-box
      data-conversation-taken-over={takenOver ? "true" : undefined}
      role="group"
      aria-label="Conversation composer"
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

          <select
            class="c2-pick-select"
            class:on={pickedModel !== null}
            data-conversation-picker-model
            aria-label="Model"
            disabled={inputDisabled}
            value={shownModel}
            title={modelTitle}
            onchange={(event) => (pickedModel = event.currentTarget.value || null)}
          >
            {#if shownModel === ""}
              <option value=""></option>
            {/if}
            {#each modelOptions as model (model.model_id)}
              <option value={model.model_id} title={model.detail ?? undefined}>
                {model.display_name ?? model.model_id}
              </option>
            {/each}
          </select>

          {#if effortChoices.length > 0}
            <select
              class="c2-pick-select"
              class:on={pickedEffort !== null}
              class:is-bare={effortIsBare}
              data-conversation-picker-effort
              data-conversation-picker-effort-bare={effortIsBare ? "true" : undefined}
              aria-label="Reasoning effort"
              title="Reasoning effort"
              disabled={inputDisabled}
              value={shownEffort}
              onchange={(event) => (pickedEffort = event.currentTarget.value || null)}
            >
              {#if effortIsBare}
                <option value=""></option>
              {/if}
              {#each effortChoices as effort (effort)}
                <option value={effort}>{effort}</option>
              {/each}
            </select>
          {/if}

          {#if armed}
            <span class="c2-armed" data-conversation-picker-armed>next message</span>
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
            class={`chat-send${running ? " stop" : text.trim() ? " on" : ""}`}
            class:is-sending={sendIsInFlight}
            data-conversation-send={running ? undefined : true}
            data-conversation-stop={running ? true : undefined}
            data-conversation-sending={sendIsInFlight ? true : undefined}
            aria-busy={sendIsInFlight ? "true" : undefined}
            disabled={running ? false : inputDisabled || !text.trim()}
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
    </div>
  </div>

  {#if errorNote}
    <div class="chat-receipt" role="alert" data-conversation-error>{errorNote}</div>
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
  .c2-pick-select {
    min-width: 0;
    max-width: calc(var(--space-page-tail) * 1.25);
    background: transparent;
    border: var(--border-hairline) solid transparent;
    border-radius: var(--radius-sm);
    color: var(--text-muted);
    cursor: pointer;
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    padding: var(--space-1);
    text-overflow: ellipsis;
  }
  .c2-pick-select:hover { border-color: var(--border-color); color: var(--text-strong); }
  .c2-pick-select.on { color: var(--accent-bright); border-color: var(--border-color); }
  .c2-pick-select:disabled { cursor: default; opacity: 0.5; }
  /* No value to show: the control keeps its capability and gives up its width, down to
     the arrow that says there is something here to pick. */
  .c2-pick-select.is-bare {
    width: var(--space-5);
    min-width: var(--space-5);
    padding-inline: 0;
    text-indent: var(--space-1);
  }
  .c2-armed {
    color: var(--accent-bright);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
  }
  .c2-fate {
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
  }
</style>

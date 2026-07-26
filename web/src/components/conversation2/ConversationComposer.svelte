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
   */
  import { tick } from "svelte";
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
  } from "../../lib/conversation2/composer";
  import type { RunValues } from "../../lib/conversation2/composer";
  import type {
    BackendModel,
    ConversationBackendKey,
    PermissionAskOption,
    PromptDeliveryMode
  } from "../../lib/conversation2/wire";

  let {
    backendKey = null,
    running = false,
    ask = null,
    askNote = null,
    current = { model: null, reasoningEffort: null },
    models = [],
    effortOptions = [],
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
  }

  function onKeydown(event: KeyboardEvent): void {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send();
    }
  }

  /** Aim this message at a skill.
   *
   * A skill is asked for by the message starting with a slash, so this puts one there and
   * hands the box straight back with the cursor at the end. What skill, and everything
   * after it, is the person's to type — this only saves them reaching for the key and
   * knowing that a leading slash is what does it.
   */
  async function aimAtASkill(): Promise<void> {
    if (!text.startsWith("/")) text = `/${text}`;
    await tick();
    const input = inputElement;
    if (input === null) return;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }
</script>

<section class="c2-composer" data-conversation2-composer>
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
      data-conversation2-box
      data-conversation2-taken-over={takenOver ? "true" : undefined}
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

      <textarea
        class="chat-ta"
        data-conversation2-input
        rows="1"
        placeholder={livePlaceholder}
        bind:this={inputElement}
        bind:value={text}
        disabled={inputDisabled}
        onkeydown={onKeydown}
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
          <button
            type="button"
            class="chat-slash"
            data-conversation2-slash
            aria-label="Aim this message at a skill"
            title="Aim this message at a skill"
            disabled={inputDisabled}
            onclick={() => void aimAtASkill()}
          >/</button>

          <select
            class="c2-pick-select"
            class:on={pickedModel !== null}
            data-conversation2-picker-model
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
              data-conversation2-picker-effort
              data-conversation2-picker-effort-bare={effortIsBare ? "true" : undefined}
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
            <span class="c2-armed" data-conversation2-picker-armed>next message</span>
          {/if}

          {#if running}
            <div class="chat-seg" data-conversation2-delivery role="group" aria-label="Delivery">
              {#each deliveryOptions as option (option.mode)}
                <button
                  type="button"
                  class:on={mode === option.mode}
                  data-conversation2-delivery-mode={option.mode}
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
            data-conversation2-send={running ? undefined : true}
            data-conversation2-stop={running ? true : undefined}
            data-conversation2-sending={sendIsInFlight ? true : undefined}
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
    <div class="chat-receipt" role="alert" data-conversation2-error>{errorNote}</div>
  {:else if fateNote}
    <div class="c2-fate" data-conversation2-fate>{fateNote}</div>
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

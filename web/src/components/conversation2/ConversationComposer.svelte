<script lang="ts">
  /** The one place text goes in, and the one place a change to the run rides out on.
   *
   * What is typed here is this component's own and nothing arriving from the server ever
   * touches it. A model or effort picked here changes nothing until the next message
   * carries it; closing the picker without sending leaves the conversation exactly as it
   * was. When an ask is waiting the whole box gives way to it, because until it is
   * answered there is nothing else this composer could usefully do.
   */
  import PermissionAskActions from "./PermissionAskActions.svelte";
  import PermissionAskCard from "./PermissionAskCard.svelte";
  import {
    askPlaceholder,
    deliveryOptionsFor,
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
  let sending = $state(false);

  let deliveryOptions = $derived(deliveryOptionsFor(backendKey));
  let effectiveMode = $derived<PromptDeliveryMode>(running ? mode : "run_when_free");
  let picked = $derived<RunValues>({ model: pickedModel, reasoningEffort: pickedEffort });
  let armed = $derived(hasArmedChange(current, picked, effectiveMode));
  let takenOver = $derived(ask !== null);
  let inputDisabled = $derived(disabled || takenOver || sending);
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
  let effortChoices = $derived(
    shownEffort !== "" && !effortOptions.includes(shownEffort)
      ? [shownEffort, ...effortOptions]
      : effortOptions
  );

  // What the chosen model really is, when the catalog says — an alias and the version it
  // reaches. A native select has nowhere to put a second line, so it is the tooltip.
  let modelTitle = $derived.by(() => {
    const value = shownModel === "" ? null : shownModel;
    const name = modelDisplayName(models, value) ?? "the backend's own model";
    const detail = modelDetail(models, value);
    return detail === null ? name : `${name} — ${detail}`;
  });

  async function send(): Promise<void> {
    const trimmed = text.trim();
    if (!trimmed || sending || inputDisabled) return;
    sending = true;
    try {
      const delivered = await onSend(trimmed, effectiveMode, picked);
      if (!delivered) return;
      text = "";
      // The change rode out with the message, so it is no longer pending: the selects
      // fall back to showing what the conversation now runs on.
      pickedModel = null;
      pickedEffort = null;
    } finally {
      sending = false;
    }
  }

  function onKeydown(event: KeyboardEvent): void {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send();
    }
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
              data-conversation2-picker-effort
              aria-label="Reasoning effort"
              disabled={inputDisabled}
              value={shownEffort}
              onchange={(event) => (pickedEffort = event.currentTarget.value || null)}
            >
              {#if shownEffort === ""}
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
            data-conversation2-send={running ? undefined : true}
            data-conversation2-stop={running ? true : undefined}
            disabled={running ? false : inputDisabled || !text.trim()}
            title={running ? "Stop the turn — press Enter to send instead" : "Send"}
            aria-label={running ? "Stop the turn" : "Send"}
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

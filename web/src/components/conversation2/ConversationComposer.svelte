<script lang="ts">
  /** The one place text goes in, and the one place a change to the run rides out on.
   *
   * What is typed here is this component's own and nothing arriving from the server ever
   * touches it. A model or effort picked here changes nothing until the next message
   * carries it; closing the picker without sending leaves the conversation exactly as it
   * was. When an ask is waiting the whole box gives way to it, because until it is
   * answered there is nothing else this composer could usefully do.
   */
  import PermissionAskCard from "./PermissionAskCard.svelte";
  import {
    askPlaceholder,
    deliveryOptionsFor,
    hasArmedChange,
    modelDisplayName
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
  let pickerOpen = $state(false);
  let sending = $state(false);

  let deliveryOptions = $derived(deliveryOptionsFor(backendKey));
  let effectiveMode = $derived<PromptDeliveryMode>(running ? mode : "run_when_free");
  let picked = $derived<RunValues>({ model: pickedModel, reasoningEffort: pickedEffort });
  let armed = $derived(hasArmedChange(current, picked, effectiveMode));
  let takenOver = $derived(ask !== null);
  let inputDisabled = $derived(disabled || takenOver || sending);
  let livePlaceholder = $derived(takenOver ? askPlaceholder(ask) : placeholder);
  let modelLabel = $derived(
    modelDisplayName(models, pickedModel ?? current.model) ?? "the backend's own model"
  );
  let effortLabel = $derived(pickedEffort ?? current.reasoningEffort);
  let pickerLabel = $derived(
    [modelLabel, effortLabel, armed ? "next message" : null]
      .filter((part) => part)
      .join(" · ")
  );

  function abandonPicker(): void {
    pickedModel = null;
    pickedEffort = null;
    pickerOpen = false;
  }

  async function send(): Promise<void> {
    const trimmed = text.trim();
    if (!trimmed || sending || inputDisabled) return;
    sending = true;
    try {
      const delivered = await onSend(trimmed, effectiveMode, picked);
      if (!delivered) return;
      text = "";
      // The change rode out with the message, so it is no longer pending.
      pickedModel = null;
      pickedEffort = null;
      pickerOpen = false;
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
      data-conversation2-box
      data-conversation2-taken-over={takenOver ? "true" : undefined}
      role="group"
      aria-label="Conversation composer"
    >
      <textarea
        class="chat-ta"
        data-conversation2-input
        rows="1"
        placeholder={livePlaceholder}
        bind:value={text}
        disabled={inputDisabled}
        onkeydown={onKeydown}
      ></textarea>

      {#if takenOver && ask}
        <PermissionAskCard
          ask={ask}
          busy={disabled}
          note={askNote}
          onAnswer={(optionId) => onAnswer?.(optionId)}
          onCancelTurn={() => onCancelTurn?.()}
        />
      {:else}
        <div class="chat-foot">
          <button
            type="button"
            class="c2-picker-btn"
            class:on={armed}
            data-conversation2-picker-toggle
            aria-expanded={pickerOpen}
            onclick={() => (pickerOpen = !pickerOpen)}
          >
            {pickerLabel}
          </button>

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
        </div>
      {/if}

      {#if pickerOpen && !takenOver}
        <div class="c2-picker" data-conversation2-picker>
          <label class="c2-picker-field">
            <span>model</span>
            <select
              data-conversation2-picker-model
              value={pickedModel ?? current.model ?? ""}
              onchange={(event) => (pickedModel = event.currentTarget.value || null)}
            >
              <option value="">the backend's own model</option>
              {#each models as model (model.model_id)}
                <option value={model.model_id}>{model.display_name ?? model.model_id}</option>
              {/each}
            </select>
          </label>
          {#if effortOptions.length > 0}
            <label class="c2-picker-field">
              <span>reasoning effort</span>
              <select
                data-conversation2-picker-effort
                value={pickedEffort ?? current.reasoningEffort ?? ""}
                onchange={(event) => (pickedEffort = event.currentTarget.value || null)}
              >
                <option value="">the backend's own effort</option>
                {#each effortOptions as effort (effort)}
                  <option value={effort}>{effort}</option>
                {/each}
              </select>
            </label>
          {/if}
          <div class="c2-picker-foot">
            <span class="c2-picker-hint">
              {armed ? "rides the next message" : "nothing changes until you send"}
            </span>
            <button type="button" data-conversation2-picker-abandon onclick={abandonPicker}>
              Leave as it is
            </button>
          </div>
        </div>
      {/if}
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
  .c2-picker-btn {
    background: transparent;
    border: 0;
    border-radius: var(--radius-sm);
    color: var(--text-faintest);
    cursor: pointer;
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
    padding: var(--space-1) var(--space-2);
  }
  .c2-picker-btn:hover { color: var(--text-muted); background: var(--surface-overlay); }
  .c2-picker-btn.on { color: var(--accent-bright); }
  .c2-picker {
    display: grid;
    gap: var(--space-2);
    margin-top: var(--space-2);
    padding-top: var(--space-2);
    border-top: var(--border-hairline) solid var(--border-color);
  }
  .c2-picker-field {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
  }
  .c2-picker-field select {
    flex: 1;
    min-width: 0;
    background: var(--surface-raised);
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-sm);
    color: var(--text-default);
    font: inherit;
    padding: var(--space-1) var(--space-2);
  }
  .c2-picker-foot {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
  }
  .c2-picker-hint { color: var(--text-faintest); font-family: var(--font-mono); font-size: var(--type-xs); }
  .c2-picker-foot button {
    background: transparent;
    border: 0;
    color: var(--text-faint);
    cursor: pointer;
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    padding: 0;
  }
  .c2-picker-foot button:hover { color: var(--text-strong); }
  .c2-fate {
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
  }
</style>

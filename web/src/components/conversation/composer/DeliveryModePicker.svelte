<script lang="ts">
  import type { PromptDeliveryMode } from "../../../lib/conversation/wire";
  import ListboxPicker, {
    type ListboxPickerController,
    type ListboxPickerItem
  } from "../ListboxPicker.svelte";

  let {
    value,
    modes = ["steer", "queue", "send_now"],
    disabled = false,
    onChoose
  }: {
    value: PromptDeliveryMode;
    /** What the running turn can do with a message; Steer only when it can take one. */
    modes?: readonly PromptDeliveryMode[];
    disabled?: boolean;
    onChoose: (mode: PromptDeliveryMode) => void;
  } = $props();

  /** Each mode says what it will do to the turn that is running, because that is the whole
   *  of the difference between them. */
  const everyChoice: readonly (ListboxPickerItem & { value: PromptDeliveryMode; hint: string })[] = [
    { value: "steer", name: "Steer", hint: "Add it to the turn that is running" },
    { value: "queue", name: "Queue", hint: "Send it when this turn ends" },
    { value: "send_now", name: "Send now", hint: "Stop this turn and run it next" }
  ];
  let choices = $derived(everyChoice.filter((choice) => modes.includes(choice.value)));
  let picker = $state<ListboxPickerController>(null!);
  let selected = $derived(choices.find((choice) => choice.value === value) ?? choices[0] ?? everyChoice[0]);

  function choose(next: string): void {
    if (disabled) return;
    onChoose(next as PromptDeliveryMode);
    picker.close();
  }
</script>

<ListboxPicker
  chevron="drawn"
  items={choices}
  selectedValue={value}
  {disabled}
  label={`Message delivery mode: ${selected.name}`}
  align="right"
  bind:controller={picker}
  attributes={{ "data-conversation-send-mode": "" }}
  triggerAttributes={{ "data-conversation-send-mode-trigger": "" }}
  panelAttributes={{ "data-conversation-send-mode-panel": "" }}
  optionAttributes={(choice) => ({ "data-conversation-send-mode-choice": choice.value })}
  onChoose={choose}
>
  {#snippet triggerContent(open)}
    <span class="delivery-mode-face">{selected.name}</span>
  {/snippet}
  {#snippet optionContent(choice, isSelected)}
    <span class="delivery-mode-choice">
      <span class="delivery-mode-name">{choice.name}</span>
      <span class="delivery-mode-hint">{everyChoice.find((each) => each.value === choice.value)?.hint}</span>
    </span>
  {/snippet}
</ListboxPicker>

<style>
  .delivery-mode-face { white-space: nowrap; }
  .delivery-mode-choice { display: grid; gap: 2px; min-width: 0; }
  .delivery-mode-name { color: var(--text-strong); }
  .delivery-mode-hint { color: var(--tone-tertiary); font-size: var(--type-small); white-space: nowrap; }
  /* Closed, the mode is a control and reads as one: a filled pill with its chevron, in the
   * primary tone, so it never runs into the model's plain label beside it ("gpt-6.1-sol high
   * Steer"). 28px tall, the send round's height, on the composer's row. */
  :global([data-conversation-composer] .chat-foot [data-conversation-send-mode] > .listbox-picker-trigger) {
    height: 28px;
    min-height: 28px;
    gap: 2px;
    padding-inline: var(--space-2) var(--space-1);
    background: var(--surface-selected);
    border-color: transparent;
    color: var(--tone-primary);
  }
  :global([data-conversation-composer] .chat-foot [data-conversation-send-mode] > .listbox-picker-trigger:hover:not(:disabled)),
  :global([data-conversation-composer] .chat-foot [data-conversation-send-mode] > .listbox-picker-trigger.is-open) {
    background: var(--surface-selected);
    background-image: var(--interaction-hover);
    color: var(--tone-strong);
  }
  :global([data-conversation-composer] .chat-foot [data-conversation-send-mode] .listbox-picker-chevron-drawn) {
    display: block;
    width: 14px;
    height: 14px;
  }
  /* The panel opens from the composer's right edge, 8px above the box, like the model
   * picker's, and floats over the queue tray and the task pill on the raised plane rather than
   * moving them (ConversationComposer). */
  :global(.chat-box .chat-submit [data-conversation-send-mode]) { position: static; }
  :global(.chat-box .chat-submit [data-conversation-send-mode] > .listbox-picker-panel.compact) {
    left: auto;
    right: 0;
    bottom: calc(100% + var(--space-2));
    width: min(264px, calc(100vw - var(--space-4) * 2));
  }
</style>

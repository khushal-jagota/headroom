<script lang="ts">
  import type { PromptDeliveryMode } from "../../../lib/conversation/wire";
  import { DELIVERY_MODE_CHOICES, deliveryModeChoices } from "./deliveryModes";
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

  let choices = $derived(deliveryModeChoices(modes));
  let picker = $state<ListboxPickerController>(null!);
  let selected = $derived(choices.find((choice) => choice.value === value) ?? choices[0] ?? DELIVERY_MODE_CHOICES[0]);

  function choose(next: string): void {
    if (disabled) return;
    onChoose(next as PromptDeliveryMode);
    picker.close();
  }
</script>

<span class="delivery-mode-host">
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
  optionAttributes={(choice) => {
    const meaning = DELIVERY_MODE_CHOICES.find((each) => each.value === choice.value)?.meaning;
    return {
      "data-conversation-send-mode-choice": choice.value,
      "aria-label": meaning ? `${choice.name}: ${meaning}` : choice.name,
      title: meaning
    };
  }}
  onChoose={choose}
>
  {#snippet triggerContent(open)}
    <span class="delivery-mode-face">{selected.name}</span>
  {/snippet}
  {#snippet optionContent(choice, isSelected)}
    <span class="delivery-mode-name">{choice.name}</span>
  {/snippet}
</ListboxPicker>
</span>

<style>
  .delivery-mode-host { display: contents; }
  .delivery-mode-face { white-space: nowrap; }
  .delivery-mode-name { white-space: nowrap; }
  /* Closed, the mode is the one filled chip on the row, because it changes what Send does:
   * 28px, 8px round, the control fill, the mode in 500 t1 and a drawn chevron. It stands apart
   * from the model's plain words beside it by its fill and weight; no edge is drawn. */
  :global([data-conversation-composer] .chat-foot [data-conversation-send-mode] > .listbox-picker-trigger) {
    gap: 4px;
    padding-inline: 10px 6px;
    background: var(--cv-control);
    color: var(--cv-t1);
    font-weight: var(--cv-weight-label);
  }
  :global([data-conversation-composer] .chat-foot [data-conversation-send-mode] > .listbox-picker-trigger:hover:not(:disabled)),
  :global([data-conversation-composer] .chat-foot [data-conversation-send-mode] > .listbox-picker-trigger.is-open) {
    background: var(--cv-control-hover);
    color: var(--cv-t1);
  }
  :global([data-conversation-composer] .chat-foot [data-conversation-send-mode] .listbox-picker-chevron-drawn) {
    display: block;
    width: 14px;
    height: 14px;
    color: var(--cv-t3);
  }
  /* The menu is a native dropdown: anchored on its own chip, right edges aligned, 6px over
   * it. Three short labels: it is as narrow as they are. */
  :global(.chat-box .chat-submit [data-conversation-send-mode] > .listbox-picker-panel.compact) {
    left: auto;
    right: 0;
    bottom: calc(100% + 6px);
    width: 148px;
  }
</style>

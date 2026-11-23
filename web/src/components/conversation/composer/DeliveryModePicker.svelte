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
  chevron="none"
  drawnTick
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
  /* The chip is one of the composer's run chips (app.css). Its menu is a native dropdown:
     anchored on its own chip, right edges aligned, its bottom 8px above the composer's top
     edge, as wide as its longest label (its rows: ListboxPicker). */
  :global(.chat-box .chat-submit [data-conversation-send-mode] > .listbox-picker-panel.compact) {
    left: auto;
    right: 0;
    bottom: calc(100% + var(--cv-s2) + var(--c2-menu-lift, 0px));
    width: max-content;
    min-width: 0;
  }
</style>

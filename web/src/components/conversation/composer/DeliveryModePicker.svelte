<script lang="ts">
  import type { PromptDeliveryMode } from "../../../lib/conversation/wire";
  import ListboxPicker, {
    type ListboxPickerController,
    type ListboxPickerItem
  } from "../ListboxPicker.svelte";

  let {
    value,
    disabled = false,
    onChoose
  }: {
    value: PromptDeliveryMode;
    disabled?: boolean;
    onChoose: (mode: PromptDeliveryMode) => void;
  } = $props();

  const choices: readonly ListboxPickerItem[] = [
    { value: "steer", name: "Steer" },
    { value: "queue", name: "Queue" },
    { value: "send_now", name: "Send now" }
  ];
  let picker = $state<ListboxPickerController>(null!);
  let selected = $derived(choices.find((choice) => choice.value === value) ?? choices[0]);

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
  {#snippet optionContent(choice, selected)}
    <span>{choice.name}</span>
  {/snippet}
</ListboxPicker>

<style>
  .delivery-mode-face { white-space: nowrap; }
</style>

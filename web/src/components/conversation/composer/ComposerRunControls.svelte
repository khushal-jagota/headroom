<script lang="ts">
  import UnifiedModelPicker from "../UnifiedModelPicker.svelte";
  import type {
    ComposerRunControlIntents,
    ComposerRunControlsView
  } from "../../../lib/conversation/runControls/contracts";

  let {
    view,
    intents
  }: {
    view: ComposerRunControlsView;
    intents: ComposerRunControlIntents;
  } = $props();
</script>

<UnifiedModelPicker
  view={view.picker}
  snapshots={view.pickerSource.backends}
  models={view.pickerSource.models}
  backendEffortOptions={view.pickerSource.backendEffortOptions}
  disabled={view.disabled}
  attributes={{ "data-conversation-picker-model": "" }}
  afterChoose={() => {}}
  onChooseBackend={(backend) => intents.chooseBackend(backend)}
  onChooseModel={intents.chooseModel}
  onChooseReasoningEffort={intents.chooseReasoningEffort}
/>

{#if view.delivery}
  <div class="chat-seg" data-conversation-delivery role="group" aria-label="Delivery">
    {#each view.delivery.options as option (option.mode)}
      <button
        type="button"
        class:on={view.delivery.selected === option.mode}
        data-conversation-delivery-mode={option.mode}
        aria-pressed={view.delivery.selected === option.mode}
        title={option.description}
        onclick={() => intents.chooseDeliveryMode(option.mode)}
      >{option.label}</button>
    {/each}
  </div>
{/if}

<button
  type="button"
  class={`chat-send${view.submit.action === "stop" ? " stop" : view.submit.active ? " on" : ""}`}
  class:is-sending={view.submit.sending}
  data-conversation-send={view.submit.action === "send" ? true : undefined}
  data-conversation-stop={view.submit.action === "stop" ? true : undefined}
  data-conversation-sending={view.submit.sending ? true : undefined}
  aria-busy={view.submit.sending ? "true" : undefined}
  disabled={view.submit.disabled}
  title={view.submit.title}
  aria-label={view.submit.ariaLabel}
  onclick={() => (view.submit.action === "stop" ? intents.stop() : intents.send())}
>{view.submit.action === "stop" ? "■" : "↑"}</button>

<script lang="ts">
  import UnifiedModelPicker from "../UnifiedModelPicker.svelte";
  import DeliveryModePicker from "./DeliveryModePicker.svelte";
  import type {
    ComposerRunControlIntents,
    ComposerRunControlsView
  } from "../../../lib/conversation/runControls/contracts";

  let {
    view,
    intents,
    snapshots = $bindable()
  }: {
    view: ComposerRunControlsView;
    intents: ComposerRunControlIntents;
    snapshots: readonly import("../../../lib/conversation/wire").BackendSnapshot[];
  } = $props();
</script>

<UnifiedModelPicker
  view={view.picker}
  bind:snapshots
  models={view.pickerSource.models}
  backendEffortOptions={view.pickerSource.backendEffortOptions}
  showUsage
  disabled={view.disabled}
  attributes={{ "data-conversation-picker-model": "" }}
  afterChoose={() => {}}
  onChooseBackend={(backend) => intents.chooseBackend(backend)}
  onChooseModel={intents.chooseModel}
/>

<div class="chat-submit">
  <DeliveryModePicker
    value={view.deliveryMode}
    disabled={view.disabled}
    onChoose={intents.chooseDeliveryMode}
  />
  {#if view.showStop}
    <button
      type="button"
      class="chat-send stop"
      data-conversation-stop={true}
      title="Stop the turn"
      aria-label="Stop the turn"
      onclick={intents.stop}
    >■</button>
  {/if}
  <button
    type="button"
    class={`chat-send${view.submit.active ? " on" : ""}`}
    class:is-sending={view.submit.sending}
    data-conversation-send={true}
    data-conversation-sending={view.submit.sending ? true : undefined}
    aria-busy={view.submit.sending ? "true" : undefined}
    disabled={view.submit.disabled}
    title={view.submit.title}
    aria-label={view.submit.ariaLabel}
    onclick={intents.send}
  >↑</button>
</div>

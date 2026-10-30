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
    snapshots = $bindable(),
    beforeSubmit,
    onRetry
  }: {
    onRetry?: () => Promise<void>;
    view: ComposerRunControlsView;
    intents: ComposerRunControlIntents;
    snapshots: readonly import("../../../lib/conversation/wire").BackendSnapshot[];
    /** The microphone, drawn before the turn and send actions. */
    beforeSubmit?: import("svelte").Snippet;
  } = $props();
</script>

<UnifiedModelPicker
  displaySelection={view.displaySelection}
  readError={view.readError}
  {onRetry}
  chevron="drawn"
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

<!-- What is on the right is what there is to do. The microphone leads the turn actions,
     and sending appears when there is something to send: a composer with
     nothing in it offers no Send, greyed out or otherwise. How the message will be
     delivered is a standing choice rather than an action, so it leads the group. -->
<div class="chat-submit">
  <DeliveryModePicker
    value={view.deliveryMode}
    disabled={view.disabled}
    onChoose={intents.chooseDeliveryMode}
  />
  {#if beforeSubmit}{@render beforeSubmit()}{/if}
  {#if view.showStop}
    <button
      type="button"
      class="chat-send stop"
      data-conversation-stop={true}
      title="Stop the turn"
      aria-label="Stop the turn"
      onclick={intents.stop}
    ><span class="chat-send-fill" aria-hidden="true"></span></button>
  {/if}
  {#if view.submit.active || view.submit.sending}
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
    ><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7" /></svg></button>
  {/if}
</div>

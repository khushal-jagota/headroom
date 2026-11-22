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
  readError={view.readError}
  {onRetry}
  chevron="drawn"
  view={view.picker}
  bind:snapshots
  models={view.pickerSource.models}
  backendEffortOptions={view.pickerSource.backendEffortOptions}
  showUsage
  tonedMark
  layout="composer"
  disabled={view.disabled}
  attributes={{ "data-conversation-picker-model": "" }}
  afterChoose={() => {}}
  onChooseBackend={(backend) => intents.chooseBackend(backend)}
  onChooseModel={intents.chooseModel}
/>

<!-- What is on the right is what there is to do. How the message will be delivered is a
     standing choice rather than an action, so it leads the group, and it is only there while
     a turn runs: with nothing running a message starts the next turn whatever it says. The
     row always ends in one 32px round: Send, quiet until there is something to send; while
     a turn runs, Stop, with Send beside it once there is a draft. Stop stays reachable while
     writing, because a draft is no reason to lose the way to end the turn. -->
<div class="chat-submit">
  {#if view.showDeliveryMode}
    <DeliveryModePicker
      value={view.deliveryMode}
      modes={view.deliveryModes}
      disabled={view.disabled}
      onChoose={intents.chooseDeliveryMode}
    />
  {/if}
  {#if beforeSubmit}{@render beforeSubmit()}{/if}
  {#if view.showStop}
    <button
      type="button"
      class="chat-send stop"
      data-conversation-stop={true}
      title="Stop turn"
      aria-label="Stop turn"
      onclick={intents.stop}
    ><span class="chat-send-fill" aria-hidden="true"></span></button>
  {/if}
  <!-- Sending leaves the cursor in the field, so a phone's keyboard stays up for the reply. -->
  {#if view.submit.active || view.submit.sending || !view.showStop}
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
      onmousedown={(event) => event.preventDefault()}
      onclick={intents.send}
    ><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7" /></svg></button>
  {/if}
</div>

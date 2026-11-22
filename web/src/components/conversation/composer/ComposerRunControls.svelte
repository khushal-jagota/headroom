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
    asking = false,
    askCommitReady = false,
    onRetry
  }: {
    /** While an ask is the composer: no mode chip and no Stop here (Stop turn is the ask's
     *  footer pill). v10 (4): no confirm pill either; the slot keeps the round send, which
     *  sends what is written (an own answer, or a message that waits for the turn). */
    asking?: boolean;
    /** A several-answer question with something chosen and nothing written: send goes on. */
    askCommitReady?: boolean;
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
  chevron="none"
  view={view.picker}
  bind:snapshots
  models={view.pickerSource.models}
  backendEffortOptions={view.pickerSource.backendEffortOptions}
  showUsage
  tonedMark
  disabled={view.disabled}
  attributes={{ "data-conversation-picker-model": "" }}
  afterChoose={() => {}}
  onChooseBackend={(backend) => intents.chooseBackend(backend)}
  onChooseModel={intents.chooseModel}
/>

<!-- What is on the right is what there is to do. How the message will be delivered is a
     standing choice rather than an action, so it leads the group, and it is only there while
     a turn runs: with nothing running a message starts the next turn whatever it says. The
     row always ends in one 36px round: Send, quiet until there is something to send; while
     a turn works, Stop, with Send beside it once there is a draft. While an ask is the
     composer, the send slot keeps the round send (v10: no confirm pill), and the turn is
     stopped from the ask footer's "Stop turn": there is still exactly one way to stop. -->
<div class="chat-submit">
  {#if view.showDeliveryMode && !asking}
    <DeliveryModePicker
      value={view.deliveryMode}
      modes={view.deliveryModes}
      disabled={view.disabled}
      onChoose={intents.chooseDeliveryMode}
    />
  {/if}
  {#if beforeSubmit}{@render beforeSubmit()}{/if}
  {#if !asking && view.showStop}
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
  {#if asking || view.submit.active || view.submit.sending || !view.showStop}
    <button
      type="button"
      class={`chat-send${view.submit.active || askCommitReady ? " on" : ""}`}
      class:chat-ask-send={asking}
      class:is-sending={view.submit.sending}
      data-conversation-send={true}
      data-conversation-sending={view.submit.sending ? true : undefined}
      aria-busy={view.submit.sending ? "true" : undefined}
      disabled={view.submit.disabled && !askCommitReady}
      title={view.submit.title}
      aria-label={view.submit.ariaLabel}
      onmousedown={(event) => event.preventDefault()}
      onclick={intents.send}
    ><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7" /></svg></button>
  {/if}
</div>

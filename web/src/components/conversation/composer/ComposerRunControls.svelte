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
    askPrimary = null,
    onRetry
  }: {
    /** While an ask is the composer (v4): its primary pill in the send slot ("Next",
     *  "Send answers", "Allow"), which commits the ask. No mode chip and no Stop beside it. */
    askPrimary?: { label: string; ready: boolean; onCommit: () => void;
      /** v7: a declining answer (Deny) as a quiet Button before the primary. */
      decline?: { label: string; optionId: string; ready: boolean; onDecline: () => void } | null } | null;
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
     composer, the send slot holds the ask's primary pill instead (v4), and the turn is
     stopped from the ask header's "Stop turn": there is still exactly one way to stop. -->
<div class="chat-submit">
  {#if view.showDeliveryMode && askPrimary === null}
    <DeliveryModePicker
      value={view.deliveryMode}
      modes={view.deliveryModes}
      disabled={view.disabled}
      onChoose={intents.chooseDeliveryMode}
    />
  {/if}
  {#if beforeSubmit}{@render beforeSubmit()}{/if}
  {#if askPrimary !== null && askPrimary.decline}
    <button
      type="button"
      class="chat-ask-quiet"
      data-conversation-ask-decline
      data-conversation-ask-action={askPrimary.decline.optionId}
      disabled={!askPrimary.decline.ready}
      onmousedown={(event) => event.preventDefault()}
      onclick={() => askPrimary?.decline?.ready && askPrimary.decline.onDecline()}
    >{askPrimary.decline.label}</button>
  {/if}
  {#if askPrimary !== null}
    <button
      type="button"
      class="chat-ask-primary"
      data-conversation-ask-primary
      disabled={!askPrimary.ready}
      onmousedown={(event) => event.preventDefault()}
      onclick={() => askPrimary?.ready && askPrimary.onCommit()}
    >{askPrimary.label}</button>
  {:else if view.showStop}
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
  {#if askPrimary === null && (view.submit.active || view.submit.sending || !view.showStop)}
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

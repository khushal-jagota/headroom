<script lang="ts">
  import DeliveryModePicker from "./DeliveryModePicker.svelte";
  import type {
    ComposerRunControlIntents,
    ComposerRunControlsView
  } from "../../../lib/conversation/runControls/contracts";

  let {
    view,
    intents,
  }: {
    view: ComposerRunControlsView;
    intents: ComposerRunControlIntents;
  } = $props();
</script>

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
    ><span class="chat-send-fill" aria-hidden="true"></span></button>
  {/if}
    <button
      type="button"
      class={`chat-send${view.submit.active ? " on" : ""}`}
      class:is-sending={view.submit.sending}
      data-conversation-send={true}
      data-conversation-sending={view.submit.sending ? true : undefined}
      aria-busy={view.submit.sending ? "true" : undefined}
      disabled={view.submit.disabled || !view.submit.active}
      title={view.submit.title}
      aria-label={view.submit.ariaLabel}
      onclick={intents.send}
    ><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7" /></svg></button>
</div>

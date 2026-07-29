<script lang="ts">
  import BackendRail from "../BackendRail.svelte";
  import RunValuePicker from "../RunValuePicker.svelte";
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

<!-- Which backend is a question about the conversation rather than about this
     message, so it is drawn beside the models it decides rather than as a third
     pill in the footer. -->
{#snippet backendRail()}
  <BackendRail
    showing={view.backend.showing}
    locked={view.backend.locked}
    onChoose={intents.chooseBackend}
  />
{/snippet}

<RunValuePicker
  label="Model"
  choices={view.model.choices}
  value={view.model.value}
  searchable
  disabled={view.disabled}
  title={view.model.title}
  attributes={{ "data-conversation-picker-model": "" }}
  rail={view.backend.showing === null ? undefined : backendRail}
  onChoose={intents.chooseModel}
/>

{#if view.effort}
  <RunValuePicker
    label="Reasoning effort"
    choices={view.effort.choices}
    value={view.effort.value}
    disabled={view.disabled}
    title="Reasoning effort"
    attributes={{
      "data-conversation-picker-effort": "",
      "data-conversation-picker-effort-bare": view.effort.bare ? "true" : undefined
    }}
    onChoose={intents.chooseReasoningEffort}
  />
{/if}

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

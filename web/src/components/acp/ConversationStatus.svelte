<script lang="ts">
  import type {
    ProtocolRejectionViewState
  } from "../../lib/acp/conversationState";

  let {
    recoverableError,
    protocolRejections
  }: {
    recoverableError: string | null;
    protocolRejections: Readonly<Record<string, ProtocolRejectionViewState>>;
  } = $props();

  let protocolError = $derived(Object.values(protocolRejections).at(-1) ?? null);
  let previousProtocolSequence = $state<number | null>(null);
  let previousRecoverableError = $state<string | null>(null);
  let alertMessage = $state<string | null>(null);

  $effect(() => {
    const nextProtocolSequence = protocolError?.sequence ?? null;
    const nextRecoverableError = recoverableError;
    if (protocolError && nextProtocolSequence !== previousProtocolSequence) {
      alertMessage = protocolError.status;
    } else if (nextRecoverableError && nextRecoverableError !== previousRecoverableError) {
      alertMessage = nextRecoverableError;
    } else if (
      nextProtocolSequence !== previousProtocolSequence
      || nextRecoverableError !== previousRecoverableError
    ) {
      alertMessage = null;
    }
    previousProtocolSequence = nextProtocolSequence;
    previousRecoverableError = nextRecoverableError;
  });
</script>

{#if alertMessage}
  <span class="acp-alert" role="alert" aria-atomic="true" data-acp-alert>{alertMessage}</span>
{/if}

<style>
  .acp-alert {
    block-size: var(--border-hairline);
    clip-path: inset(50%);
    inline-size: var(--border-hairline);
    overflow: hidden;
    position: absolute;
    white-space: nowrap;
  }
</style>

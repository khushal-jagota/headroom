<script lang="ts">
  import type {
    CompactionViewState,
    ConversationConnection,
    DeepReadonly,
    ProtocolRejectionViewState
  } from "../../lib/acp/conversationState";
  import type { ConversationActivity } from "../../lib/acp/contracts";
  import type { UsageUpdate } from "@agentclientprotocol/sdk";

  let {
    connection,
    activity,
    usage,
    compactions,
    pendingPermission,
    recoverableError,
    protocolRejections
  }: {
    connection: ConversationConnection;
    activity: DeepReadonly<ConversationActivity> | null;
    usage: DeepReadonly<UsageUpdate> | null;
    compactions: Readonly<Record<string, CompactionViewState>>;
    pendingPermission: boolean;
    recoverableError: string | null;
    protocolRejections: Readonly<Record<string, ProtocolRejectionViewState>>;
  } = $props();

  let protocolError = $derived(Object.values(protocolRejections).at(-1) ?? null);
  let previousProtocolSequence = $state<number | null>(null);
  let previousRecoverableError = $state<string | null>(null);
  let alertMessage = $state<string | null>(null);
  let compacting = $derived(Object.values(compactions).some((item) => item.payload.state === "compacting"));
  let label = $derived.by(() => {
    if (protocolError) return protocolError.status;
    if (recoverableError) return recoverableError;
    if (["connecting", "open"].includes(connection.state)) return connection.state === "open" ? "loading" : "connecting";
    if (connection.state === "reset") return "loading";
    if (["closed", "error", "disposed"].includes(connection.state)) return connection.detail;
    if (pendingPermission) return "waiting for permission";
    if (compacting) return "compacting";
    return activity?.state?.replaceAll("_", " ") ?? "idle";
  });
  let usageLabel = $derived.by(() => {
    if (!usage) return "";
    const cost = usage.cost ? ` · ${usage.cost.amount} ${usage.cost.currency}` : "";
    return ` · ${usage.used} / ${usage.size} tokens${cost}`;
  });

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

<p class="acp-status" role="status" aria-live="polite" data-acp-status>
  <span>{label}</span><span class="acp-status-usage">{usageLabel}</span>
</p>
{#if alertMessage}
  <span class="acp-alert" role="alert" aria-atomic="true" data-acp-alert>{alertMessage}</span>
{/if}

<style>
  .acp-status {
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    margin: 0;
    padding: var(--space-1);
  }
  .acp-status-usage { color: var(--text-faintest); }
  .acp-alert {
    block-size: var(--border-hairline);
    clip-path: inset(50%);
    inline-size: var(--border-hairline);
    overflow: hidden;
    position: absolute;
    white-space: nowrap;
  }
</style>

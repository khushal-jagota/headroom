<script lang="ts">
  import type { ConversationSignal } from "../lib/conversationSignal";
  import StageMark from "./StageMark.svelte";

  let {
    signal,
    conversationId = null,
    class: extraClass = "",
    ...rest
  }: {
    signal: Partial<ConversationSignal> | null | undefined;
    conversationId?: string | null;
    class?: string;
    [key: string]: unknown;
  } = $props();
</script>

{#if signal?.working || signal?.message_for_owner}
  <span
    class={`conversation-signal-marks${extraClass ? ` ${extraClass}` : ""}`}
    data-conversation-id={conversationId ?? undefined}
    {...rest}
  >
    {#if signal.message_for_owner}
      <StageMark state="current-awaiting-approval" aria-label="Unread message" data-conversation-signal="message" />
    {:else if signal.working}
      <StageMark state="current-running" aria-label="Working" data-conversation-signal="working" />
    {/if}
  </span>
{/if}

<style>
  .conversation-signal-marks {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    flex: none;
  }
</style>

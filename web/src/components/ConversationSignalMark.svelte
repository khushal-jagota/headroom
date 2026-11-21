<script lang="ts">
  import { conversationSignalKind, type ConversationSignal } from "../lib/conversationSignal";
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
  const kind = $derived(conversationSignalKind(signal));
</script>

{#if kind}
  <span
    class={`conversation-signal-marks${extraClass ? ` ${extraClass}` : ""}`}
    data-conversation-id={conversationId ?? undefined}
    {...rest}
  >
    {#if kind === 'message'}
      <StageMark state="current-awaiting-approval" aria-label="Unread message" data-conversation-signal="message" />
    {:else if kind === 'working'}
      <StageMark state="current-running" aria-label="Working" data-conversation-signal="working" />
    {:else if kind === 'setting_up'}
      <StageMark state="current-running" aria-label="Setting up" data-conversation-signal="setting_up" />
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

<script lang="ts">
  import ConversationSignalMark from "./ConversationSignalMark.svelte";
  import type { ConversationSignal } from "../lib/conversationSignal";

  let {
    title,
    signal = undefined,
    conversationId = null,
    href = null,
    active = false,
    quiet = false,
    onclick = undefined,
    class: extraClass = "",
    ...rest
  }: {
    title: string;
    signal?: ConversationSignal;
    conversationId?: string | null;
    href?: string | null;
    active?: boolean;
    quiet?: boolean;
    onclick?: ((event: MouseEvent) => void) | undefined;
    class?: string;
    [key: string]: unknown;
  } = $props();
</script>

{#snippet content()}
  <span class="ticket-row-title">{title}</span>
  <ConversationSignalMark {signal} {conversationId} />

{/snippet}

{#if href === null}
  <button
    type="button"
    class={`ticket-row${extraClass ? ` ${extraClass}` : ""}`}
    class:ticket-row--active={active}
    class:ticket-row--quiet={quiet}
    {onclick}
    {...rest}
  >
    {@render content()}
  </button>
{:else}
  <a
    class={`ticket-row${extraClass ? ` ${extraClass}` : ""}`}
    class:ticket-row--active={active}
    class:ticket-row--quiet={quiet}
    {href}
    {onclick}
    {...rest}
  >
    {@render content()}
  </a>
{/if}

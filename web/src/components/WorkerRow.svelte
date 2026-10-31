<script lang="ts">
  import MachineActivityMark from "./machines/MachineActivityMark.svelte";
  import MachineIdentity from "./machines/MachineIdentity.svelte";
  import type { TicketLocale } from "../lib/ticketMachines";
  import type { ConversationSignal } from "../lib/conversationSignal";

  let {
    locale, machine = true,
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
    locale?: TicketLocale;
    machine?: boolean;
    title: string;
    signal?: Partial<ConversationSignal>;
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
  {#if locale && machine}<MachineIdentity {locale} />{/if}
  <span class="ticket-row-title">{title}</span>
  <MachineActivityMark {locale} {signal} {conversationId} completed={quiet} />

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

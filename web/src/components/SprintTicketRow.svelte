<script lang="ts">
  import MachineActivityMark from "./machines/MachineActivityMark.svelte";
  import MachineIdentity from "./machines/MachineIdentity.svelte";
  import type { TicketLocale } from "../lib/ticketMachines";
  import type { Priority } from "../lib/types";
  import PriorityTile from "./PriorityTile.svelte";
  import ConversationSignalMark from "./ConversationSignalMark.svelte";
  import type { ConversationSignal } from "../lib/conversationSignal";

  let {
    locale, ticketId,
    priority,
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
    // No priority when the row's own container already answers for it: inside a
    // Sprint Item the tile is repeated noise.
    locale?: TicketLocale;
    ticketId?: string;
    priority: Priority | null;
    title: string;
    signal?: ConversationSignal;
    conversationId?: string | null;
    href?: string | null;
    active?: boolean;
    quiet?: boolean;
    onclick?: (() => void) | undefined;
    class?: string;
    [key: string]: unknown;
  } = $props();
</script>

{#snippet content()}
  {#if priority}<PriorityTile {priority} />{/if}
  <span class="ticket-row-title">{title}</span>
  <MachineActivityMark {locale} {signal} {conversationId} completed={quiet} />

{/snippet}

<div class="machine-ticket-row">
{#if locale && ticketId}<MachineIdentity {locale} {ticketId} />{/if}
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
    {...rest}
  >
    {@render content()}
  </a>
{/if}

</div>
<style>.machine-ticket-row { display:flex; align-items:center; min-width:0; }.machine-ticket-row > :global(.ticket-row) { flex:1; min-width:0; }</style>

<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { machineRows } from "../lib/machines/visibility";
  const registry = machineRows();
  const machineRowKey = Symbol();
  let rowElement = $state<HTMLElement>();
  $effect(() => { const element=rowElement, machine=locale; if (element) untrack(() => registry?.set(machineRowKey,machine,Boolean(element.closest('.approved-rail')))); });
  onMount(() => () => registry?.delete(machineRowKey));
  import MachineActivityMark from "./machines/MachineActivityMark.svelte";
  import MachineIdentity from "./machines/MachineIdentity.svelte";
  import type { TicketLocale } from "../lib/ticketMachines";
  import type { ConversationSignal } from "../lib/conversationSignal";

  let {
    locale, ticketId,
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
    ticketId?: string;
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
  <span class="ticket-row-title">{title}</span>
  <MachineActivityMark {locale} {signal} {conversationId} completed={quiet} />

{/snippet}

<div bind:this={rowElement} class="machine-ticket-row">
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
    {onclick}
    {...rest}
  >
    {@render content()}
  </a>
{/if}

</div>
<style>.machine-ticket-row { display:flex; align-items:center; min-width:0; }.machine-ticket-row > :global(.ticket-row) { flex:1; min-width:0; }</style>

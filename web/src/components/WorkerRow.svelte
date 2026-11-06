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
    lead = undefined,
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
    /** A mark drawn before the title, in the row's own leading space. */
    lead?: import("svelte").Snippet;
    class?: string;
    [key: string]: unknown;
  } = $props();
</script>

{#snippet content()}
  {#if locale && machine}<MachineIdentity {locale} />{/if}
  {#if lead}<span class="ticket-row-lead">{@render lead()}</span>{/if}
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

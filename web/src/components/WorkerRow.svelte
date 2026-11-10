<script lang="ts">
  import MachineActivityMark from "./machines/MachineActivityMark.svelte";
  import MachineIdentity from "./machines/MachineIdentity.svelte";
  import DestinationIcon from "./DestinationIcon.svelte";
  import type { TicketLocale } from "../lib/ticketMachines";
  import type { ConversationSignal } from "../lib/conversationSignal";
  import type { AgentHoldFacts } from "../lib/workAttentionPresentation";

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
    approval = null,
    agentHold = undefined,
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
    /** Whose approval the Ticket waits on, drawn in the mark column as the Review icon. */
    approval?: "owner" | "agent" | null;
    /** Whether an agent holds the Ticket, for what a Mac Ticket says while the Mac is away. */
    agentHold?: AgentHoldFacts;
    class?: string;
    [key: string]: unknown;
  } = $props();
</script>

{#snippet content()}
  {#if locale && machine}<MachineIdentity {locale} />{/if}
  {#if lead}<span class="ticket-row-lead">{@render lead()}</span>{/if}
  <span class="ticket-row-title">{title}</span>
  <!-- The mark column says what matters most about the row (owner): the Review icon in the accent
       when it waits on your approval, before an unread message's dot; the icon greyed when it
       waits on an agent's approval and nothing else is showing; otherwise the dot or the
       working mark, or nothing. -->
  {#if approval === "owner"}
    <span class="conversation-signal-marks approval-mark approval-mark--owner" role="img" aria-label="Needs your approval" data-approval-mark="owner" data-conversation-id={conversationId ?? undefined}><DestinationIcon name="review" /></span>
  {:else if approval === "agent" && !signal?.message_for_owner && !signal?.working}
    <span class="conversation-signal-marks approval-mark approval-mark--agent" role="img" aria-label="Awaiting an agent's approval" data-approval-mark="agent" data-conversation-id={conversationId ?? undefined}><DestinationIcon name="review" /></span>
  {:else}
    <MachineActivityMark {locale} {signal} {conversationId} completed={quiet} {agentHold} />
  {/if}

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

<style>
  /* The approval mark: the Review icon at 14px, in the mark column. */
  .approval-mark { display:inline-flex; align-items:center; justify-content:center; flex:none; }
  .approval-mark :global(svg) { width:14px; height:14px; stroke-width:1.8; }
  .approval-mark--owner { color:var(--accent-bright); }
  .approval-mark--agent { color:var(--rail-tone-label); }
</style>

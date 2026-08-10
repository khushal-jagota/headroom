<script lang="ts">
  import type { TicketConversationHistoryEntry } from "../lib/types";

  const CURRENT_SELECTION = "__current__";

  let {
    history,
    activeConversationId,
    selectedPastConversationId = $bindable(null)
  }: {
    history: readonly TicketConversationHistoryEntry[];
    activeConversationId: string | null;
    selectedPastConversationId?: string | null;
  } = $props();

  let pastConversations = $derived(
    history.filter((entry) => entry.conversation_id !== activeConversationId)
  );

  function conversationLabel(entry: TicketConversationHistoryEntry): string {
    const timestamp = new Date(entry.created_at * 1000);
    const when = Number.isNaN(timestamp.valueOf())
      ? "Unknown date"
      : timestamp.toLocaleString([], {
          dateStyle: "medium",
          timeStyle: "short"
        });
    return `${when} · ${entry.conversation_id}`;
  }

  function selectConversation(event: Event): void {
    const value = (event.currentTarget as HTMLSelectElement).value;
    selectedPastConversationId = value === CURRENT_SELECTION ? null : value;
  }
</script>

{#if pastConversations.length > 0}
  <label class="ticket-conversation-history" data-ticket-conversation-history>
    <span>Conversation</span>
    <select
      aria-label="Ticket conversation"
      value={selectedPastConversationId ?? CURRENT_SELECTION}
      onchange={selectConversation}
    >
      <option value={CURRENT_SELECTION}>
        {activeConversationId === null ? "Current · new conversation" : "Current conversation"}
      </option>
      {#each pastConversations as entry (entry.conversation_id)}
        <option value={entry.conversation_id}>{conversationLabel(entry)}</option>
      {/each}
    </select>
  </label>
{/if}

<style>
  .ticket-conversation-history {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    min-width: 0;
    padding: 0 var(--space-4) var(--space-2);
    color: var(--text-faint);
    font-family: var(--font-ui);
    font-size: var(--type-xs);
  }

  .ticket-conversation-history span {
    flex: none;
    letter-spacing: var(--tracking-label);
    text-transform: uppercase;
  }

  .ticket-conversation-history select {
    min-width: 0;
    max-width: 100%;
    border: 0;
    background: transparent;
    color: var(--text-muted);
    font: inherit;
  }

  .ticket-conversation-history select:focus-visible {
    outline: var(--border-hairline) solid var(--accent-bright);
    outline-offset: 2px;
  }
</style>

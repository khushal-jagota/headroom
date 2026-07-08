<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson } from "../lib/api";
  import { resource } from "../lib/resources";
  import type { BoardResponse } from "../lib/types";
  import ErrorLine from "../components/ErrorLine.svelte";

  const board = resource<BoardResponse>("board", (signal) => fetchJson("/api/board", { signal }));
  let columns = $derived(board.data?.columns || []);
  let selectedTicketId = $state<string | null>(null);
  let collapsedStates = $state<string[]>([]);
  let allCards = $derived(columns.flatMap((column) => column.cards));
  let selectedCard = $derived(
    allCards.find((card) => card.id === selectedTicketId) || allCards[0] || null
  );

  function stateLabel(state: string): string {
    return state.replace(/_/g, " ");
  }

  function selectCard(ticketId: string): void {
    selectedTicketId = ticketId;
  }

  function isCollapsed(state: string): boolean {
    return collapsedStates.includes(state);
  }

  function toggleState(state: string): void {
    collapsedStates = isCollapsed(state)
      ? collapsedStates.filter((current) => current !== state)
      : [...collapsedStates, state];
  }

  function runtimeMarker(card: Record<string, any>): string | null {
    if (card.ticket_status === "agent_running_step") return "agent-running-step";
    if (card.ticket_status === "user_takeover") return "user-takeover";
    if (card.has_pending_proposal) return "pending-proposal";
    return null;
  }

  onDestroy(() => board.dispose());
</script>

<section class="board-screen" data-screen="board">
  {#if board.error}
    <ErrorLine error={board.error} />
  {:else if board.loading && !board.data}
    <div class="quiet-line">Loading board...</div>
  {:else}
    <div class="board-workspace-wrap">
      <div class="board-workspace-shell">
        <section class="board-workspace-left" aria-label="Board ticket tree">
          <h2 class="board-workspace-heading board-workspace-heading-row">
            <span>Refinement Tree</span>
          </h2>

          {#each columns as column}
            {@const collapsed = isCollapsed(column.state)}
            <section class="board-workspace-index-section" data-column={column.state}>
              <button
                aria-expanded={!collapsed}
                class="board-workspace-index-heading board-workspace-index-toggle"
                type="button"
                onclick={() => toggleState(column.state)}
              >
                <span class="board-workspace-index-heading-main">
                  <span class:board-workspace-index-chevron--collapsed={collapsed} class="board-workspace-index-chevron">▸</span>
                  <span>{stateLabel(column.state)}</span>
                </span>
                <span class="board-workspace-index-count">{column.cards.length}</span>
              </button>

              {#if !collapsed}
                <div class="board-workspace-index-items">
                  {#each column.cards as card}
                    {@const marker = runtimeMarker(card)}
                    <button
                      class:active={selectedCard?.id === card.id}
                      class="board-workspace-item-row"
                      data-card
                      data-ticket-id={card.id}
                      type="button"
                      onclick={() => selectCard(card.id)}
                    >
                      <span class="board-workspace-item-label entity-row-title">{card.title}</span>
                      {#if marker}
                        <span
                          class={`board-workspace-runtime-status board-workspace-runtime-status--${marker}`}
                          data-marker={marker}
                        ></span>
                      {:else}
                        <span class="board-workspace-runtime-status"></span>
                      {/if}
                    </button>
                  {/each}
                </div>
              {/if}
            </section>
          {/each}
        </section>

        <section class="board-workspace-right" aria-label="Board inspector">
          {#if selectedCard}
            <div class="board-workspace-placeholder">
              <a class="board-workspace-open-ticket" href={`#/ticket/${selectedCard.id}`}>
                {selectedCard.title}
              </a>
            </div>
          {/if}
        </section>
      </div>
    </div>
  {/if}
</section>

<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson } from "../lib/api";
  import { resource } from "../lib/resources";
  import type { BoardResponse } from "../lib/types";
  import {
    FIELD_NAMES,
    ticketStageVisualState,
    type FieldStageVisualState
  } from "../lib/ui";
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

  function cardStageState(
    columnState: string,
    card: Record<string, any>,
    fieldName: string
  ): FieldStageVisualState {
    return ticketStageVisualState({
      ticketState: columnState,
      ticketStatus: card.ticket_status,
      fieldName,
      fieldHasProposal: Boolean(card.has_pending_proposal)
    });
  }

  function stageMarker(card: Record<string, any>, stageState: FieldStageVisualState): string | null {
    if (stageState === "current-running") return "agent-running-step";
    if (stageState === "errored") return "errored";
    if (stageState === "current-awaiting-approval" && card.has_pending_proposal) {
      return "pending-proposal";
    }
    if (stageState === "current-waiting" && card.ticket_status === "user_takeover") {
      return "user-takeover";
    }
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
                    <button
                      class:active={selectedCard?.id === card.id}
                      class="board-workspace-item-row"
                      data-card
                      data-ticket-id={card.id}
                      type="button"
                      onclick={() => selectCard(card.id)}
                    >
                      <span class="board-workspace-item-label entity-row-title">{card.title}</span>
                      <span class="board-workspace-stage-rail" aria-label="Ticket stages">
                        {#each FIELD_NAMES as name}
                          {@const stageState = cardStageState(column.state, card, name)}
                          {@const marker = stageMarker(card, stageState)}
                          <span
                            class={`board-workspace-stage-mark fsec-mark fsec-mark--${stageState}`}
                            data-stage-field={name}
                            data-stage-state={stageState}
                            data-marker={marker || undefined}
                            aria-label={`${name} ${stageState.replace(/-/g, " ")}`}
                          ></span>
                        {/each}
                      </span>
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

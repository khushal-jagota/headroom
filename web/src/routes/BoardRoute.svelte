<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson } from "../lib/api";
  import { resource } from "../lib/resources";
  import type { BoardResponse } from "../lib/types";
  import Chip from "../components/Chip.svelte";
  import EntityRow from "../components/EntityRow.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";

  const board = resource<BoardResponse>("board", (signal) => fetchJson("/api/board", { signal }));
  let columns = $derived(board.data?.columns || []);

  function stateLabel(state: string): string {
    return state.replace(/_/g, " ");
  }

  function stageNumber(index: number): string {
    return String(index + 1).padStart(2, "0");
  }

  onDestroy(() => board.dispose());
</script>

<section class="board-screen" data-screen="board">
  {#if board.error}
    <ErrorLine error={board.error} />
  {:else if board.loading && !board.data}
    <div class="quiet-line">Loading board...</div>
  {:else}
    <div class="board">
      {#each columns as column, index}
        <section
          class:board-column--empty={column.cards.length === 0}
          class="board-column"
          data-column={column.state}
        >
          <div class="board-column-head">
            <span class="board-stage-number">{stageNumber(index)}</span>
            <span class="board-column-name">{stateLabel(column.state)}</span>
            <span class="board-column-count">{column.cards.length}</span>
          </div>
          <div class="board-column-cards">
            {#each column.cards as card}
              <EntityRow href={`#/ticket/${card.id}`} title={card.title} card ticketId={card.id}>
                <Chip variant="priority" value={card.priority} />
                {#if card.deadline}<Chip variant="deadline" value={card.deadline} />{/if}
                {#if card.project}<Chip variant="project" value={card.project} />{/if}
                {#if card.has_pending_proposal}
                  <span data-marker="pending-proposal"><Chip variant="pending-proposal" /></span>
                {/if}
                {#if card.ticket_status === "agent_running_step"}
                  <span data-marker="agent-running-step"><Chip variant="agent-running-step" /></span>
                {/if}
                {#if card.ticket_status === "user_takeover"}
                  <span data-marker="user-takeover"><Chip variant="user-takeover" /></span>
                {/if}
              </EntityRow>
            {/each}
          </div>
        </section>
      {/each}
    </div>
  {/if}
</section>

<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson } from "../lib/api";
  import { resource } from "../lib/resources";
  import type { BoardResponse } from "../lib/types";
  import Chip from "../components/Chip.svelte";
  import EntityRow from "../components/EntityRow.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";

  const board = resource<BoardResponse>("board", (signal) => fetchJson("/api/board", { signal }));

  onDestroy(() => board.dispose());
</script>

<section class="board-screen" data-screen="board">
  {#if board.error}
    <ErrorLine error={board.error} />
  {:else if board.loading && !board.data}
    <div class="quiet-line">Loading board...</div>
  {:else}
    <div class="board">
      {#each board.data?.columns || [] as column}
        <div class="board-column" data-column={column.state}>
          <div class="board-column-head">
            <span class="board-column-name">{column.state.replace(/_/g, " ")}</span>
            <span class="board-column-count">{column.cards.length}</span>
          </div>
          <div class="board-column-cards">
            {#if column.cards.length}
              {#each column.cards as card}
                <EntityRow href={`#/ticket/${card.id}`} title={card.title}>
                  <Chip variant="priority" value={card.priority} />
                  {#if card.deadline}<Chip variant="deadline" value={card.deadline} />{/if}
                  {#if card.project}<Chip variant="project" value={card.project} />{/if}
                  {#if card.has_pending_proposal}<Chip variant="pending-proposal" />{/if}
                  {#if card.ticket_status === "agent_running_step"}<Chip variant="agent-running-step" />{/if}
                  {#if card.ticket_status === "user_takeover"}<Chip variant="user-takeover" />{/if}
                </EntityRow>
              {/each}
            {:else}
              <div class="quiet-line">(empty)</div>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/if}
</section>

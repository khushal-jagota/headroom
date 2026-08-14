<script lang="ts">
  import { onMount } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../lib/queryCatalogue";
  import { conversationSignalPresentation } from "../lib/conversationSignalPresentation";
  import { onReplyWatermarkMoved, readReplyWatermark } from "../lib/replyWatermark";
  import {
    buildWorkspaceRail,
    hiddenWorkspaceCardCount,
    workspaceItemIsOpen,
    workspaceVisibleCards,
    type WorkspaceRailItem,
    type WorkspaceRailMode
  } from "../lib/workspaceRail";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import type { BoardCard } from "../lib/types";
  import ChiefConversation from "../components/ChiefConversation.svelte";
  import PriorityTile from "../components/PriorityTile.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import SprintItemWorkspace from "../components/SprintItemWorkspace.svelte";
  import SprintTicketRow from "../components/SprintTicketRow.svelte";
  import StageMark from "../components/StageMark.svelte";
  import TicketRoute from "./TicketRoute.svelte";
  import chiefOfStaffProfile from "../assets/chief-of-staff-profile.webp";

  let { ticketId, itemId }: { ticketId?: string; itemId?: string } = $props();

  const board = createQuery(() => queries.board());
  const workers = createQuery(() => queries.workers());
  let railMode = $state<WorkspaceRailMode>("attention");
  let expandedItemIds = $state<Set<string>>(new Set());
  let openedItemIds = $state<Set<string>>(new Set());
  let foldedItemIds = $state<Set<string>>(new Set());
  let allCards = $derived((board.data?.columns ?? []).flatMap((column) => column.cards));
  let rail = $derived(buildWorkspaceRail(allCards));
  let selectedCard = $derived(allCards.find((card) => card.id === ticketId) ?? null);
  let selectedItem = $derived(rail.items.find((item) => item.id === itemId) ?? null);
  let noItemCards = $derived(workspaceVisibleCards(rail.noItemCards, railMode));
  let chiefSelected = $derived(ticketId === "chief-of-staff");

  $effect(() => {
    const staleTicket = ticketId && !chiefSelected && !selectedCard;
    const staleItem = itemId && !selectedItem;
    if (
      (staleTicket || staleItem) &&
      board.data &&
      !board.isFetching &&
      !board.isError
    ) {
      window.location.replace(workspaceAddress({ kind: "none" }));
    }
  });

  function selectCard(id: string): void {
    window.location.hash = window.matchMedia("(max-width: 960px)").matches
      ? `#/ticket/${encodeURIComponent(id)}`
      : workspaceAddress({ kind: "ticket", id });
  }

  function selectItem(id: string): void {
    window.location.hash = workspaceAddress({ kind: "item", id });
  }

  function toggleRailMode(): void {
    railMode = railMode === "attention" ? "all" : "attention";
    foldedItemIds = new Set();
  }

  function itemExpanded(item: WorkspaceRailItem): boolean {
    return expandedItemIds.has(item.id);
  }

  function itemOpen(item: WorkspaceRailItem): boolean {
    return workspaceItemIsOpen(item, railMode, {
      expanded: itemExpanded(item),
      opened: openedItemIds.has(item.id),
      folded: foldedItemIds.has(item.id)
    });
  }

  function toggleItemFold(item: WorkspaceRailItem): void {
    const nextFolded = new Set(foldedItemIds);
    const nextOpened = new Set(openedItemIds);
    const nextExpanded = new Set(expandedItemIds);
    if (itemOpen(item)) {
      nextFolded.add(item.id);
      nextOpened.delete(item.id);
    } else {
      nextFolded.delete(item.id);
      nextOpened.add(item.id);
      if (item.attentionCards.length === 0) nextExpanded.add(item.id);
    }
    foldedItemIds = nextFolded;
    openedItemIds = nextOpened;
    expandedItemIds = nextExpanded;
  }

  function expandItem(id: string): void {
    expandedItemIds = new Set(expandedItemIds).add(id);
  }

  let howFarThisBrowserHasRead = $state<Record<string, number>>({});

  function rereadWhereThisBrowserHasGot(): void {
    const positions: Record<string, number> = {};
    for (const card of allCards) {
      if (card.conversation_id) {
        positions[card.conversation_id] = readReplyWatermark(card.conversation_id);
      }
    }
    const chiefConversationId = workers.data?.chief_of_staff.conversation_id;
    if (chiefConversationId) {
      positions[chiefConversationId] = readReplyWatermark(chiefConversationId);
    }
    howFarThisBrowserHasRead = positions;
  }

  let chiefPresentation = $derived(
    workers.data
      ? conversationSignalPresentation(
          workers.data.chief_of_staff,
          howFarThisBrowserHasRead
        )
      : null
  );

  onMount(() => onReplyWatermarkMoved(rereadWhereThisBrowserHasGot));
  $effect(() => {
    board.data;
    workers.data;
    rereadWhereThisBrowserHasGot();
  });

  function cardPresentation(card: BoardCard) {
    return conversationSignalPresentation(
      {
        conversation_id: card.conversation_id,
        needs_me: card.needs_me,
        agent_working: card.agent_working,
        latest_turn_ended_sequence: card.latest_turn_ended_sequence
      },
      howFarThisBrowserHasRead
    );
  }
</script>

{#snippet ticketRow(card: BoardCard)}
  {@const presentation = cardPresentation(card)}
  <SprintTicketRow
    priority={card.priority}
    title={card.title}
    state={presentation.state}
    ariaLabel={presentation.ariaLabel}
    active={selectedCard?.id === card.id}
    onclick={() => selectCard(card.id)}
    stageMarkClass="board-workspace-stage-mark"
    stageMarkAttributes={{
      "data-stage-state": presentation.state,
      "data-needs-me": card.needs_me ? "true" : "false",
      "data-agent-working": card.agent_working ? "true" : "false",
      "data-latest-turn-ended": card.latest_turn_ended_sequence
    }}
    data-card=""
    data-ticket-id={card.id}
    data-ticket-stage={card.stage}
    data-ticket-status={card.ticket_status}
  />
{/snippet}

<section class="board-screen" data-screen="workspace">
  <ResourceState
    error={board.error}
    loading={board.isFetching}
    hasData={Boolean(board.data)}
    loadingText="Loading workspace..."
  >
    <div class="board-workspace-wrap">
      <div
        class="board-workspace-shell"
        class:board-workspace-shell--chief={chiefSelected}
        class:board-workspace-shell--item={Boolean(selectedItem)}
      >
        <section class="board-workspace-left" aria-label="Workspace tickets by Sprint Item">
          {#if workers.data}
            <a
              class="board-workspace-chief-row"
              class:active={chiefSelected}
              href={workspaceAddress({ kind: "chief" })}
              data-chief-destination
              aria-current={chiefSelected ? "page" : undefined}
            >
              <img
                class="board-workspace-agent-profile"
                src={chiefOfStaffProfile}
                alt=""
                aria-hidden="true"
              />
              <span class="board-workspace-chief-name">
                {workers.data.chief_of_staff.label}
              </span>
              {#if chiefPresentation}
                <StageMark
                  state={chiefPresentation.state}
                  data-stage-state={chiefPresentation.state}
                  data-needs-me={workers.data.chief_of_staff.needs_me ? "true" : "false"}
                  data-agent-working={workers.data.chief_of_staff.agent_working ? "true" : "false"}
                  data-latest-turn-ended={workers.data.chief_of_staff.latest_turn_ended_sequence}
                  aria-label={chiefPresentation.ariaLabel}
                />
              {/if}
            </a>
          {/if}

          <div class="board-workspace-view-control">
            <button type="button" onclick={toggleRailMode} data-workspace-view={railMode}>
              {railMode === "attention" ? "Everything on today" : "What needs you"}
            </button>
          </div>

          {#each rail.items as item (item.id)}
            {@const visibleCards = workspaceVisibleCards(item.cards, railMode, itemExpanded(item))}
            {@const hiddenCount = hiddenWorkspaceCardCount(item, railMode, itemExpanded(item))}
            {@const open = itemOpen(item)}
            <section
              class="board-workspace-item"
              class:board-workspace-item--open={open}
              class:board-workspace-item--quiet={item.attentionCards.length === 0}
              class:active={selectedItem?.id === item.id}
              data-sprint-item={item.id}
              data-sprint-item-priority={item.priority}
            >
              <div class="board-workspace-item-head">
                <button
                  type="button"
                  class="board-workspace-item-open"
                  onclick={() => selectItem(item.id)}
                  aria-current={selectedItem?.id === item.id ? "page" : undefined}
                >
                  <span class="board-workspace-item-priority">
                    <PriorityTile priority={item.priority} />
                  </span>
                  <span class="board-workspace-item-title">{item.title}</span>
                </button>
                <button
                  type="button"
                  class="board-workspace-item-fold"
                  onclick={() => toggleItemFold(item)}
                  aria-label={`${open ? "Fold" : "Open"} ${item.title}`}
                  aria-expanded={open}
                >
                  {#if !open && item.liveCards.length}
                    <span class="board-workspace-item-count">{item.liveCards.length}</span>
                  {/if}
                  {#if open}<span class="board-workspace-item-chevron" aria-hidden="true"></span>{/if}
                </button>
              </div>

              {#if open}
                <div class="board-workspace-item-tickets">
                  {#each visibleCards as card (card.id)}
                    {@render ticketRow(card)}
                  {/each}
                  {#if hiddenCount > 0}
                    <button
                      type="button"
                      class="board-workspace-item-more"
                      onclick={() => expandItem(item.id)}
                    >{hiddenCount} more</button>
                  {/if}
                </div>
              {/if}
            </section>
          {/each}

          {#if noItemCards.length}
            <section class="board-workspace-no-item" data-no-item>
              <h2>No Item</h2>
              <div class="board-workspace-no-item-tickets">
                {#each noItemCards as card (card.id)}
                  {@render ticketRow(card)}
                {/each}
              </div>
            </section>
          {/if}

          {#if rail.items.every((item) => item.attentionCards.length === 0) && workspaceVisibleCards(rail.noItemCards, "attention").length === 0 && railMode === "attention"}
            <div class="board-workspace-quiet-line">Nothing waits for you.</div>
          {/if}
        </section>

        <section
          class:board-workspace-right--ticket={Boolean(selectedCard)}
          class:board-workspace-right--chief={chiefSelected}
          class:board-workspace-right--item={Boolean(selectedItem)}
          class="board-workspace-right"
          aria-label={chiefSelected ? "Chief of Staff conversation" : selectedItem ? "Sprint Item workspace" : "Workspace inspector"}
        >
          {#if chiefSelected}
            <div class="board-workspace-chief-conversation">
              <ChiefConversation />
            </div>
          {:else if selectedCard}
            {#key selectedCard.id}
              <TicketRoute id={selectedCard.id} />
            {/key}
          {:else if selectedItem}
            {#key selectedItem.id}
              <SprintItemWorkspace itemId={selectedItem.id} sprintName="Workspace" backHref="#/workspace" />
            {/key}
          {:else}
            <div class="board-workspace-empty-inspector">
              <span>Select a Sprint Item or Ticket to inspect it.</span>
            </div>
          {/if}
        </section>
      </div>
    </div>
  </ResourceState>
</section>

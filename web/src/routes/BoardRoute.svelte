<script lang="ts">
  import { onMount } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../lib/queryCatalogue";
  import { conversationSignalPresentation } from "../lib/conversationSignalPresentation";
  import { onReplyWatermarkMoved, readReplyWatermark } from "../lib/replyWatermark";
  import {
    buildWorkspaceRail,
    hiddenWorkspaceCardCount,
    workspaceGroupsHaveShownCards,
    workspaceItemGroups,
    type WorkspaceRailItem,
    type WorkspaceTicketGroup
  } from "../lib/workspaceRail";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import type { BoardCard } from "../lib/types";
  import ChiefConversation from "../components/ChiefConversation.svelte";
  import PriorityTile from "../components/PriorityTile.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import SectionHeading from "../components/SectionHeading.svelte";
  import SprintItemWorkspace from "../components/SprintItemWorkspace.svelte";
  import SprintTicketRow from "../components/SprintTicketRow.svelte";
  import StageMark from "../components/StageMark.svelte";
  import TicketRoute from "./TicketRoute.svelte";
  import chiefOfStaffProfile from "../assets/chief-of-staff-profile.webp";

  let { ticketId, itemId }: { ticketId?: string; itemId?: string } = $props();

  // The No Item tail reveals its own quiet groups, so it needs a reveal key of its own.
  const NO_ITEM_KEY = "no-item";

  const board = createQuery(() => queries.board());
  const workers = createQuery(() => queries.workers());
  let revealedItemIds = $state<Set<string>>(new Set());
  let foldedItemIds = $state<Set<string>>(new Set());
  let allCards = $derived((board.data?.columns ?? []).flatMap((column) => column.cards));
  let rail = $derived(buildWorkspaceRail(allCards, board.data?.sprint_items ?? []));
  let selectedCard = $derived(allCards.find((card) => card.id === ticketId) ?? null);
  let selectedItem = $derived(rail.items.find((item) => item.id === itemId) ?? null);
  let chiefSelected = $derived(ticketId === "chief-of-staff");
  // A Ticket opens here from anywhere, including the Sprint page and Review, so the
  // selection is not limited to the cards on today's board. The rail highlights a
  // Ticket only when it holds a card for it, and TicketRoute answers for the Ticket
  // itself, including one that does not exist.
  let ticketSelected = $derived(Boolean(ticketId) && !chiefSelected);
  let nothingWaits = $derived(
    rail.items.every((item) => !workspaceGroupsHaveShownCards(item.groups)) &&
      !workspaceGroupsHaveShownCards(rail.noItemGroups)
  );

  $effect(() => {
    const staleItem = itemId && !selectedItem;
    if (staleItem && board.data && !board.isFetching && !board.isError) {
      window.location.replace(workspaceAddress({ kind: "none" }));
    }
  });

  function selectCard(id: string): void {
    window.location.hash = workspaceAddress({ kind: "ticket", id });
  }

  function selectItem(id: string): void {
    window.location.hash = workspaceAddress({ kind: "item", id });
  }

  function itemOpen(item: WorkspaceRailItem): boolean {
    return !foldedItemIds.has(item.id);
  }

  function toggleItemFold(item: WorkspaceRailItem): void {
    const next = new Set(foldedItemIds);
    if (next.has(item.id)) next.delete(item.id);
    else next.add(item.id);
    foldedItemIds = next;
  }

  function itemRevealed(item: WorkspaceRailItem): boolean {
    return revealedItemIds.has(item.id);
  }

  function toggleReveal(key: string): void {
    const next = new Set(revealedItemIds);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    revealedItemIds = next;
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

{#snippet ticketGroups(groups: WorkspaceTicketGroup[])}
  {#each groups as group (group.key)}
    <SectionHeading
      label={group.label}
      class="board-workspace-group-heading"
      data-workspace-group={group.key}
    />
    {#each group.cards as card (card.id)}
      {@render ticketRow(card)}
    {/each}
  {/each}
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
        class:board-workspace-shell--ticket={ticketSelected}
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

          {#each rail.items as item (item.id)}
            {@const revealed = itemRevealed(item)}
            {@const shownGroups = workspaceItemGroups(item.groups, revealed)}
            {@const hiddenCount = hiddenWorkspaceCardCount(item.groups)}
            {@const shownCount = shownGroups.reduce((total, group) => total + group.cards.length, 0)}
            {@const open = itemOpen(item)}
            <section
              class="board-workspace-item"
              class:board-workspace-item--open={open}
              class:board-workspace-item--quiet={!item.needsUser}
              class:active={selectedItem?.id === item.id}
              data-sprint-item={item.id}
              data-sprint-item-priority={item.priority}
            >
              <div class="board-workspace-item-head">
                <div class="board-workspace-item-eyebrow" data-sprint-item-eyebrow>
                  <PriorityTile priority={item.priority} />
                  {#if item.project}<span>·</span><span>{item.project}</span>{/if}
                  {#if item.progress}
                    <span>·</span>
                    <span data-sprint-item-progress>
                      {item.progress.done} of {item.progress.total} done
                    </span>
                  {/if}
                </div>
                <div class="board-workspace-item-line">
                  <button
                    type="button"
                    class="board-workspace-item-open"
                    onclick={() => selectItem(item.id)}
                    aria-current={selectedItem?.id === item.id ? "page" : undefined}
                  >
                    <span class="board-workspace-item-title">{item.title}</span>
                  </button>
                  <button
                    type="button"
                    class="board-workspace-item-fold"
                    onclick={() => toggleItemFold(item)}
                    aria-label={`${open ? "Fold" : "Open"} ${item.title}`}
                    aria-expanded={open}
                  >
                    {#if !open && shownCount}
                      <span class="board-workspace-item-count">{shownCount}</span>
                    {/if}
                    <span class="board-workspace-item-chevron" aria-hidden="true"></span>
                  </button>
                </div>
              </div>

              {#if open}
                <div class="board-workspace-item-tickets">
                  {@render ticketGroups(shownGroups)}
                  {#if hiddenCount > 0}
                    <button
                      type="button"
                      class="board-workspace-item-more"
                      onclick={() => toggleReveal(item.id)}
                      aria-expanded={revealed}
                      data-workspace-reveal={item.id}
                    >{revealed ? "less" : `+${hiddenCount} more`}</button>
                  {/if}
                </div>
              {/if}
            </section>
          {/each}

          {#if rail.noItemGroups.length}
            {@const revealed = revealedItemIds.has(NO_ITEM_KEY)}
            {@const hiddenCount = hiddenWorkspaceCardCount(rail.noItemGroups)}
            <section class="board-workspace-no-item" data-no-item>
              <h2>No Item</h2>
              <div class="board-workspace-no-item-tickets">
                {@render ticketGroups(workspaceItemGroups(rail.noItemGroups, revealed))}
                {#if hiddenCount > 0}
                  <button
                    type="button"
                    class="board-workspace-item-more"
                    onclick={() => toggleReveal(NO_ITEM_KEY)}
                    aria-expanded={revealed}
                    data-workspace-reveal={NO_ITEM_KEY}
                  >{revealed ? "less" : `+${hiddenCount} more`}</button>
                {/if}
              </div>
            </section>
          {/if}

          {#if nothingWaits}
            <div class="board-workspace-quiet-line">Nothing waits for you.</div>
          {/if}
        </section>

        <section
          class:board-workspace-right--ticket={ticketSelected}
          class:board-workspace-right--chief={chiefSelected}
          class:board-workspace-right--item={Boolean(selectedItem)}
          class="board-workspace-right"
          aria-label={chiefSelected ? "Chief of Staff conversation" : selectedItem ? "Sprint Item workspace" : "Workspace inspector"}
        >
          {#if chiefSelected}
            <div class="board-workspace-chief-conversation">
              <ChiefConversation />
            </div>
          {:else if ticketSelected && ticketId}
            <a class="board-workspace-back" href={workspaceAddress({ kind: "none" })}>&lsaquo; Workspace</a>
            {#key ticketId}
              <TicketRoute id={ticketId} />
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

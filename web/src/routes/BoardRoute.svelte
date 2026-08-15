<script lang="ts">
  import { onMount } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../lib/queryCatalogue";
  import { conversationSignalPresentation } from "../lib/conversationSignalPresentation";
  import { onReplyWatermarkMoved, readReplyWatermark } from "../lib/replyWatermark";
  import {
    buildWorkspaceRail,
    type WorkspaceRailItem,
    type WorkspaceTicketGroup
  } from "../lib/workspaceRail";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import type { BoardCard } from "../lib/types";
  import ChiefConversation from "../components/ChiefConversation.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import SprintItemWorkspace from "../components/SprintItemWorkspace.svelte";
  import SprintTicketRow from "../components/SprintTicketRow.svelte";
  import StageMark from "../components/StageMark.svelte";
  import TicketRoute from "./TicketRoute.svelte";
  import chiefOfStaffProfile from "../assets/chief-of-staff-profile.webp";

  let { ticketId, itemId }: { ticketId?: string; itemId?: string } = $props();

  const VIEWS = [
    { key: "tickets", label: "Tickets" },
    { key: "items", label: "Sprint Items" }
  ] as const;

  const board = createQuery(() => queries.board());
  const workers = createQuery(() => queries.workers());
  // Two views over one board. Until the reader picks one, an Item in the address
  // decides, so a reloaded Item selection is visible in the rail it belongs to.
  // Otherwise Tickets leads: it shows every Ticket without a selection.
  let chosenView = $state<"tickets" | "items" | null>(null);
  let view = $derived(chosenView ?? (itemId ? "items" : "tickets"));
  // Which Item the rail holds open. It follows the address, until a Ticket opened from
  // inside an Item takes the pane: the Item the reader is working in stays open.
  let openedItemId = $state<string | null>(null);
  let railItemId = $derived(openedItemId ?? itemId ?? null);
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
    view === "tickets" ? rail.groups.length === 0 : rail.items.length === 0
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

  // The whole box is the target: a click selects the Item, a second click shuts it.
  // Reaching for an Item is choosing this view, so shutting it stays here.
  function selectItem(id: string): void {
    chosenView = "items";
    const shutting = railItemId === id;
    openedItemId = shutting ? null : id;
    window.location.hash = workspaceAddress(
      shutting ? { kind: "none" } : { kind: "item", id }
    );
  }

  let howFarThisBrowserHasRead = $state<Record<string, number>>({});

  function rereadWhereThisBrowserHasGot(): void {
    const positions: Record<string, number> = {};
    for (const card of allCards) {
      if (card.conversation_id) {
        positions[card.conversation_id] = readReplyWatermark(card.conversation_id);
      }
    }
    for (const item of rail.items) {
      const conversationId = item.signals.conversation_id;
      if (conversationId) {
        positions[conversationId] = readReplyWatermark(conversationId);
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

{#snippet ticketRow(card: BoardCard, withPriority: boolean)}
  {@const presentation = cardPresentation(card)}
  <SprintTicketRow
    priority={withPriority ? card.priority : null}
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

{#snippet ticketGroups(
  groups: WorkspaceTicketGroup[],
  withPriority: boolean,
  nested: boolean
)}
  {#each groups as group (group.key)}
    <Disclosure
      variant="workspace-bucket"
      chevron="trailing"
      defaultOpen={!group.defaultCollapsed}
      class={nested ? "disclosure--workspace-bucket--nested" : ""}
      data-bucket-section=""
      data-bucket-key={group.key}
    >
      {#snippet summary()}
        <span class="board-workspace-bucket-label">{group.label}</span>
        <span
          class="board-workspace-bucket-count"
          role="img"
          aria-label={`${group.cards.length} ${group.cards.length === 1 ? "Ticket" : "Tickets"}`}
        >{group.cards.length}</span>
      {/snippet}

      <div class="board-workspace-bucket-tickets">
        {#each group.cards as card (card.id)}
          {@render ticketRow(card, withPriority)}
        {/each}
      </div>
    </Disclosure>
  {/each}
{/snippet}

{#snippet sprintItem(item: WorkspaceRailItem)}
  {@const open = railItemId === item.id}
  {@const presentation = conversationSignalPresentation(
    item.signals,
    howFarThisBrowserHasRead
  )}
  <section
    class="board-workspace-item"
    class:board-workspace-item--selected={open}
    data-sprint-item={item.id}
    onclick={() => selectItem(item.id)}
    role="presentation"
  >
    <button
      type="button"
      class="board-workspace-item-head"
      onclick={(event) => {
        event.stopPropagation();
        selectItem(item.id);
      }}
      aria-expanded={open}
      aria-current={selectedItem?.id === item.id ? "page" : undefined}
    >
      <span class="board-workspace-item-title">{item.title}</span>
      <StageMark
        state={presentation.state}
        class="board-workspace-stage-mark"
        data-stage-state={presentation.state}
        data-needs-me={item.signals.needs_me ? "true" : "false"}
        data-agent-working={item.signals.agent_working ? "true" : "false"}
        data-latest-turn-ended={item.signals.latest_turn_ended_sequence}
        aria-label={presentation.ariaLabel}
      />
    </button>
    {#if open}
      <!-- A fold or a Ticket inside the Item is not a click on the Item. -->
      <div
        class="board-workspace-item-groups"
        onclick={(event) => event.stopPropagation()}
        role="presentation"
      >
        {@render ticketGroups(item.groups, false, true)}
      </div>
    {:else}
      <div class="board-workspace-item-line">
        {#each item.groups as group, index (group.key)}
          {#if index > 0}<span>·</span>{/if}
          <span><b>{group.cards.length}</b> {group.label}</span>
        {/each}
      </div>
    {/if}
  </section>
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
        <section class="board-workspace-left" aria-label="Workspace">
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

          <div class="board-workspace-views" role="tablist" aria-label="Workspace view">
            {#each VIEWS as option (option.key)}
              <button
                type="button"
                class="board-workspace-view"
                role="tab"
                aria-selected={view === option.key}
                data-workspace-view={option.key}
                onclick={() => (chosenView = option.key)}
              >{option.label}</button>
            {/each}
          </div>

          {#if view === "tickets"}
            {@render ticketGroups(rail.groups, true, false)}
          {:else}
            {#each rail.items as item (item.id)}
              {@render sprintItem(item)}
            {/each}
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

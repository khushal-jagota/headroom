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
  import {
    whatTheAddressOpens,
    workspaceAddress,
    type WorkspaceAddress
  } from "../lib/workspaceAddress";
  import type { BoardCard } from "../lib/types";
  import ChiefConversation from "../components/ChiefConversation.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import SprintItemWorkspace from "../components/SprintItemWorkspace.svelte";
  import SprintTicketRow from "../components/SprintTicketRow.svelte";
  import StageMark from "../components/StageMark.svelte";
  import TicketRoute from "./TicketRoute.svelte";
  import chiefOfStaffProfile from "../assets/chief-of-staff-profile.webp";

  let { address }: { address: WorkspaceAddress } = $props();

  const VIEWS = [
    { key: "items", label: "Sprint Items" },
    { key: "tickets", label: "Tickets" }
  ] as const;

  const board = createQuery(() => queries.board());
  const workers = createQuery(() => queries.workers());
  // The address says which list the rail shows, which Item is open, and which single row
  // carries the selected mark. This screen keeps no answer of its own, so the same
  // address always draws the same rail.
  let opening = $derived(whatTheAddressOpens(address));
  let allCards = $derived((board.data?.columns ?? []).flatMap((column) => column.cards));
  let rail = $derived(buildWorkspaceRail(allCards, board.data?.sprint_items ?? []));
  // A Ticket opens here from anywhere, including the Sprint page and Review, so the
  // selection is not limited to the cards on today's board. The rail highlights a
  // Ticket only when it holds a card for it, and TicketRoute answers for the Ticket
  // itself, including one that does not exist.
  let selectedCard = $derived(
    allCards.find((card) => card.id === opening.markedTicketId) ?? null
  );
  let openItem = $derived(rail.items.find((item) => item.id === opening.openItemId) ?? null);
  let itemPane = $derived(Boolean(opening.markedItemId) && Boolean(openItem));
  let nothingWaits = $derived(
    opening.view === "tickets" ? rail.groups.length === 0 : rail.items.length === 0
  );

  // An Item that is not on the board cannot be drawn open. The address gives it up once
  // the board has settled, and a Ticket in the pane keeps its own address.
  $effect(() => {
    const staleItem = opening.openItemId && !openItem;
    if (staleItem && board.data && !board.isFetching && !board.isError) {
      const kept = opening.markedTicketId
        ? workspaceAddress({ kind: "ticket", id: opening.markedTicketId })
        : workspaceAddress({ kind: "none" });
      window.location.replace(kept);
    }
  });

  // Every click writes an address, and the draw follows from it. A Ticket clicked inside
  // an open Item names that Item, which is the whole of "opened from inside an Item".
  function selectCard(id: string, insideItemId: string | null): void {
    const selection = insideItemId
      ? ({ kind: "ticket", id, openedFromItemId: insideItemId } as const)
      : ({ kind: "ticket", id } as const);
    window.location.hash = workspaceAddress(selection, opening.view);
  }

  // The whole box is the target, and the click has one meaning: open this Item and
  // show its supervisor. No click on an Item shuts it, including a click on the Item
  // already in the pane.
  function selectItem(id: string): void {
    window.location.hash = workspaceAddress({ kind: "item", id });
  }

  // Changing the list the rail shows keeps whatever the pane is showing.
  function showView(next: (typeof VIEWS)[number]["key"]): void {
    window.location.hash = workspaceAddress(address.selection, next);
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
          {
            conversation_id: workers.data.chief_of_staff.conversation_id,
            needs_me: workers.data.chief_of_staff.needs_me,
            agent_working: workers.data.chief_of_staff.agent_working,
            unread_position: workers.data.chief_of_staff.latest_turn_ended_sequence
          },
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
        unread_position: card.latest_turn_ended_sequence
      },
      howFarThisBrowserHasRead
    );
  }

  // Awake beats rested: a done Item that needs the user, is mid-turn, or holds a
  // reply this browser has not seen yet stays with the live Items.
  function itemIsAwake(item: WorkspaceRailItem): boolean {
    const state = conversationSignalPresentation(item.signals, howFarThisBrowserHasRead).state;
    return state === "needs-me" || state === "current-running" || state === "current-awaiting-approval";
  }

  // A stable partition, not a re-sort: live-or-awake Items keep rail.items's
  // priority-then-age order, then rested-and-quiet Items follow in that same order.
  function partitionByRest(items: readonly WorkspaceRailItem[]): WorkspaceRailItem[] {
    const live = items.filter((item) => !item.rested || itemIsAwake(item));
    const rested = items.filter((item) => item.rested && !itemIsAwake(item));
    return [...live, ...rested];
  }

  let orderedItems = $derived(partitionByRest(rail.items));
</script>

{#snippet ticketRow(card: BoardCard, withPriority: boolean, insideItemId: string | null)}
  {@const presentation = cardPresentation(card)}
  <SprintTicketRow
    priority={withPriority ? card.priority : null}
    title={card.title}
    state={presentation.state}
    ariaLabel={presentation.ariaLabel}
    active={selectedCard?.id === card.id}
    onclick={() => selectCard(card.id, insideItemId)}
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
  insideItemId: string | null
)}
  {#each groups as group (group.key)}
    <Disclosure
      variant="workspace-bucket"
      chevron="trailing"
      defaultOpen={!group.defaultCollapsed}
      class={insideItemId ? "disclosure--workspace-bucket--nested" : ""}
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
          {@render ticketRow(card, withPriority, insideItemId)}
        {/each}
      </div>
    </Disclosure>
  {/each}
{/snippet}

{#snippet sprintItem(item: WorkspaceRailItem)}
  {@const open = opening.openItemId === item.id}
  {@const selected = opening.markedItemId === item.id}
  {@const presentation = conversationSignalPresentation(
    item.signals,
    howFarThisBrowserHasRead
  )}
  <section
    class="board-workspace-item"
    class:board-workspace-item--selected={selected}
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
      aria-current={selected ? "page" : undefined}
    >
      <span
        class="board-workspace-item-title"
        class:board-workspace-item-title--rested={item.rested && !itemIsAwake(item)}
      >{item.title}</span>
      <StageMark
        state={presentation.state}
        class="board-workspace-stage-mark"
        data-stage-state={presentation.state}
        data-needs-me={item.signals.needs_me ? "true" : "false"}
        data-agent-working={item.signals.agent_working ? "true" : "false"}
        data-latest-ping={item.signals.needs_me_position}
        data-latest-turn-ended={item.signals.unread_position}
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
        {@render ticketGroups(item.groups, false, item.id)}
      </div>
    {:else if item.groups.length}
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
        class:board-workspace-shell--chief={opening.chiefMarked}
        class:board-workspace-shell--item={itemPane}
        class:board-workspace-shell--ticket={Boolean(opening.markedTicketId)}
      >
        <section class="board-workspace-left" aria-label="Workspace">
          {#if workers.data}
            <a
              class="board-workspace-chief-row"
              class:active={opening.chiefMarked}
              href={workspaceAddress({ kind: "chief" }, opening.view)}
              data-chief-destination
              aria-current={opening.chiefMarked ? "page" : undefined}
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
                aria-selected={opening.view === option.key}
                data-workspace-view={option.key}
                onclick={() => showView(option.key)}
              >{option.label}</button>
            {/each}
          </div>

          {#if opening.view === "tickets"}
            {@render ticketGroups(rail.groups, true, null)}
          {:else}
            {#each orderedItems as item (item.id)}
              {@render sprintItem(item)}
            {/each}
          {/if}

          {#if nothingWaits}
            <div class="board-workspace-quiet-line">Nothing waits for you.</div>
          {/if}
        </section>

        <section
          class:board-workspace-right--ticket={Boolean(opening.markedTicketId)}
          class:board-workspace-right--chief={opening.chiefMarked}
          class:board-workspace-right--item={itemPane}
          class="board-workspace-right"
          aria-label={opening.chiefMarked ? "Chief of Staff conversation" : itemPane ? "Sprint Item workspace" : "Workspace inspector"}
        >
          {#if opening.chiefMarked}
            <div class="board-workspace-chief-conversation">
              <ChiefConversation />
            </div>
          {:else if opening.markedTicketId}
            <a class="board-workspace-back" href={workspaceAddress({ kind: "none" }, opening.view)}>&lsaquo; Workspace</a>
            {#key opening.markedTicketId}
              <TicketRoute id={opening.markedTicketId} />
            {/key}
          {:else if itemPane && openItem}
            {#key openItem.id}
              <SprintItemWorkspace itemId={openItem.id} sprintName="Workspace" backHref="#/workspace" />
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

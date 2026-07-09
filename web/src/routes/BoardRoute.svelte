<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson } from "../lib/api";
  import { resource } from "../lib/resources";
  import type { BoardResponse, GatewayStatus } from "../lib/types";
  import {
    FIELD_NAMES,
    STATE_ORDER,
    ticketStageVisualState,
    ticketStatusLabel,
    type FieldStageVisualState
  } from "../lib/ui";
  import ChatPanel from "../components/ChatPanel.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import TicketRoute from "./TicketRoute.svelte";

  const chiefOfStaffEntityId = "agent_panels_chief_of_staff";
  const noProjectKey = "__no_project__";
  const ticketStatusFilters = [
    { value: "all", label: "All" },
    { value: "empty", label: "Empty" },
    { value: "agent_running_step", label: "Running" },
    { value: "awaiting_approval", label: "Awaiting approval" },
    { value: "user_takeover", label: "User takeover" },
    { value: "errored", label: "Errored" }
  ];
  const board = resource<BoardResponse>("board", (signal) => fetchJson("/api/board", { signal }));
  const chiefChatStatus = resource<GatewayStatus>(`chat-status:${chiefOfStaffEntityId}`, (signal) =>
    fetchJson(`/api/chat/${chiefOfStaffEntityId}/status`, { signal })
  );
  let columns = $derived(board.data?.columns || []);
  let selectedTicketId = $state<string | null>(null);
  let rightPaneMode = $state<"chief" | "ticket">("chief");
  let collapsedProjects = $state<string[]>([]);
  let statusFilter = $state<string>("all");
  let hideDone = $state(false);
  let allCards = $derived(columns.flatMap((column) => column.cards));
  let selectedCard = $derived(allCards.find((card) => card.id === selectedTicketId) || null);
  let projectSections = $derived(buildProjectSections(columns, statusFilter, hideDone));

  function selectCard(ticketId: string): void {
    selectedTicketId = ticketId;
    rightPaneMode = "ticket";
  }

  function showChiefOfStaff(): void {
    rightPaneMode = "chief";
  }

  function isCollapsed(projectKey: string): boolean {
    return collapsedProjects.includes(projectKey);
  }

  function toggleProject(projectKey: string): void {
    collapsedProjects = isCollapsed(projectKey)
      ? collapsedProjects.filter((current) => current !== projectKey)
      : [...collapsedProjects, projectKey];
  }

  function cardStageState(card: Record<string, any>, fieldName: string): FieldStageVisualState {
    return ticketStageVisualState({
      ticketState: card.state,
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

  function buildProjectSections(
    sourceColumns: Array<{ state: string; cards: Record<string, any>[] }>,
    activeStatusFilter: string,
    shouldHideDone: boolean
  ): Array<{ key: string; label: string; cards: Record<string, any>[] }> {
    const groups = new Map<string, { key: string; label: string; cards: Record<string, any>[] }>();
    let sequence = 0;

    for (const column of sourceColumns) {
      for (const card of column.cards) {
        if (shouldHideDone && column.state === "done") continue;
        if (activeStatusFilter !== "all" && card.ticket_status !== activeStatusFilter) continue;
        const key = card.group_project_id || noProjectKey;
        const label = card.group_project || "No project";
        if (!groups.has(key)) groups.set(key, { key, label, cards: [] });
        groups.get(key)?.cards.push({
          ...card,
          state: column.state,
          boardSequence: sequence
        });
        sequence += 1;
      }
    }

    const stateRank = new Map(STATE_ORDER.map((state, index) => [state, index]));
    for (const group of groups.values()) {
      group.cards.sort((left, right) => {
        const stateDelta =
          (stateRank.get(left.state) ?? STATE_ORDER.length) -
          (stateRank.get(right.state) ?? STATE_ORDER.length);
        return stateDelta || left.boardSequence - right.boardSequence;
      });
    }

    return Array.from(groups.values()).sort((left, right) => {
      if (left.key === noProjectKey) return 1;
      if (right.key === noProjectKey) return -1;
      return left.label.localeCompare(right.label, undefined, { sensitivity: "base" });
    });
  }

  onDestroy(() => {
    board.dispose();
    chiefChatStatus.dispose();
  });
</script>

<section class="board-screen" data-screen="workspace">
  {#if board.error}
    <ErrorLine error={board.error} />
  {:else if board.loading && !board.data}
    <div class="quiet-line">Loading workspace...</div>
  {:else}
    <div class="board-workspace-wrap">
      <div class="board-workspace-shell">
        <section class="board-workspace-left" aria-label="Workspace ticket tree">
          <div class="board-workspace-heading board-workspace-heading-row">
            <button
              aria-pressed={rightPaneMode === "chief"}
              class="board-workspace-chief-button"
              data-chief-of-staff-button
              type="button"
              onclick={showChiefOfStaff}
            >
              Chief of Staff
            </button>
          </div>

          <div class="board-workspace-filters" data-workspace-filters>
            <div class="board-workspace-filter-head">Filters</div>
            <div class="board-workspace-filter-row">
              <label class="board-workspace-filter-group" data-filter-group="ticket-status">
                <select
                  aria-label="Ticket status"
                  class="board-workspace-filter-select"
                  data-status-filter={statusFilter}
                  bind:value={statusFilter}
                >
                  {#each ticketStatusFilters as filter}
                    <option
                      value={filter.value}
                      title={filter.value === "all" ? "All ticket statuses" : ticketStatusLabel(filter.value)}
                    >
                      {filter.label}
                    </option>
                  {/each}
                </select>
              </label>

              <label class="board-workspace-hide-done-toggle">
                <input
                  type="checkbox"
                  data-hide-done-toggle
                  bind:checked={hideDone}
                />
                <span>Hide done</span>
              </label>
            </div>
          </div>

          {#each projectSections as section}
            {@const collapsed = isCollapsed(section.key)}
            <section
              class="board-workspace-index-section"
              data-project-section
              data-project-key={section.key}
            >
              <button
                aria-expanded={!collapsed}
                class="board-workspace-index-heading board-workspace-index-toggle"
                type="button"
                onclick={() => toggleProject(section.key)}
              >
                <span class="board-workspace-index-heading-main">
                  <span class:board-workspace-index-chevron--collapsed={collapsed} class="board-workspace-index-chevron">▸</span>
                  <span>{section.label}</span>
                </span>
                <span class="board-workspace-index-count">{section.cards.length}</span>
              </button>

              {#if !collapsed}
                <div class="board-workspace-index-items">
                  {#each section.cards as card}
                    <button
                      class:active={rightPaneMode === "ticket" && selectedCard?.id === card.id}
                      class="board-workspace-item-row"
                      data-card
                      data-ticket-id={card.id}
                      data-ticket-state={card.state}
                      data-ticket-status={card.ticket_status}
                      type="button"
                      onclick={() => selectCard(card.id)}
                    >
                      <span class="board-workspace-item-label entity-row-title">{card.title}</span>
                      <span class="board-workspace-stage-rail" aria-label="Ticket stages">
                        {#each FIELD_NAMES as name}
                          {@const stageState = cardStageState(card, name)}
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

        <section
          class:board-workspace-right--ticket={rightPaneMode === "ticket"}
          class="board-workspace-right"
          aria-label="Workspace inspector"
        >
          {#if rightPaneMode === "chief"}
            <ChatPanel
              entityId={chiefOfStaffEntityId}
              available={chiefChatStatus.data?.available ?? true}
              label="Chief of Staff"
            />
          {:else if selectedCard}
            {#key selectedCard.id}
              <TicketRoute id={selectedCard.id} />
            {/key}
          {/if}
        </section>
      </div>
    </div>
  {/if}
</section>

<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson } from "../lib/api";
  import { resource } from "../lib/resources";
  import type { BoardResponse, GatewayStatus } from "../lib/types";
  import { gatingField, ticketStageVisualState, ticketStatusLabel } from "../lib/ui";
  import type { FieldStageVisualState } from "../lib/ui";
  import Button from "../components/Button.svelte";
  import ChatPanel from "../components/ChatPanel.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import ListRow from "../components/ListRow.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import SectionHeading from "../components/SectionHeading.svelte";
  import StageMark from "../components/StageMark.svelte";
  import TicketRoute from "./TicketRoute.svelte";

  let { hideDone = $bindable(false), ticketId }: { hideDone?: boolean; ticketId?: string } = $props();

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
  let statusFilter = $state<string>("all");
  let statusFilterLabel = $derived(
    ticketStatusFilters.find((filter) => filter.value === statusFilter)?.label ?? "All"
  );
  let allCards = $derived(columns.flatMap((column) => column.cards));
  let selectedCard = $derived(allCards.find((card) => card.id === ticketId) || null);
  let rightPaneMode = $derived<"chief" | "ticket">(selectedCard ? "ticket" : "chief");
  let projectSections = $derived(buildProjectSections(columns, statusFilter, hideDone));

  $effect(() => {
    if (ticketId && board.data && !board.loading && !board.stale && !selectedCard) {
      window.location.replace("#/workspace");
    }
  });

  function selectCard(ticketId: string): void {
    window.location.hash = `#/workspace/${encodeURIComponent(ticketId)}`;
  }

  function showChiefOfStaff(): void {
    window.location.hash = "#/workspace";
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

  function currentStageField(card: Record<string, any>): string {
    if (card.state === "needs_review" || card.state === "done") return "result";
    return gatingField(card.state) || "result";
  }

  function currentStageState(card: Record<string, any>): FieldStageVisualState {
    return cardStageState(card, currentStageField(card));
  }

  function activitySortValue(card: Record<string, any>): number {
    return Number(card.activity_at ?? 0);
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

    for (const group of groups.values()) {
      group.cards.sort((left, right) => {
        const activityDelta = activitySortValue(right) - activitySortValue(left);
        return activityDelta || left.boardSequence - right.boardSequence;
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
  <ResourceState error={board.error} loading={board.loading} hasData={Boolean(board.data)} loadingText="Loading workspace...">
    <div class="board-workspace-wrap">
      <div class="board-workspace-shell">
        <section class="board-workspace-left" aria-label="Workspace ticket tree">
          <div class="board-workspace-heading board-workspace-heading-row">
            <Button
              variant="quiet"
              class="board-workspace-chief-button"
              aria-pressed={rightPaneMode === "chief"}
              data-chief-of-staff-button=""
              onclick={showChiefOfStaff}
            >
              Chief of Staff
            </Button>
          </div>

          <div class="board-workspace-filters" data-workspace-filters>
            <label class="board-workspace-filter-group" data-filter-group="ticket-status">
              <span class="board-workspace-filter-value">{statusFilterLabel}</span>
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

            <label class="board-workspace-hide-done-toggle" class:board-workspace-hide-done-toggle--on={hideDone}>
              <input
                type="checkbox"
                data-hide-done-toggle
                bind:checked={hideDone}
              />
              <span>Hide done</span>
            </label>
          </div>

          {#each projectSections as section}
            <Disclosure
              variant="project"
              class="board-workspace-index-section"
              chevron="none"
              defaultOpen={true}
              data-project-section=""
              data-project-key={section.key}
            >
              {#snippet summary()}
                <span class="board-workspace-index-heading-main">
                  <span class="board-workspace-index-chevron">▸</span>
                  <SectionHeading label={section.label} />
                </span>
                <span class="board-workspace-index-count">{section.cards.length}</span>
              {/snippet}

              <div class="board-workspace-index-items">
                {#each section.cards as card}
                  {@const stageField = currentStageField(card)}
                  {@const stageState = currentStageState(card)}
                  {@const marker = stageMarker(card, stageState)}
                  <ListRow
                    variant="board"
                    title={card.title}
                    active={rightPaneMode === "ticket" && selectedCard?.id === card.id}
                    onclick={() => selectCard(card.id)}
                    data-card=""
                    data-ticket-id={card.id}
                    data-ticket-state={card.state}
                    data-ticket-status={card.ticket_status}
                  >
                    {#snippet trailing()}
                      <span class="board-workspace-stage-rail" aria-label="Ticket current stage">
                        <StageMark
                          state={stageState}
                          class="board-workspace-stage-mark"
                          data-stage-field={stageField}
                          data-stage-state={stageState}
                          data-marker={marker || undefined}
                          aria-label={`${stageField} ${stageState.replace(/-/g, " ")}`}
                        />
                      </span>
                    {/snippet}
                  </ListRow>
                {/each}
              </div>
            </Disclosure>
          {/each}
        </section>

        <section
          class:board-workspace-right--ticket={rightPaneMode === "ticket"}
          class="board-workspace-right"
          aria-label="Workspace inspector"
        >
          {#if rightPaneMode === "chief"}
            <div class="board-workspace-desk-inner">
              <ChatPanel
                entityId={chiefOfStaffEntityId}
                available={chiefChatStatus.data?.available ?? true}
                label="Chief of Staff"
              />
            </div>
          {:else if selectedCard}
            {#key selectedCard.id}
              <TicketRoute id={selectedCard.id} />
            {/key}
          {/if}
        </section>
      </div>
    </div>
  </ResourceState>
</section>

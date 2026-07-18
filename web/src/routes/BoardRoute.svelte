<script lang="ts">
  import { onDestroy } from "svelte";
  import { relayChief, retryRelayChiefMeta } from "../lib/capabilities";
  import { resourceCatalogue } from "../lib/resourceCatalogue";
  import { labelize } from "../lib/ui";
  import type { FieldStageVisualState } from "../lib/ui";
  import { lifecycleFor, type WorkerTypesResponse } from "../lib/lifecycle";
  import Button from "../components/Button.svelte";
  import ChatPanel from "../components/ChatPanel.svelte";
  import ChiefNeutralPane from "../components/ChiefNeutralPane.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import SectionHeading from "../components/SectionHeading.svelte";
  import StageMark from "../components/StageMark.svelte";
  import TicketRoute from "./TicketRoute.svelte";

  let { hideDone = $bindable(true), ticketId }: { hideDone?: boolean; ticketId?: string } = $props();

  const chiefOfStaffEntityId = "agent_panels_chief_of_staff";
  const noProjectKey = "__no_project__";
  const board = resourceCatalogue.board();
  // The legacy gateway-status resource is meaningful only on the legacy (disabled) path — a
  // Chief-addressed status call falls through EntityRoutingGateway to the worker gateway. Create
  // it lazily so the neutral/unknown/error branches never subscribe it (mirrors ChiefOfStaffRoute).
  let chiefChatStatus: ReturnType<typeof resourceCatalogue.chatGatewayStatus> | null = null;

  function legacyChiefChatStatus(): ReturnType<typeof resourceCatalogue.chatGatewayStatus> {
    if (chiefChatStatus === null) {
      chiefChatStatus = resourceCatalogue.chatGatewayStatus(chiefOfStaffEntityId);
    }
    return chiefChatStatus;
  }
  const manifest = resourceCatalogue.workerTypeManifests();
  let columns = $derived(board.data?.columns || []);
  let allCards = $derived(columns.flatMap((column) => column.cards));
  let selectedCard = $derived(allCards.find((card) => card.id === ticketId) || null);
  let rightPaneMode = $derived<"chief" | "ticket">(selectedCard ? "ticket" : "chief");
  let projectSections = $derived(buildProjectSections(columns, hideDone, manifest.data));

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

  function stageMarker(card: Record<string, any>, stageState: FieldStageVisualState): string | null {
    if (stageState === "current-running") return "agent-running-step";
    if (stageState === "errored") return "errored";
    if (stageState === "current-awaiting-approval" && card.has_pending_proposal) {
      return "pending-proposal";
    }
    if (stageState === "current-waiting" && card.ticket_status === "user_takeover") {
      return "user-takeover";
    }
    if (stageState === "current-paired-work") {
      return "paired-work";
    }
    return null;
  }

  // The board rail marks only the CURRENT stage, driven from the fields t_tt04a puts
  // on each card (gating_field, is_done, ticket_status, has_pending_proposal). The
  // manifest resource supplies only the registered worker-type label; it never drives
  // the mark. The current stage is the card's own gating field (null for a done/dropped
  // card → the "closeout" cosmetic fallback the e2e pins).
  function currentStageField(card: Record<string, any>): string {
    return card.gating_field || "closeout";
  }

  // The current stage's visual state: for the current gating field, ticketStageVisualState
  // reduces to "completed" when done, else the status/proposal classification — the fieldSlot
  // is never "passed" at its own gating state, so no manifest lookup is needed here.
  function currentStageState(card: Record<string, any>): FieldStageVisualState {
    if (card.is_done) return "completed";
    if (card.ticket_status === "agent_running_step") return "current-running";
    if (card.ticket_status === "errored") return "errored";
    if (card.ticket_status === "paired_work") return "current-paired-work";
    if (card.has_pending_proposal || card.ticket_status === "awaiting_approval") {
      return "current-awaiting-approval";
    }
    return "current-waiting";
  }

  function currentStageLabel(card: Record<string, any>): string {
    if (card.is_done || card.is_dropped) {
      return card.stage_label || labelize(card.stage);
    }
    return card.gating_field_label || labelize(currentStageField(card));
  }

  function activitySortValue(card: Record<string, any>): number {
    return Number(card.activity_at ?? 0);
  }

  function workerTypeSortValue(
    workerTypes: WorkerTypesResponse | undefined,
    card: Record<string, any>
  ): number {
    const index = workerTypes?.worker_types.findIndex(
      (workerType) => workerType.worker_type === card.worker_type
    );
    return index !== undefined && index >= 0 ? index : Number.MAX_SAFE_INTEGER;
  }

  function stageSortValue(
    workerTypes: WorkerTypesResponse | undefined,
    card: Record<string, any>
  ): number {
    const workerType = workerTypes?.worker_types.find(
      (candidate) => candidate.worker_type === card.worker_type
    );
    const index = workerType?.stages.findIndex((stage) => stage.id === card.stage);
    return index !== undefined && index >= 0 ? index : Number.MAX_SAFE_INTEGER;
  }

  function buildProjectSections(
    sourceColumns: Array<{ stage: string; cards: Record<string, any>[] }>,
    shouldHideDone: boolean,
    workerTypes: WorkerTypesResponse | undefined
  ): Array<{ key: string; label: string; cards: Record<string, any>[] }> {
    const groups = new Map<string, { key: string; label: string; cards: Record<string, any>[] }>();
    let sequence = 0;

    for (const column of sourceColumns) {
      for (const card of column.cards) {
        if (shouldHideDone && column.stage === "done") continue;
        const key = card.group_project_id || noProjectKey;
        const label = card.group_project || "No project";
        if (!groups.has(key)) groups.set(key, { key, label, cards: [] });
        groups.get(key)?.cards.push({
          ...card,
          stage: column.stage,
          boardSequence: sequence
        });
        sequence += 1;
      }
    }

    for (const group of groups.values()) {
      group.cards.sort((left, right) => {
        const workerTypeDelta =
          workerTypeSortValue(workerTypes, left) - workerTypeSortValue(workerTypes, right);
        if (workerTypeDelta) return workerTypeDelta;

        const stageDelta = stageSortValue(workerTypes, left) - stageSortValue(workerTypes, right);
        if (stageDelta) return stageDelta;

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
    chiefChatStatus?.dispose();
    manifest.dispose();
  });
</script>

<section class="board-screen" data-screen="workspace">
  <ResourceState
    error={board.error || manifest.error}
    loading={board.loading || manifest.loading}
    hasData={Boolean(board.data && manifest.data)}
    loadingText="Loading workspace..."
  >
    <div class="board-workspace-wrap">
      <div class="board-workspace-shell">
        <section class="board-workspace-left" aria-label="Workspace ticket tree">
          <div class="board-workspace-heading-row">
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
                  {@const cardLifecycle = lifecycleFor(manifest.data, card.worker_type)}
                  <button
                    type="button"
                    class="list-row list-row--board"
                    class:active={rightPaneMode === "ticket" && selectedCard?.id === card.id}
                    onclick={() => selectCard(card.id)}
                    data-card=""
                    data-ticket-id={card.id}
                    data-ticket-stage={card.stage}
                    data-ticket-status={card.ticket_status}
                  >
                    <span class="board-workspace-row-main">
                      <span class="list-row-title">{card.title}</span>
                      <span class="board-workspace-row-byline">
                        <span class="board-workspace-row-metadata">
                          <span class="board-workspace-row-type">
                            {cardLifecycle?.workerTypeLabel ?? labelize(card.worker_type)}
                          </span>
                          <span class="board-workspace-row-separator" aria-hidden="true"> · </span>
                          <span class="board-workspace-row-stage">{currentStageLabel(card)}</span>
                        </span>
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
                      </span>
                    </span>
                  </button>
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
              {#if $relayChief === "enabled"}
                <ChiefNeutralPane entityId={chiefOfStaffEntityId} label="Chief of Staff" />
              {:else if $relayChief === "disabled"}
                {@const status = legacyChiefChatStatus()}
                <ChatPanel
                  entityId={chiefOfStaffEntityId}
                  available={status.data?.available ?? true}
                  label="Chief of Staff"
                />
              {:else if $relayChief === "error"}
                <div class="chief-chat-placeholder" data-chief-meta-error>
                  <p>Could not load Chief of Staff.</p>
                  <button
                    type="button"
                    data-chief-meta-retry
                    onclick={() => void retryRelayChiefMeta()}
                  >
                    Retry
                  </button>
                </div>
              {:else}
                <div class="chief-chat-placeholder" data-chief-meta-loading></div>
              {/if}
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

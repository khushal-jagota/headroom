<script lang="ts">
  import { onDestroy } from "svelte";
  import { relayChief, retryRelayChiefMeta } from "../lib/capabilities";
  import { resourceCatalogue } from "../lib/resourceCatalogue";
  import { labelize } from "../lib/ui";
  import type { FieldStageVisualState } from "../lib/ui";
  import type { WorkerTypesResponse } from "../lib/lifecycle";
  import Button from "../components/Button.svelte";
  import ChatPanel from "../components/ChatPanel.svelte";
  import ChiefNeutralPane from "../components/ChiefNeutralPane.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import ResourceState from "../components/ResourceState.svelte";
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
    if (stageState === "current-paired-work") return "paired-work";
    return null;
  }

  // The stage heading carries position; the existing mark still carries the ticket's
  // current condition (waiting, running, needs approval, paired, errored, or complete).
  function currentStageField(card: Record<string, any>): string {
    return card.gating_field || "closeout";
  }

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

  type StageSection = {
    key: string;
    label: string;
    cards: Record<string, any>[];
  };

  type WorkerSection = {
    key: string;
    label: string;
    stages: StageSection[];
  };

  type ProjectSection = {
    key: string;
    label: string;
    workers: WorkerSection[];
  };

  function buildProjectSections(
    sourceColumns: Array<{ stage: string; cards: Record<string, any>[] }>,
    shouldHideDone: boolean,
    workerTypes: WorkerTypesResponse | undefined
  ): ProjectSection[] {
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

    const sortedProjects = Array.from(groups.values()).sort((left, right) => {
      if (left.key === noProjectKey) return 1;
      if (right.key === noProjectKey) return -1;
      return left.label.localeCompare(right.label, undefined, { sensitivity: "base" });
    });

    return sortedProjects.map((project) => {
      const cardsByWorker = new Map<string, Record<string, any>[]>();
      for (const card of project.cards) {
        if (!cardsByWorker.has(card.worker_type)) cardsByWorker.set(card.worker_type, []);
        cardsByWorker.get(card.worker_type)?.push(card);
      }

      const workers = Array.from(cardsByWorker.entries())
        .sort(([leftType, leftCards], [rightType, rightCards]) => {
          const manifestDelta =
            workerTypeSortValue(workerTypes, leftCards[0]) -
            workerTypeSortValue(workerTypes, rightCards[0]);
          return manifestDelta || leftType.localeCompare(rightType);
        })
        .map(([workerType, cards]) => {
          const cardsByStage = new Map<string, Record<string, any>[]>();
          for (const card of cards) {
            if (!cardsByStage.has(card.stage)) cardsByStage.set(card.stage, []);
            cardsByStage.get(card.stage)?.push(card);
          }

          const stages = Array.from(cardsByStage.entries())
            .sort(([leftStage, leftCards], [rightStage, rightCards]) => {
              const manifestDelta =
                stageSortValue(workerTypes, leftCards[0]) - stageSortValue(workerTypes, rightCards[0]);
              return manifestDelta || leftStage.localeCompare(rightStage);
            })
            .map(([stage, stageCards]) => ({
              key: stage,
              label: currentStageLabel(stageCards[0]),
              cards: stageCards.sort((left, right) => {
                const activityDelta = activitySortValue(right) - activitySortValue(left);
                return activityDelta || left.boardSequence - right.boardSequence;
              })
            }));

          const manifestWorker = workerTypes?.worker_types.find(
            (candidate) => candidate.worker_type === workerType
          );
          return {
            key: workerType,
            label: manifestWorker?.label ?? labelize(workerType),
            stages
          };
        });

      return { key: project.key, label: project.label, workers };
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

          {#each projectSections as project}
            <Disclosure
              variant="workspace-project"
              class="board-workspace-index-section"
              chevron="trailing"
              defaultOpen={true}
              data-project-section=""
              data-project-key={project.key}
            >
              {#snippet summary()}
                <span class="board-workspace-project-label">{project.label}</span>
              {/snippet}

              <div class="board-workspace-index-items">
                {#each project.workers as worker}
                  <Disclosure
                    variant="workspace-worker"
                    chevron="trailing"
                    defaultOpen={true}
                    data-worker-section=""
                    data-worker-type={worker.key}
                  >
                    {#snippet summary()}
                      <span class="board-workspace-worker-label">{worker.label}</span>
                    {/snippet}

                    <div class="board-workspace-worker-stages">
                      {#each worker.stages as stage}
                        <Disclosure
                          variant="workspace-stage"
                          chevron="trailing"
                          defaultOpen={true}
                          data-stage-section=""
                          data-stage-key={stage.key}
                        >
                          {#snippet summary()}
                            <span class="board-workspace-stage-label">{stage.label}</span>
                          {/snippet}

                          <div class="board-workspace-stage-tickets">
                            {#each stage.cards as card}
                              {@const stageField = currentStageField(card)}
                              {@const stageState = currentStageState(card)}
                              {@const marker = stageMarker(card, stageState)}
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
                                <span class="list-row-title">{card.title}</span>
                                <StageMark
                                  state={stageState}
                                  class="board-workspace-stage-mark"
                                  data-stage-field={stageField}
                                  data-stage-state={stageState}
                                  data-marker={marker || undefined}
                                  aria-label={`${stageField} ${stageState.replace(/-/g, " ")}`}
                                />
                              </button>
                            {/each}
                          </div>
                        </Disclosure>
                      {/each}
                    </div>
                  </Disclosure>
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

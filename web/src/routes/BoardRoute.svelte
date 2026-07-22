<script lang="ts">
  import { onDestroy } from "svelte";
  import { resourceCatalogue } from "../lib/resourceCatalogue";
  import { labelize } from "../lib/ui";
  import type { FieldStageVisualState } from "../lib/ui";
  import type { WorkerTypeManifest, WorkerTypesResponse } from "../lib/lifecycle";
  import AcpConversation from "../components/AcpConversation.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import StageMark from "../components/StageMark.svelte";
  import TicketRoute from "./TicketRoute.svelte";

  let { ticketId }: { ticketId?: string } = $props();

  const chiefOfStaffEntityId = "agent_panels_chief_of_staff";
  const noProjectKey = "__no_project__";
  const board = resourceCatalogue.board();
  const manifest = resourceCatalogue.workerTypeManifests();
  let columns = $derived(board.data?.columns || []);
  let allCards = $derived(columns.flatMap((column) => column.cards));
  let selectedCard = $derived(allCards.find((card) => card.id === ticketId) || null);
  let rightPaneMode = $derived<"chief" | "ticket">(selectedCard ? "ticket" : "chief");
  let projectSections = $derived(buildProjectSections(columns, manifest.data));

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

  type WorkspaceDotState = "exceptional" | "active" | "needs_attention" | "quiet";

  type WorkspaceDotPresentation = {
    state: FieldStageVisualState;
    marker: string | null;
    ariaLabel: string;
  };

  const workspaceDotPresentation: Record<WorkspaceDotState, WorkspaceDotPresentation> = {
    exceptional: {
      state: "errored",
      marker: "errored",
      ariaLabel: "Worker exception"
    },
    active: {
      state: "current-running",
      marker: "agent-running-step",
      ariaLabel: "Worker active"
    },
    needs_attention: {
      state: "current-awaiting-approval",
      marker: null,
      ariaLabel: "Worker needs attention"
    },
    quiet: {
      state: "current-waiting",
      marker: null,
      ariaLabel: "Worker quiet"
    }
  };

  // The stage heading carries position; the mark carries only the derived Workspace condition.
  function currentStageField(card: Record<string, any>): string {
    return card.gating_field || "closeout";
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

  function workspaceStage(card: Record<string, any>): string {
    if (card.stage === "needs_kickoff") return card.stage;
    return card.blocked ? "blocked" : card.stage;
  }

  function workspaceStageSortValue(
    workerTypes: WorkerTypesResponse | undefined,
    stage: string,
    card: Record<string, any>
  ): number {
    return stage === "blocked" ? -1 : stageSortValue(workerTypes, card);
  }

  function isTerminalStage(
    manifestWorker: WorkerTypeManifest | undefined,
    stage: string
  ): boolean {
    return Boolean(manifestWorker?.stages.find((candidate) => candidate.id === stage)?.is_terminal);
  }

  type StageSection = {
    key: string;
    label: string;
    // Terminal (Done) stages render collapsed by default; every other stage is open.
    // Keyed off the served manifest's is_terminal so it generalizes across worker types.
    collapsed: boolean;
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
    workerTypes: WorkerTypesResponse | undefined
  ): ProjectSection[] {
    const groups = new Map<string, { key: string; label: string; cards: Record<string, any>[] }>();
    let sequence = 0;

    for (const column of sourceColumns) {
      for (const card of column.cards) {
        const key = card.group_project_id || noProjectKey;
        const label = card.group_project || "No project";
        if (!groups.has(key)) groups.set(key, { key, label, cards: [] });
        groups.get(key)?.cards.push({
          ...card,
          stage: column.stage,
          workspace_stage: workspaceStage({ ...card, stage: column.stage }),
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
            if (!cardsByStage.has(card.workspace_stage)) cardsByStage.set(card.workspace_stage, []);
            cardsByStage.get(card.workspace_stage)?.push(card);
          }

          const manifestWorker = workerTypes?.worker_types.find(
            (candidate) => candidate.worker_type === workerType
          );

          const stages = Array.from(cardsByStage.entries())
            .sort(([leftStage, leftCards], [rightStage, rightCards]) => {
              const manifestDelta =
                workspaceStageSortValue(workerTypes, leftStage, leftCards[0]) -
                workspaceStageSortValue(workerTypes, rightStage, rightCards[0]);
              return manifestDelta || leftStage.localeCompare(rightStage);
            })
            .map(([stage, stageCards]) => ({
              key: stage,
              label: stage === "blocked" ? "Blocked" : currentStageLabel(stageCards[0]),
              collapsed: stage === "blocked" ? false : isTerminalStage(manifestWorker, stage),
              cards: stageCards.sort((left, right) => {
                const activityDelta = activitySortValue(right) - activitySortValue(left);
                return activityDelta || left.boardSequence - right.boardSequence;
              })
            }));

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
          <button
            type="button"
            class="board-workspace-chief-peer"
            class:active={rightPaneMode === "chief"}
            aria-pressed={rightPaneMode === "chief"}
            data-chief-of-staff-button=""
            onclick={showChiefOfStaff}
          >
            <span class="board-workspace-chief-peer-label">Chief of Staff</span>
          </button>

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
                          defaultOpen={!stage.collapsed}
                          data-stage-section=""
                          data-stage-key={stage.key}
                        >
                          {#snippet summary()}
                            <span class="board-workspace-stage-label">{stage.label}</span>
                          {/snippet}

                          <div class="board-workspace-stage-tickets">
                            {#each stage.cards as card}
                              {@const stageField = currentStageField(card)}
                              {@const workspaceDotState = (card.workspace_stage === "blocked"
                                ? "quiet"
                                : card.workspace_dot_state) as WorkspaceDotState}
                              {@const workspacePresentation = workspaceDotPresentation[workspaceDotState]}
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
                                  state={workspacePresentation.state}
                                  class="board-workspace-stage-mark"
                                  data-stage-field={stageField}
                                  data-stage-state={workspacePresentation.state}
                                  data-marker={workspacePresentation.marker || undefined}
                                  data-workspace-dot-state={workspaceDotState}
                                  aria-label={workspacePresentation.ariaLabel}
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
              <AcpConversation
                employeeId={chiefOfStaffEntityId}
                employeeLabel="Chief of Staff"
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

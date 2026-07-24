<script lang="ts">
  import { onDestroy } from "svelte";
  import { resourceCatalogue } from "../lib/resourceCatalogue";
  import type { FieldStageVisualState } from "../lib/ui";
  import AcpConversation from "../components/AcpConversation.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import StageMark from "../components/StageMark.svelte";
  import TicketRoute from "./TicketRoute.svelte";

  let { ticketId }: { ticketId?: string } = $props();

  const chiefOfStaffEntityId = "agent_panels_chief_of_staff";
  const board = resourceCatalogue.board();
  let columns = $derived(board.data?.columns || []);
  let allCards = $derived(
    columns.flatMap((column) =>
      column.cards.map(
        (card): Record<string, any> => ({ ...card, stage: column.stage })
      )
    )
  );
  let selectedCard = $derived(allCards.find((card) => card.id === ticketId) || null);
  let rightPaneMode = $derived<"chief" | "ticket">(selectedCard ? "ticket" : "chief");
  const ALL_PROJECTS = "__all_projects__";
  const NO_PROJECT = "__no_project__";
  let selectedProjectId = $state(ALL_PROJECTS);
  let projectOptions = $derived(
    Array.from(
      new Map(
        allCards
          .filter((card) => card.group_project_id)
          .map((card) => [String(card.group_project_id), String(card.group_project)])
      )
    )
      .map(([id, name]) => ({ id, name }))
      .sort((left, right) => left.name.localeCompare(right.name))
  );
  let rosterCards = $derived(
    selectedProjectId === ALL_PROJECTS
      ? allCards
      : allCards.filter((card) =>
          selectedProjectId === NO_PROJECT
            ? !card.group_project_id
            : card.group_project_id === selectedProjectId
        )
  );
  let buckets = $derived(buildBuckets(rosterCards));

  $effect(() => {
    const selectedProjectStillExists = projectOptions.some(
      (project) => project.id === selectedProjectId
    );
    if (
      selectedProjectId !== ALL_PROJECTS &&
      selectedProjectId !== NO_PROJECT &&
      !selectedProjectStillExists
    ) {
      selectedProjectId = ALL_PROJECTS;
    }
  });

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

  type AgentReplyState = "none" | "unseen" | "seen";

  type SignalPresentation = {
    state: FieldStageVisualState;
    ariaLabel: string;
  };

  // The row mark carries only the two signals: an agent working now wins the
  // mark; otherwise the reply state shows — accent while unseen, grey once
  // seen, the reduced ring when nothing is waiting.
  function signalPresentation(card: Record<string, any>): SignalPresentation {
    if (card.agent_working) {
      return { state: "current-running", ariaLabel: "Agent working" };
    }
    const reply = card.agent_reply_state as AgentReplyState;
    if (reply === "unseen") {
      return { state: "current-awaiting-approval", ariaLabel: "Unseen agent reply" };
    }
    if (reply === "seen") {
      return { state: "reply-seen", ariaLabel: "Agent reply seen" };
    }
    return { state: "upcoming", ariaLabel: "Nothing waiting" };
  }

  type BucketKey =
    | "errored"
    | "needs_you"
    | "kickoff"
    | "stopped"
    | "taken_over"
    | "paired"
    | "agent_working"
    | "needs_approval"
    | "closing_out"
    | "blocked"
    | "done";

  type BucketDefinition = {
    key: BucketKey;
    label: string;
    defaultCollapsed: boolean;
  };

  // Canonical order, top to bottom. A bucket with no tickets is not rendered.
  const BUCKET_DEFINITIONS: readonly BucketDefinition[] = [
    { key: "errored", label: "Errored", defaultCollapsed: false },
    { key: "needs_you", label: "Needs you", defaultCollapsed: false },
    { key: "kickoff", label: "Kickoff", defaultCollapsed: false },
    { key: "stopped", label: "Stopped", defaultCollapsed: false },
    { key: "taken_over", label: "Taken over", defaultCollapsed: false },
    { key: "paired", label: "Paired", defaultCollapsed: false },
    { key: "agent_working", label: "Agent working", defaultCollapsed: false },
    { key: "needs_approval", label: "Needs approval", defaultCollapsed: false },
    { key: "closing_out", label: "Closing out", defaultCollapsed: false },
    { key: "blocked", label: "Blocked", defaultCollapsed: true },
    { key: "done", label: "Done", defaultCollapsed: true }
  ];

  // Every ticket sits in exactly one bucket. Status decides first; Blocked
  // claims only idle tickets. The one exception: a kickoff-stage ticket with a
  // parked proposal belongs in Kickoff, not Needs approval.
  function bucketFor(card: Record<string, any>): BucketKey {
    if (card.is_done) return "done";
    const status = String(card.ticket_status);
    if (status === "errored") return "errored";
    if (status === "needs_user") return "needs_you";
    if (status === "awaiting_approval") {
      return card.stage === "needs_kickoff" ? "kickoff" : "needs_approval";
    }
    if (status === "proposal_discussion" || status === "paired_work") return "paired";
    if (status === "agent_running_step") return "agent_working";
    if (status === "user_takeover") return "taken_over";
    if (card.blocked) return "blocked";
    if (card.stage === "needs_kickoff") return "kickoff";
    if (card.stage === "needs_closeout") return "closing_out";
    return "stopped";
  }

  type BucketSection = {
    key: BucketKey;
    label: string;
    defaultCollapsed: boolean;
    cards: Record<string, any>[];
  };

  function buildBuckets(cards: Record<string, any>[]): BucketSection[] {
    const byBucket = new Map<BucketKey, Record<string, any>[]>();
    for (const card of cards) {
      const key = bucketFor(card);
      if (!byBucket.has(key)) byBucket.set(key, []);
      byBucket.get(key)?.push(card);
    }
    return BUCKET_DEFINITIONS.filter((definition) => byBucket.has(definition.key)).map(
      (definition) => ({
        key: definition.key,
        label: definition.label,
        defaultCollapsed: definition.defaultCollapsed,
        cards: (byBucket.get(definition.key) ?? []).sort((left, right) => {
          const activityDelta =
            Number(right.activity_at ?? 0) - Number(left.activity_at ?? 0);
          return activityDelta || String(left.id).localeCompare(String(right.id));
        })
      })
    );
  }

  onDestroy(() => {
    board.dispose();
  });
</script>

<section class="board-screen" data-screen="workspace">
  <ResourceState
    error={board.error}
    loading={board.loading}
    hasData={Boolean(board.data)}
    loadingText="Loading workspace..."
  >
    <div class="board-workspace-wrap">
      <div class="board-workspace-shell">
        <section class="board-workspace-left" aria-label="Workspace tickets by status">
          <label class="board-workspace-project-filter">
            <span>Project</span>
            <select bind:value={selectedProjectId} data-project-filter>
              <option value={ALL_PROJECTS}>All projects</option>
              {#each projectOptions as project (project.id)}
                <option value={project.id}>{project.name}</option>
              {/each}
              <option value={NO_PROJECT}>No project</option>
            </select>
          </label>

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

          {#each buckets as bucket (bucket.key)}
            <Disclosure
              variant="workspace-bucket"
              chevron="trailing"
              defaultOpen={!bucket.defaultCollapsed}
              data-bucket-section=""
              data-bucket-key={bucket.key}
            >
              {#snippet summary()}
                <span class="board-workspace-bucket-label">{bucket.label}</span>
              {/snippet}

              <div class="board-workspace-bucket-tickets">
                {#each bucket.cards as card (card.id)}
                  {@const presentation = signalPresentation(card)}
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
                      state={presentation.state}
                      class="board-workspace-stage-mark"
                      data-stage-state={presentation.state}
                      data-agent-working={card.agent_working ? "true" : "false"}
                      data-reply-state={card.agent_reply_state}
                      aria-label={presentation.ariaLabel}
                    />
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

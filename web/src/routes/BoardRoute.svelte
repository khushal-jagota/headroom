<script lang="ts">
  import { onMount } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../lib/queryCatalogue";
  import { labelize, type FieldStageVisualState } from "../lib/ui";
  import { onReplyWatermarkMoved, readReplyWatermark } from "../lib/replyWatermark";
  import ChiefConversation from "../components/ChiefConversation.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import StageMark from "../components/StageMark.svelte";
  import TicketRoute from "./TicketRoute.svelte";

  let { ticketId }: { ticketId?: string } = $props();

  const board = createQuery(() => queries.board());
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
  let groups = $derived(buildGroups(rosterCards));

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

  // The board is settled — loaded, no fetch in flight, and the last read
  // succeeded — and the ticket in the address is not on it, so the address is
  // stale: fall back to the board. A failed refetch leaves the previous board in
  // place, which is not evidence the ticket is gone, so it holds instead.
  $effect(() => {
    if (ticketId && board.data && !board.isFetching && !board.isError && !selectedCard) {
      window.location.replace("#/workspace");
    }
  });

  function selectCard(ticketId: string): void {
    window.location.hash = `#/workspace/${encodeURIComponent(ticketId)}`;
  }

  function showChiefOfStaff(): void {
    window.location.hash = "#/workspace";
  }

  type SignalPresentation = {
    state: FieldStageVisualState;
    ariaLabel: string;
  };

  // Reading happens in this browser and writes nothing, so a row redraws when the
  // watermark moves rather than when something refetches.
  let watermarkGeneration = $state(0);
  onMount(() => onReplyWatermarkMoved(() => (watermarkGeneration += 1)));

  // The row mark carries three signals in one precedence. A permission ask wins: a
  // turn waiting on an ask is still running, and the ask is the part only the user can
  // clear. Then an agent working now. Otherwise the reply shows — accent while a turn
  // has ended past where this browser has read, grey once it has been read, the reduced
  // ring for a conversation whose turns have never ended.
  function signalPresentation(card: Record<string, any>): SignalPresentation {
    if (card.needs_me) {
      return { state: "needs-me", ariaLabel: "Needs you" };
    }
    if (card.agent_working) {
      return { state: "current-running", ariaLabel: "Agent working" };
    }
    watermarkGeneration;
    const latestTurnEnded = Number(card.latest_turn_ended_sequence ?? 0);
    if (latestTurnEnded === 0 || card.conversation_id === null) {
      return { state: "upcoming", ariaLabel: "Nothing waiting" };
    }
    if (latestTurnEnded > readReplyWatermark(card.conversation_id)) {
      return { state: "current-awaiting-approval", ariaLabel: "Unseen agent reply" };
    }
    return { state: "reply-seen", ariaLabel: "Agent reply seen" };
  }

  // Every ticket sits in exactly one group: its own ticket status, except a done
  // ticket, which groups as done.
  function groupKeyFor(card: Record<string, any>): string {
    return card.is_done ? "done" : String(card.ticket_status);
  }

  // Presentation only: the top-to-bottom order of the status groups. A group with
  // no tickets is not rendered, and a status not named here appends as its own
  // group after these, in the order first seen.
  const GROUP_ORDER: readonly string[] = [
    "errored",
    "needs_user",
    "empty",
    "user",
    "paired",
    "agent",
    "awaiting_approval",
    "blocked",
    "done"
  ];

  const DEFAULT_COLLAPSED_GROUPS: ReadonlySet<string> = new Set(["blocked", "done"]);

  type GroupSection = {
    key: string;
    label: string;
    defaultCollapsed: boolean;
    cards: Record<string, any>[];
  };

  function buildGroups(cards: Record<string, any>[]): GroupSection[] {
    const byGroup = new Map<string, Record<string, any>[]>();
    const firstSeen: string[] = [];
    for (const card of cards) {
      const key = groupKeyFor(card);
      if (!byGroup.has(key)) {
        byGroup.set(key, []);
        firstSeen.push(key);
      }
      byGroup.get(key)?.push(card);
    }
    const orderedKeys = GROUP_ORDER.filter((key) => byGroup.has(key)).concat(
      firstSeen.filter((key) => !GROUP_ORDER.includes(key))
    );
    return orderedKeys.map((key) => ({
      key,
      label: labelize(key),
      defaultCollapsed: DEFAULT_COLLAPSED_GROUPS.has(key),
      cards: (byGroup.get(key) ?? []).sort((left, right) => {
        const activityDelta =
          Number(right.activity_at ?? 0) - Number(left.activity_at ?? 0);
        return activityDelta || String(left.id).localeCompare(String(right.id));
      })
    }));
  }

</script>

<section class="board-screen" data-screen="workspace">
  <ResourceState
    error={board.error}
    loading={board.isFetching}
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

          {#each groups as group (group.key)}
            <Disclosure
              variant="workspace-bucket"
              chevron="trailing"
              defaultOpen={!group.defaultCollapsed}
              data-bucket-section=""
              data-bucket-key={group.key}
            >
              {#snippet summary()}
                <span class="board-workspace-bucket-label">{group.label}</span>
              {/snippet}

              <div class="board-workspace-bucket-tickets">
                {#each group.cards as card (card.id)}
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
                      data-needs-me={card.needs_me ? "true" : "false"}
                      data-agent-working={card.agent_working ? "true" : "false"}
                      data-latest-turn-ended={card.latest_turn_ended_sequence}
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
              <ChiefConversation />
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

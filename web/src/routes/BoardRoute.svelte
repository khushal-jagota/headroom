<script lang="ts">
  import { onMount } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../lib/queryCatalogue";
  import { labelize } from "../lib/ui";
  import { conversationSignalPresentation } from "../lib/conversationSignalPresentation";
  import { onReplyWatermarkMoved, readReplyWatermark } from "../lib/replyWatermark";
  import ChiefConversation from "../components/ChiefConversation.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import PriorityTile from "../components/PriorityTile.svelte";
  import StageMark from "../components/StageMark.svelte";
  import TicketRoute from "./TicketRoute.svelte";
  import chiefOfStaffProfile from "../assets/chief-of-staff-profile.webp";

  let { ticketId }: { ticketId?: string } = $props();

  const board = createQuery(() => queries.board());
  const workers = createQuery(() => queries.workers());
  let columns = $derived(board.data?.columns || []);
  let allCards = $derived(
    columns.flatMap((column) =>
      column.cards.map(
        (card): Record<string, any> => ({ ...card, stage: column.stage })
      )
    )
  );
  let selectedCard = $derived(allCards.find((card) => card.id === ticketId) || null);
  let chiefSelected = $derived(ticketId === "chief-of-staff");
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
    if (
      ticketId &&
      !chiefSelected &&
      board.data &&
      !board.isFetching &&
      !board.isError &&
      !selectedCard
    ) {
      window.location.replace("#/workspace");
    }
  });

  function selectCard(ticketId: string): void {
    window.location.hash = window.matchMedia("(max-width: 960px)").matches
      ? `#/ticket/${encodeURIComponent(ticketId)}`
      : `#/workspace/${encodeURIComponent(ticketId)}`;
  }

  // The project filter is a mini header that opens a dropdown menu, not a form
  // control. The trigger shows the active project; the menu picks a new one.
  let projectMenuOpen = $state(false);
  let projectMenuElement = $state<HTMLDivElement | null>(null);
  let projectMenuButton = $state<HTMLButtonElement | null>(null);
  let activeProjectLabel = $derived(
    selectedProjectId === ALL_PROJECTS
      ? "All projects"
      : selectedProjectId === NO_PROJECT
        ? "No project"
        : projectOptions.find((project) => project.id === selectedProjectId)?.name ??
          "All projects"
  );

  function closeProjectMenu(): void {
    projectMenuOpen = false;
  }

  function chooseProject(projectId: string): void {
    selectedProjectId = projectId;
    closeProjectMenu();
    projectMenuButton?.focus();
  }

  function onProjectMenuWindowPointerDown(event: PointerEvent): void {
    if (
      projectMenuOpen &&
      projectMenuElement &&
      !projectMenuElement.contains(event.target as Node)
    ) {
      closeProjectMenu();
    }
  }

  function onProjectMenuWindowKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape" && projectMenuOpen) {
      closeProjectMenu();
      projectMenuButton?.focus();
    }
  }

  // How far this browser has read each conversation on the board.
  //
  // It is held here rather than read while a row is being drawn, and that is the whole
  // point: reading a conversation writes nothing the server can announce, so no refetch
  // is coming to redraw the board. Keeping the positions in state is what makes a row
  // redraw when one of them moves — and it is a value the marks visibly depend on, so
  // there is nothing here a tidy-up could remove without the dots going wrong loudly.
  let howFarThisBrowserHasRead = $state<Record<string, number>>({});

  function rereadWhereThisBrowserHasGot(): void {
    const positions: Record<string, number> = {};
    for (const column of board.data?.columns || []) {
      for (const card of column.cards) {
        const conversationId = card.conversation_id;
        if (typeof conversationId === "string") {
          positions[conversationId] = readReplyWatermark(conversationId);
        }
      }
    }
    const chiefConversationId = workers.data?.chief_of_staff.conversation_id;
    if (typeof chiefConversationId === "string") {
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

  // Two things move a position: this browser reading a conversation, and a board arriving
  // with conversations it has not seen before.
  onMount(() => onReplyWatermarkMoved(rereadWhereThisBrowserHasGot));
  $effect(() => {
    board.data;
    workers.data;
    rereadWhereThisBrowserHasGot();
  });

  // Every ticket sits in exactly one group: Done wins, a resting Closeout ticket
  // that the server says is runnable gets its server-projected semantic exception,
  // and Kickoff approvals use the existing gating field to split from later
  // approvals. Every other ticket uses its own status.
  function groupKeyFor(card: Record<string, any>): string {
    if (card.is_done) return "done";
    if (card.waiting_to_closeout) return "waiting_to_closeout";
    if (
      card.ticket_status === "awaiting_approval" &&
      card.gating_field === "kickoff"
    ) {
      return "waiting_for_kickoff";
    }
    return String(card.ticket_status);
  }

  // Presentation only: the top-to-bottom order of the status groups. A group with
  // no tickets is not rendered, and a status not named here appends as its own
  // group after these, in the order first seen.
  const GROUP_ORDER: readonly string[] = [
    "errored",
    "needs_user",
    "waiting_for_kickoff",
    "user",
    "paired",
    "agent",
    "waiting_to_closeout",
    "awaiting_approval",
    "empty",
    "blocked",
    "done"
  ];

  const DEFAULT_COLLAPSED_GROUPS: ReadonlySet<string> = new Set(["blocked", "done"]);
  const GROUP_LABELS: Readonly<Record<string, string>> = {
    waiting_to_closeout: "Waiting to Closeout",
    waiting_for_kickoff: "Waiting for Kickoff"
  };

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
      label: GROUP_LABELS[key] ?? labelize(key),
      defaultCollapsed: DEFAULT_COLLAPSED_GROUPS.has(key),
      cards: (byGroup.get(key) ?? []).sort((left, right) => {
        const activityDelta =
          Number(right.activity_at ?? 0) - Number(left.activity_at ?? 0);
        return activityDelta || String(left.id).localeCompare(String(right.id));
      })
    }));
  }

</script>

<svelte:window
  onpointerdown={onProjectMenuWindowPointerDown}
  onkeydown={onProjectMenuWindowKeydown}
/>

<section class="board-screen" data-screen="workspace">
  <ResourceState
    error={board.error}
    loading={board.isFetching}
    hasData={Boolean(board.data)}
    loadingText="Loading workspace..."
  >
    <div class="board-workspace-wrap">
      <div class="board-workspace-shell" class:board-workspace-shell--chief={chiefSelected}>
        <section class="board-workspace-left" aria-label="Workspace tickets by status">
          {#if workers.data}
            <a
              class="board-workspace-chief-row"
              class:active={chiefSelected}
              href="#/workspace/chief-of-staff"
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

          <div class="board-workspace-project-filter" bind:this={projectMenuElement}>
            <button
              type="button"
              class="board-workspace-project-filter-trigger"
              bind:this={projectMenuButton}
              aria-haspopup="menu"
              aria-expanded={projectMenuOpen}
              data-project-filter
              data-active-project-id={selectedProjectId}
              onclick={() =>
                projectMenuOpen ? closeProjectMenu() : (projectMenuOpen = true)}
            >
              <span class="board-workspace-project-filter-value">{activeProjectLabel}</span>
              <span class="board-workspace-project-filter-caret" aria-hidden="true"></span>
            </button>

            {#if projectMenuOpen}
              <div class="board-workspace-project-filter-menu" role="menu" data-project-menu>
                <button
                  type="button"
                  class="board-workspace-project-filter-item"
                  role="menuitemradio"
                  aria-checked={selectedProjectId === ALL_PROJECTS}
                  data-project-option={ALL_PROJECTS}
                  onclick={() => chooseProject(ALL_PROJECTS)}
                >
                  <span class="board-workspace-project-filter-tick" aria-hidden="true"></span>
                  All projects
                </button>
                {#each projectOptions as project (project.id)}
                  <button
                    type="button"
                    class="board-workspace-project-filter-item"
                    role="menuitemradio"
                    aria-checked={selectedProjectId === project.id}
                    data-project-option={project.id}
                    onclick={() => chooseProject(project.id)}
                  >
                    <span class="board-workspace-project-filter-tick" aria-hidden="true"></span>
                    {project.name}
                  </button>
                {/each}
                <button
                  type="button"
                  class="board-workspace-project-filter-item"
                  role="menuitemradio"
                  aria-checked={selectedProjectId === NO_PROJECT}
                  data-project-option={NO_PROJECT}
                  onclick={() => chooseProject(NO_PROJECT)}
                >
                  <span class="board-workspace-project-filter-tick" aria-hidden="true"></span>
                  No project
                </button>
              </div>
            {/if}
          </div>

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
                  {@const presentation = conversationSignalPresentation(
                    {
                      conversation_id:
                        typeof card.conversation_id === "string" ? card.conversation_id : null,
                      needs_me: Boolean(card.needs_me),
                      agent_working: Boolean(card.agent_working),
                      latest_turn_ended_sequence: Number(
                        card.latest_turn_ended_sequence ?? 0
                      )
                    },
                    howFarThisBrowserHasRead
                  )}
                  <button
                    type="button"
                    class="list-row list-row--board"
                    class:active={selectedCard?.id === card.id}
                    onclick={() => selectCard(card.id)}
                    data-card=""
                    data-ticket-id={card.id}
                    data-ticket-stage={card.stage}
                    data-ticket-status={card.ticket_status}
                  >
                    <PriorityTile priority={card.priority} />
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
          class:board-workspace-right--ticket={Boolean(selectedCard)}
          class:board-workspace-right--chief={chiefSelected}
          class="board-workspace-right"
          aria-label={chiefSelected ? "Chief of Staff conversation" : "Workspace inspector"}
        >
          {#if chiefSelected}
            <div class="board-workspace-chief-conversation">
              <ChiefConversation />
            </div>
          {:else if selectedCard}
            {#key selectedCard.id}
              <TicketRoute id={selectedCard.id} />
            {/key}
          {:else}
            <div class="board-workspace-empty-inspector">
              <span>Select a ticket to inspect it.</span>
            </div>
          {/if}
        </section>
      </div>
    </div>
  </ResourceState>
</section>

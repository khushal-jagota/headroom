<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { shortMonthDayLabel } from "../lib/dates";
  import { mutateJson } from "../lib/mutate";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import { queries } from "../lib/queryCatalogue";
  import { resourceStateForQueries } from "../lib/resourceStateForQueries";
  import {
    sprintItemIsDone,
    sprintItemRollup,
    sprintItems,
    sprintDayLabel,
    sprintProjectGroups,
    sprintTicketCondition,
    sprintTicketSectionsForTickets,
    type SprintItem,
    type SprintTicket
  } from "../lib/sprintPresentation";
  import type { AnyRecord } from "../lib/types";
  import Disclosure from "../components/Disclosure.svelte";
  import InlineEdit from "../components/InlineEdit.svelte";
  import MarkdownBlock from "../components/MarkdownBlock.svelte";
  import PriorityTile from "../components/PriorityTile.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import SprintItemWorkspace from "../components/SprintItemWorkspace.svelte";
  import StageMark from "../components/StageMark.svelte";

  let {
    sub = "tracking",
    selectedItemId = null
  }: { sub?: string; selectedItemId?: string | null } = $props();

  const current = createQuery(() => queries.currentSprint());
  const projects = createQuery(() => queries.projects());
  const today = createQuery(() => queries.todayDay());

  const kickoff = [
    ["limiting_factor", "Limiting factor"],
    ["primary_bet", "Primary bet"],
    ["supports", "Supports"],
    ["premortem", "Premortem"]
  ];
  const mid = [
    ["mid_where_we_stand", "Where we stand"],
    ["mid_whats_changed", "What's changed"],
    ["mid_what_to_adjust", "What to adjust"]
  ];
  const review = [
    ["outcomes", "Outcomes"],
    ["solo_reflection", "Solo reflection"],
    ["joint_discussion", "Joint discussion"],
    ["updates_to_thinking", "Updates to thinking"],
    ["carry_forward", "Carry forward"]
  ];

  let documents = $derived(sub === "documents");
  let resource = $derived(
    documents
      ? resourceStateForQueries(current)
      : resourceStateForQueries(current, projects, today)
  );
  let allItems = $derived(
    sprintItems((current.data?.groups || {}) as Record<string, SprintItem[]>)
  );
  let projectGroups = $derived(sprintProjectGroups(allItems, projects.data?.projects || []));
  let todayTicketIds = $derived(new Set((today.data?.tickets || []).map((ticket) => ticket.id)));
  let otherSections = $derived(
    sprintTicketSectionsForTickets(
      (current.data?.other_tickets || []) as SprintTicket[],
      todayTicketIds
    )
  );
  let otherTicketCount = $derived(
    otherSections.today.length + otherSections.later.length + otherSections.done.length
  );

  let summaryElement = $state<HTMLElement | null>(null);
  let summaryExpanded = $state(false);
  let summaryCanExpand = $state(false);
  let measuredSummaryKey = "";
  let summaryKey = $derived(`sprint:${current.data?.sprint?.primary_bet || ""}`);

  $effect(() => {
    const key = summaryKey;
    if (key === measuredSummaryKey) return;
    measuredSummaryKey = key;
    summaryExpanded = false;
    summaryCanExpand = false;
  });

  $effect(() => {
    const node = summaryElement;
    const key = summaryKey;
    if (!node) return;
    void key;
    const measure = () => {
      const lineHeight = Number.parseFloat(getComputedStyle(node).lineHeight);
      const maxHeight = Number.isFinite(lineHeight) ? lineHeight * 4.5 : node.clientHeight;
      summaryCanExpand = node.scrollHeight > maxHeight + 1;
    };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(node);
    const frame = requestAnimationFrame(measure);
    return () => {
      cancelAnimationFrame(frame);
      observer?.disconnect();
    };
  });

  function toggleSummary(): void {
    if (summaryCanExpand) summaryExpanded = !summaryExpanded;
  }

  function toggleSummaryFromKeyboard(event: KeyboardEvent): void {
    if (!summaryCanExpand || (event.key !== "Enter" && event.key !== " ")) return;
    event.preventDefault();
    toggleSummary();
  }

  function saveSprint(sprintId: string, field: string, raw: string): Promise<unknown> {
    return mutateJson(`/api/sprints/${sprintId}`, { method: "PATCH", body: { [field]: raw } });
  }

  function totalItems(groups: Record<string, AnyRecord[]>): number {
    return Object.values(groups || {}).flat().length;
  }

  function sprintDate(iso: string): string {
    const parsed = Date.parse(`${iso}T00:00:00`);
    return Number.isNaN(parsed) ? iso : shortMonthDayLabel(new Date(parsed));
  }

  function sectionHasContent(sprint: AnyRecord, fields: string[][]): boolean {
    return fields.some((field) => String(sprint[field[0]] || "").trim() !== "");
  }
</script>

{#snippet ticketRows(tickets: SprintTicket[])}
  {#each tickets as ticket (ticket.id)}
    {@const condition = sprintTicketCondition(ticket)}
    <a
      class="list-row sprint-ticket-row"
      class:sprint-ticket-row--settled={ticket.stage === "done"}
      href={workspaceAddress({ kind: "ticket", id: ticket.id })}
      data-sprint-ticket-id={ticket.id}
      data-ticket-state={condition.mark}
    >
      <StageMark state={condition.mark} aria-label={condition.word} />
      <span class="list-row-title">{ticket.title}</span>
      <span class="sprint-ticket-state">
        <span class="sprint-ticket-priority">{ticket.priority}</span>{#if ticket.stage !== "done"} · {condition.word}{/if}
      </span>
    </a>
  {/each}
{/snippet}

<section
  class="sprint-screen"
  class:sprint-screen--workspace={selectedItemId !== null}
  data-screen="sprint"
>
  <ResourceState
    error={resource.error}
    loading={resource.loading}
    hasData={resource.hasData}
    loadingText="Loading sprint..."
  >
    {#if !current.data?.sprint}
      <div class="quiet-line">No current sprint.</div>
    {:else}
      {@const sprint = current.data.sprint}
      {@const groups = current.data.groups || {}}
      {#if selectedItemId}
        <SprintItemWorkspace itemId={selectedItemId} sprintName={sprint.name} />
      {:else}
        <div class="doc">
          <div class="col">
          {#if documents}
            <a class="sprint-back" href="#/sprint">‹ {sprint.name}</a>
            <header class="sprint-docs-head">
              <h1 class="sprint-docs-title">Sprint documents</h1>
              <div class="sprint-docs-sub">Kickoff, Checkpoint, and sprint review — the sprint's written record.</div>
            </header>
            {@const reviewHas = sectionHasContent(sprint, review)}
            {@const midHas = sectionHasContent(sprint, mid)}
            {#each [
              { kind: "kickoff", name: "Kickoff", meta: "set at the start", open: !reviewHas && !midHas, fields: kickoff, refline: "" },
              { kind: "mid", name: "Checkpoint", meta: "day four", open: reviewHas || midHas, fields: mid, refline: "" },
              { kind: "review", name: "Sprint Review", meta: "end of sprint", open: reviewHas, fields: review, refline: "Written with the Checkpoint above in view — it is the raw material for this retrospective." }
            ] as phase}
              <Disclosure variant="phase" data-phase={phase.kind} defaultOpen={phase.open}>
                {#snippet summary()}
                  <span class="pnm">{phase.name}</span>
                  <span class="pmeta">{phase.meta}</span>
                {/snippet}
                {#if phase.refline}<div class="refline">{phase.refline}</div>{/if}
                {#each phase.fields as field}
                  <div class="field" data-field={field[0]}>
                    <div class="flabel">{field[1]}</div>
                    <div class="fval">
                      <InlineEdit
                        value={sprint[field[0]]}
                        markdown
                        multiline
                        placeholder="(none)"
                        onSave={(raw) => saveSprint(sprint.id, field[0], raw)}
                      />
                    </div>
                  </div>
                {/each}
              </Disclosure>
            {/each}
          {:else}
            <header class="sprint-head">
              <h1 class="sprint-title">
                <InlineEdit
                  value={sprint.name}
                  placeholder="(unnamed sprint)"
                  onSave={(raw) => saveSprint(sprint.id, "name", raw)}
                />
              </h1>
              {#if String(sprint.primary_bet || "").trim()}
                <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
                <div
                  bind:this={summaryElement}
                  class="ticket-recap sprint-summary"
                  class:ticket-recap--clamped={!summaryExpanded}
                  class:sprint-summary--expandable={summaryCanExpand}
                  role={summaryCanExpand ? "button" : undefined}
                  tabindex={summaryCanExpand ? 0 : undefined}
                  aria-expanded={summaryCanExpand ? summaryExpanded : undefined}
                  onclick={toggleSummary}
                  onkeydown={toggleSummaryFromKeyboard}
                  data-sprint-bet
                ><MarkdownBlock text={sprint.primary_bet} /></div>
              {/if}
              <div class="sprint-meta-line">
                <span>{sprintDate(sprint.date_start)} – {sprintDate(sprint.date_end)}</span>
                <span class="sep">·</span>
                <span>{sprintDayLabel(sprint, current.data.planning_date)}</span>
                <span class="sep">·</span>
                <span>{allItems.filter(sprintItemIsDone).length} of {totalItems(groups)} items done</span>
                <a class="sprint-docs-link" href="#/sprint/documents">Sprint documents ›</a>
              </div>
            </header>

            <div class="sprint-projects" data-sprint-projects>
              {#each projectGroups as group (group.key)}
                <Disclosure
                  variant="workspace-bucket"
                  defaultOpen={true}
                  data-sprint-project={group.key}
                  data-project-priority={group.priority || ""}
                >
                  {#snippet summary()}
                    <span class="board-workspace-bucket-label">{group.label}</span>
                    <span class="board-workspace-bucket-count" aria-label={`${group.items.length} Sprint Items`}>
                      {group.items.length}
                    </span>
                  {/snippet}
                  <div class="sprint-project-items">
                    {#each group.items as item (item.id)}
                      <a
                        class="list-row sprint-item-row"
                        class:sprint-item-row--settled={sprintItemIsDone(item)}
                        href={`#/sprint?item=${encodeURIComponent(item.id)}`}
                        data-item-id={item.id}
                        data-item-status={item.status}
                      >
                        <PriorityTile priority={item.priority} />
                        <span class="list-row-title">{item.title}</span>
                        <span class="sprint-item-rollup">{sprintItemRollup(item)}</span>
                      </a>
                    {/each}
                  </div>
                </Disclosure>
              {/each}
              {#if otherTicketCount}
                <Disclosure
                  variant="workspace-bucket"
                  defaultOpen={true}
                  data-sprint-other
                >
                  {#snippet summary()}
                    <span class="board-workspace-bucket-label">Other</span>
                    <span class="board-workspace-bucket-count" aria-label={`${otherTicketCount} Tickets`}>
                      {otherTicketCount}
                    </span>
                  {/snippet}
                  <div class="sprint-project-items" data-sprint-other-tickets>
                    {@render ticketRows(otherSections.today)}
                    {@render ticketRows(otherSections.later)}
                    {@render ticketRows(otherSections.done)}
                  </div>
                </Disclosure>
              {/if}
            </div>
          {/if}
          </div>
        </div>
      {/if}
    {/if}
  </ResourceState>
</section>

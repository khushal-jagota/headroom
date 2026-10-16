<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { shortMonthDayLabel } from "../lib/dates";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import { queries } from "../lib/queryCatalogue";
  import { resourceStateForQueries } from "../lib/resourceStateForQueries";
  import {
    outcomeTicketProgress,
    sprintProjectGroups,
    sprintTicketCondition,
    sprintTicketSectionsForTickets,
    type SprintTicket
  } from "../lib/sprintPresentation";
  import ClampedText from "../components/ClampedText.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import MarkdownBlock from "../components/MarkdownBlock.svelte";
  import PriorityTile from "../components/PriorityTile.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import StageMark from "../components/StageMark.svelte";

  let {
    sub = "tracking",
    selectedItemId = null,
    sprintId = null
  }: { sub?: string; selectedItemId?: string | null; sprintId?: string | null } = $props();

  const current = createQuery(() => sprintId ? queries.sprintTracking(sprintId) : queries.currentSprint());
  const projects = createQuery(() => queries.projects());

  let documents = $derived(sub === "documents");
  // Historical Sprint content is complete on its own. The optional project catalogue
  // can enrich stored project labels, but neither it nor a live Day gates this record.
  let resource = $derived(resourceStateForQueries(current));
  let projectGroups = $derived(sprintProjectGroups(current.data?.outcome_groups || [], projects.data?.projects || []));
  let looseTicketSections = $derived(
    sprintTicketSectionsForTickets(
      (current.data?.unclassified_tickets || []) as SprintTicket[],
      new Set<string>()
    )
  );
  let looseTickets = $derived([
    ...looseTicketSections.today,
    ...looseTicketSections.later,
    ...looseTicketSections.done
  ]);
  let looseTicketProgress = $derived(`${looseTicketSections.done.length}/${looseTickets.length}`);

  // The bet is its own control: there is no "Show more" under it, the text itself opens
  // and closes, and a new bet arrives closed.
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

  function toggleSummary(): void {
    if (summaryCanExpand) summaryExpanded = !summaryExpanded;
  }

  function toggleSummaryFromKeyboard(event: KeyboardEvent): void {
    if (!summaryCanExpand || (event.key !== "Enter" && event.key !== " ")) return;
    event.preventDefault();
    toggleSummary();
  }

  function sprintDate(iso: string): string {
    const parsed = Date.parse(`${iso}T00:00:00`);
    return Number.isNaN(parsed) ? iso : shortMonthDayLabel(new Date(parsed));
  }
  function sprintHref(sprintId: string, suffix = ""): string {
    return `#/sprint${suffix}?sprint=${encodeURIComponent(sprintId)}`;
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
      {#if condition.mark}
        <StageMark state={condition.mark} aria-label="Unread reply" />
      {/if}
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
      <div class="quiet-line">No sprint record.</div>
    {:else}
      {@const sprint = current.data.sprint}
      {#if selectedItemId}
        <div class="doc"><div class="col">
          <a class="sprint-back" href={sprintHref(sprint.id)}>‹ {sprint.name}</a>
          <a href={workspaceAddress({ kind:"item", id:selectedItemId })} data-historical-orchestrator>Open orchestrator ›</a>
        </div></div>
      {:else}
        <div class="doc">
          <div class="col">
          {#if documents}
            <a class="sprint-back" href={sprintHref(sprint.id)}>‹ {sprint.name}</a>
            <header class="sprint-docs-head">
              <h1 class="sprint-docs-title">Sprint documents</h1>
              <div class="sprint-docs-sub">Kickoff, Checkpoint, and sprint review — the sprint's written record.</div>
            </header>
            <div class="field" data-field="primary_bet">
              <div class="flabel">Primary bet</div>
              <div class="fval">
                <MarkdownBlock text={sprint.primary_bet} />
              </div>
            </div>
            {@const reviewHas = sprint.review.trim() !== ""}
            {@const checkpointHas = sprint.checkpoint.trim() !== ""}
            {#each [
              { field: "kickoff" as const, name: "Kickoff", meta: "set at the start", open: !reviewHas && !checkpointHas },
              { field: "checkpoint" as const, name: "Checkpoint", meta: "day four", open: reviewHas || checkpointHas },
              { field: "review" as const, name: "Sprint Review", meta: "end of sprint", open: reviewHas }
            ] as document}
              <Disclosure variant="phase" data-phase={document.field} defaultOpen={document.open}>
                {#snippet summary()}
                  <span class="pnm">{document.name}</span>
                  <span class="pmeta">{document.meta}</span>
                {/snippet}
                <div class="field" data-field={document.field}>
                  <MarkdownBlock text={sprint[document.field]} />
                </div>
              </Disclosure>
            {/each}
          {:else}
            <header class="sprint-head">
              <h1 class="sprint-title">
                {sprint.name || "(unnamed sprint)"}
              </h1>
              {#if String(sprint.primary_bet || "").trim()}
                <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
                <ClampedText
                  class={summaryCanExpand
                    ? "sprint-summary sprint-summary--expandable"
                    : "sprint-summary"}
                  lines={4.5}
                  contentKey={summaryKey}
                  clampEvenWhenItFits
                  moreControl={false}
                  bind:expanded={summaryExpanded}
                  bind:canExpand={summaryCanExpand}
                  role={summaryCanExpand ? "button" : undefined}
                  tabindex={summaryCanExpand ? 0 : undefined}
                  aria-expanded={summaryCanExpand ? summaryExpanded : undefined}
                  onclick={toggleSummary}
                  onkeydown={toggleSummaryFromKeyboard}
                  data-sprint-bet
                ><MarkdownBlock text={sprint.primary_bet} /></ClampedText>
              {/if}
              <div class="sprint-meta-line">
                <span>{sprintDate(sprint.date_start)} – {sprintDate(sprint.date_end)}</span>
                <a class="sprint-docs-link" href={sprintHref(sprint.id, "/documents")}>Sprint documents ›</a>
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
                  {/snippet}
                  <div class="sprint-project-items">
                    {#each group.outcomes as outcomeGroup (outcomeGroup.outcome.id)}
                      {@const outcome = outcomeGroup.outcome}
                      <div class="sprint-outcome-block" data-outcome-id={outcome.id} data-committed={outcomeGroup.committed}>
                        <a class="list-row sprint-item-row" href={workspaceAddress({ kind:"item", id:outcome.id })}>
                          <PriorityTile priority={outcome.priority} /><span class="list-row-title">{outcome.title}</span>
                          <span class="sprint-item-rollup">{outcomeTicketProgress(outcomeGroup)}</span>
                        </a>
                      </div>
                    {/each}
                  </div>
                </Disclosure>
              {/each}
              {#if looseTickets.length}
                <details class="sprint-no-outcome" data-sprint-no-outcome>
                  <summary class="list-row sprint-item-row">
                    <span class="priority-tile priority-tile--blank" aria-hidden="true"></span>
                    <span class="list-row-title">No Outcome</span>
                    <span class="sprint-item-rollup">{looseTicketProgress}</span>
                  </summary>
                  <div class="sprint-project-items" data-sprint-no-outcome-tickets>
                    {@render ticketRows(looseTickets)}
                  </div>
                </details>
              {/if}
            </div>
          {/if}
          </div>
        </div>
      {/if}
    {/if}
  </ResourceState>
</section>

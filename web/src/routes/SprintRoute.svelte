<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { shortMonthDayLabel } from "../lib/dates";
  import { mutateJson } from "../lib/mutate";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import { queries } from "../lib/queryCatalogue";
  import { resourceStateForQueries } from "../lib/resourceStateForQueries";
  import {
    outcomeTicketProgress,
    sprintDayLabel,
    sprintProjectGroups,
    sprintTicketCondition,
    sprintTicketSectionsForTickets,
    type SprintTicket
  } from "../lib/sprintPresentation";
  import type { OutcomeSummary } from "../lib/types";
  import ClampedText from "../components/ClampedText.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import InlineEdit from "../components/InlineEdit.svelte";
  import MarkdownBlock from "../components/MarkdownBlock.svelte";
  import PriorityTile from "../components/PriorityTile.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import SprintItemWorkspace from "../components/SprintItemWorkspace.svelte";
  import StageMark from "../components/StageMark.svelte";
  import OutcomePicker from "../components/OutcomePicker.svelte";
  import Button from "../components/Button.svelte";

  let {
    sub = "tracking",
    selectedItemId = null,
    sprintId = null
  }: { sub?: string; selectedItemId?: string | null; sprintId?: string | null } = $props();

  const current = createQuery(() => sprintId ? queries.sprintTracking(sprintId) : queries.currentSprint());
  const projects = createQuery(() => queries.projects());
  const today = createQuery(() => queries.todayDay());

  let documents = $derived(sub === "documents");
  let resource = $derived(
    documents
      ? resourceStateForQueries(current)
      : resourceStateForQueries(current, projects, today)
  );
  let projectGroups = $derived(sprintProjectGroups(current.data?.outcome_groups || [], projects.data?.projects || []));
  let todayTicketIds = $derived(new Set((today.data?.tickets || []).map((ticket) => ticket.id)));
  let looseTicketSections = $derived(
    sprintTicketSectionsForTickets(
      (current.data?.unclassified_tickets || []) as SprintTicket[],
      todayTicketIds
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

  function saveSprint(sprintId: string, field: string, raw: string): Promise<unknown> {
    return mutateJson(`/api/sprints/${sprintId}`, { method: "PATCH", body: { [field]: raw } });
  }

  let addOutcomeOpen = $state(false);

  async function commitOutcome(sprintId: string, outcome: OutcomeSummary): Promise<void> {
    await mutateJson(`/api/collections/sprint_outcomes/${encodeURIComponent(sprintId)}/${encodeURIComponent(outcome.id)}`, { method: "PUT" });
    addOutcomeOpen = false;
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
      {#if selectedItemId}
        <SprintItemWorkspace itemId={selectedItemId} sprintName={sprint.name} backHref={sprintHref(sprint.id)} />
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
                <InlineEdit
                  value={sprint.primary_bet}
                  markdown
                  multiline
                  ariaLabel="Primary bet"
                  placeholder="What matters this sprint?"
                  onSave={(raw) => saveSprint(sprint.id, "primary_bet", raw)}
                />
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
                  <InlineEdit
                    value={sprint[document.field]}
                    markdown
                    multiline
                    ariaLabel={document.name}
                    placeholder="Write here..."
                    onSave={(raw) => saveSprint(sprint.id, document.field, raw)}
                  />
                </div>
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
                {#if sprintDayLabel(sprint, current.data.planning_date)}
                  <span class="sep">·</span>
                  <span>{sprintDayLabel(sprint, current.data.planning_date)}</span>
                {/if}
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
                        <a class="list-row sprint-item-row" href={`${sprintHref(sprint.id)}&item=${encodeURIComponent(outcome.id)}`}>
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
              <div class="sprint-outcome-actions"><Button onclick={() => (addOutcomeOpen = !addOutcomeOpen)}>Add outcome</Button></div>
              {#if addOutcomeOpen}
                <OutcomePicker projects={projects.data?.projects || []} chooseLabel="Add" onChoose={(outcome) => commitOutcome(sprint.id, outcome)} />
              {/if}
            </div>
          {/if}
          </div>
        </div>
      {/if}
    {/if}
  </ResourceState>
</section>

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
  import type { OutcomeSummary, SprintOutcomeGroup } from "../lib/types";
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
  import ErrorLine from "../components/ErrorLine.svelte";

  let {
    sub = "tracking",
    selectedItemId = null
  }: { sub?: string; selectedItemId?: string | null } = $props();

  const current = createQuery(() => queries.currentSprint());
  const projects = createQuery(() => queries.projects());
  const today = createQuery(() => queries.todayDay());
  const sprintSummaries = createQuery(() => queries.sprintSummaries());

  let documents = $derived(sub === "documents");
  let resource = $derived(
    documents
      ? resourceStateForQueries(current)
      : resourceStateForQueries(current, projects, today)
  );
  let projectGroups = $derived(sprintProjectGroups(current.data?.outcome_groups || [], projects.data?.projects || []));
  let todayTicketIds = $derived(new Set((today.data?.tickets || []).map((ticket) => ticket.id)));
  let otherSections = $derived(
    sprintTicketSectionsForTickets(
      (current.data?.unclassified_tickets || []) as SprintTicket[],
      todayTicketIds
    )
  );
  let otherTicketCount = $derived(
    otherSections.today.length + otherSections.later.length + otherSections.done.length
  );

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
  let actionError = $state<unknown>(null);
  let carryOutcome = $state<SprintOutcomeGroup | null>(null);
  let carryTargetSprintId = $state("");
  let carryTicketIds = $state<string[]>([]);

  async function commitOutcome(sprintId: string, outcome: OutcomeSummary): Promise<void> {
    await mutateJson(`/api/sprints/${encodeURIComponent(sprintId)}/outcomes/${encodeURIComponent(outcome.id)}`, { method: "PUT" });
    addOutcomeOpen = false;
  }
  async function removeOutcome(sprintId: string, outcomeId: string): Promise<void> {
    actionError = null;
    try { await mutateJson(`/api/sprints/${encodeURIComponent(sprintId)}/outcomes/${encodeURIComponent(outcomeId)}`, { method: "DELETE" }); }
    catch (error) { actionError = error; }
  }
  function openCarry(group: SprintOutcomeGroup): void {
    carryOutcome = group; carryTargetSprintId = ""; carryTicketIds = [];
  }
  function toggleCarryTicket(ticketId: string): void {
    carryTicketIds = carryTicketIds.includes(ticketId) ? carryTicketIds.filter((id) => id !== ticketId) : [...carryTicketIds, ticketId];
  }
  async function submitCarry(sourceSprintId: string): Promise<void> {
    if (!carryOutcome || !carryTargetSprintId) return;
    actionError = null;
    try {
      await mutateJson(`/api/sprints/${encodeURIComponent(sourceSprintId)}/outcomes/${encodeURIComponent(carryOutcome.outcome.id)}/carry`, { method: "POST", body: { target_sprint_id: carryTargetSprintId, ticket_ids: carryTicketIds } });
      carryOutcome = null;
    } catch (error) { actionError = error; }
  }

  function sprintDate(iso: string): string {
    const parsed = Date.parse(`${iso}T00:00:00`);
    return Number.isNaN(parsed) ? iso : shortMonthDayLabel(new Date(parsed));
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
                <span class="sep">·</span>
                <span>{sprintDayLabel(sprint, current.data.planning_date)}</span>
                <span class="sep">·</span>
                <span>{current.data.outcome_groups.filter((group) => group.committed).length} committed outcomes</span>
                <a class="sprint-docs-link" href="#/sprint/documents">Sprint documents ›</a>
              </div>
            </header>

            <div class="sprint-projects" data-sprint-projects>
              {#if actionError}<ErrorLine error={actionError} />{/if}
              <div class="sprint-outcome-actions"><Button onclick={() => (addOutcomeOpen = !addOutcomeOpen)}>Add outcome</Button></div>
              {#if addOutcomeOpen}
                <OutcomePicker projects={projects.data?.projects || []} chooseLabel="Add" onChoose={(outcome) => commitOutcome(sprint.id, outcome)} />
              {/if}
              {#each projectGroups as group (group.key)}
                <Disclosure
                  variant="workspace-bucket"
                  defaultOpen={true}
                  data-sprint-project={group.key}
                  data-project-priority={group.priority || ""}
                >
                  {#snippet summary()}
                    <span class="board-workspace-bucket-label">{group.label}</span>
                    <span class="board-workspace-bucket-count" aria-label={`${group.outcomes.length} Outcomes`}>
                      {group.outcomes.length}
                    </span>
                  {/snippet}
                  <div class="sprint-project-items">
                    {#each group.outcomes as outcomeGroup (outcomeGroup.outcome.id)}
                      {@const outcome = outcomeGroup.outcome}
                      <div class="sprint-outcome-block" data-outcome-id={outcome.id} data-committed={outcomeGroup.committed}>
                        <a class="list-row sprint-item-row" href={`#/sprint?item=${encodeURIComponent(outcome.id)}`}>
                          <PriorityTile priority={outcome.priority} /><span class="list-row-title">{outcome.title}</span>
                          <span class="sprint-item-rollup">{outcomeTicketProgress(outcomeGroup)}</span>
                        </a>
                        <div class="sprint-outcome-meta">{outcomeGroup.committed ? "Committed outcome" : "Other work"}</div>
                        {#if outcomeGroup.committed}
                          <div class="sprint-outcome-menu"><Button onclick={() => void removeOutcome(sprint.id, outcome.id)}>Remove from Sprint</Button><Button onclick={() => openCarry(outcomeGroup)}>Carry forward</Button></div>
                        {/if}
                        <div class="sprint-project-items">{@render ticketRows(outcomeGroup.tickets)}</div>
                      </div>
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
                    <span class="board-workspace-bucket-label">Other work</span>
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
              {#if carryOutcome}
                <section class="carry-outcome" data-carry-outcome={carryOutcome.outcome.id}>
                  <h2>Carry {carryOutcome.outcome.title}</h2>
                  <select class="in" aria-label="Target Sprint" bind:value={carryTargetSprintId}>
                    <option value="">Choose target Sprint</option>
                    {#each sprintSummaries.data?.sprints || [] as target}
                      {#if target.id !== sprint.id}<option value={target.id}>{target.name}</option>{/if}
                    {/each}
                  </select>
                  {#each carryOutcome.tickets.filter((ticket) => ticket.stage !== "done" && ticket.stage !== "dropped") as ticket}
                    <label class="carry-ticket"><input type="checkbox" checked={carryTicketIds.includes(ticket.id)} onchange={() => toggleCarryTicket(ticket.id)} /> {ticket.title}</label>
                  {/each}
                  <div class="foot"><Button onclick={() => (carryOutcome = null)}>Cancel</Button><Button variant="primary" disabled={!carryTargetSprintId} onclick={() => void submitCarry(sprint.id)}>Carry selected</Button></div>
                </section>
              {/if}
            </div>
          {/if}
          </div>
        </div>
      {/if}
    {/if}
  </ResourceState>
</section>

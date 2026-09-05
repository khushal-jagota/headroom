<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import type { OutcomeSummary, ProjectSummary } from "../lib/types";
  import Button from "./Button.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import PriorityTile from "./PriorityTile.svelte";
  import ResourceState from "./ResourceState.svelte";

  let {
    projects,
    onChoose,
    chooseLabel = "Add",
    allowCreate = true
  }: {
    projects: ProjectSummary[];
    onChoose: (outcome: OutcomeSummary) => Promise<void> | void;
    chooseLabel?: string;
    allowCreate?: boolean;
  } = $props();

  const PAGE_SIZE = 30;
  let offset = $state(0);
  let projectId = $state("");
  let search = $state("");
  let title = $state("");
  let body = $state("");
  let creating = $state(false);
  let choosingId = $state<string | null>(null);
  let error = $state<unknown>(null);
  let createdOutcome = $state<OutcomeSummary | null>(null);
  const outcomes = createQuery(() => queries.outcomeSummaries(offset, PAGE_SIZE, projectId, search.trim()));

  async function choose(outcome: OutcomeSummary): Promise<void> {
    choosingId = outcome.id;
    error = null;
    try { await onChoose(outcome); } catch (caught) { error = caught; } finally { choosingId = null; }
  }

  async function createOutcome(): Promise<void> {
    if (!title.trim() || !projectId || createdOutcome) return;
    creating = true;
    error = null;
    try {
      const created = await mutateJson<OutcomeSummary>("/api/items", {
        method: "POST",
        body: { title: title.trim(), body, project_id: projectId }
      });
      createdOutcome = created;
      await choose(created);
    } catch (caught) {
      error = caught;
    } finally { creating = false; }
  }

  function retryCreatedOutcome(): void {
    if (createdOutcome) void choose(createdOutcome);
  }
</script>

<div class="outcome-picker" data-outcome-picker>
  {#if error}<ErrorLine {error} />{/if}
  <div class="outcome-picker-filters">
    <input class="in" type="search" placeholder="Search outcomes" bind:value={search} oninput={() => (offset = 0)} data-outcome-search />
    <select class="in" aria-label="Project" bind:value={projectId} onchange={() => (offset = 0)} data-outcome-project>
      <option value="">All projects</option>
      {#each projects as project}<option value={project.id}>{project.name}</option>{/each}
    </select>
  </div>
  {#if createdOutcome}
    <div class="quiet-line" data-created-outcome-id={createdOutcome.id}>Created “{createdOutcome.title}”.</div>
    <Button disabled={choosingId !== null} onclick={retryCreatedOutcome} data-retry-created-outcome>Retry {chooseLabel.toLowerCase()}</Button>
  {/if}
  <ResourceState error={outcomes.error} loading={outcomes.isFetching} hasData={Boolean(outcomes.data)} loadingText="Loading outcomes...">
    <div class="outcome-picker-list">
      {#each outcomes.data?.items || [] as outcome (outcome.id)}
        <button class="list-row outcome-picker-row" type="button" onclick={() => void choose(outcome)} disabled={choosingId !== null} data-outcome-id={outcome.id}>
          <PriorityTile priority={outcome.priority} />
          <span class="list-row-title">{outcome.title}</span><span class="quiet-line">{outcome.project}</span>
        </button>
      {/each}
    </div>
  </ResourceState>
  {#if outcomes.data?.page && !outcomes.data.page.complete}
    <div class="outcome-picker-pages">
      <Button disabled={offset === 0} onclick={() => (offset = Math.max(0, offset - PAGE_SIZE))}>Previous</Button>
      <span class="quiet-line">{outcomes.data.page.omitted_before + 1}–{outcomes.data.page.omitted_before + outcomes.data.page.return_count} of {outcomes.data.page.match_count}</span>
      <Button disabled={outcomes.data.page.next_offset === null} onclick={() => { const next = outcomes.data?.page.next_offset; if (next !== null && next !== undefined) offset = next; }}>Next</Button>
    </div>
  {/if}
  {#if allowCreate}
    <details class="outcome-picker-new" data-new-outcome>
      <summary>New outcome</summary>
      <div class="form">
        <input class="in" placeholder="Outcome title" bind:value={title} />
        <textarea class="in" rows="3" placeholder="Brief — optional" bind:value={body}></textarea>
        <select class="in" aria-label="New outcome project" bind:value={projectId}>
          <option value="">Choose a project</option>
          {#each projects as project}<option value={project.id}>{project.name}</option>{/each}
        </select>
        <Button variant="primary" disabled={creating || createdOutcome !== null || !title.trim() || !projectId} onclick={() => void createOutcome()}>Create and {chooseLabel.toLowerCase()}</Button>
      </div>
    </details>
  {/if}
</div>

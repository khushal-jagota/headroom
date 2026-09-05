<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import { labelize, PRIORITY_ORDER } from "../lib/ui";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import type {
    ListPageFacts,
    TicketSummary
  } from "../lib/types";
  import Button from "../components/Button.svelte";
  import Chip from "../components/Chip.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import ListRow from "../components/ListRow.svelte";
  import Pill from "../components/Pill.svelte";
  import PriorityTile from "../components/PriorityTile.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import ScreenHeader from "../components/ScreenHeader.svelte";
  import SectionHeading from "../components/SectionHeading.svelte";
  import SegmentedControl from "../components/SegmentedControl.svelte";

  const PAGE_SIZE = 30;

  let ticketOffset = $state(0);
  let itemOffset = $state(0);
  const tickets = createQuery(() => queries.backlogTicketSummaries(ticketOffset, PAGE_SIZE));
  const items = createQuery(() => queries.backlogSprintItemSummaries(itemOffset, PAGE_SIZE));
  const projects = createQuery(() => queries.projects());
  const workerTypes = createQuery(() => queries.workerTypeManifests());

  const priorityOptions = [
    { value: null, label: "Project default" },
    ...PRIORITY_ORDER.map((value) => ({ value, label: value }))
  ];

  let title = $state("");
  let project = $state<string | null>(null);
  let priority = $state<string | null>(null);
  let workerType = $state<string | null>(null);
  let deadline = $state("");
  let kickoffNote = $state("");
  let createError = $state<unknown>(null);
  let creating = $state(false);

  let ticketGroups = $derived.by(() => {
    const byPriority: Record<string, TicketSummary[]> = {};
    for (const value of PRIORITY_ORDER) byPriority[value] = [];
    for (const ticket of tickets.data?.tickets || []) {
      (byPriority[ticket.priority] || (byPriority[ticket.priority] = [])).push(ticket);
    }
    return byPriority;
  });
  let projectOptions = $derived(
    (projects.data?.projects || []).map((entry) => ({ value: entry.id, label: entry.name }))
  );
  let availableWorkerTypes = $derived(workerTypes.data?.worker_types || []);

  $effect(() => {
    const values = projectOptions.map((option) => option.value);
    if ((!project || !values.includes(project)) && values.length) {
      project = values[0];
    }
  });

  $effect(() => {
    const values = availableWorkerTypes.map((entry) => entry.worker_type);
    if ((!workerType || !values.includes(workerType)) && values.length) {
      workerType = values[0];
    }
  });

  function pageRange(page: ListPageFacts): string {
    if (page.return_count === 0) return `0 of ${page.match_count}`;
    return `${page.omitted_before + 1}–${page.omitted_before + page.return_count} of ${page.match_count}`;
  }

  function previousOffset(page: ListPageFacts): number {
    return Math.max(0, page.offset - page.limit);
  }

  function ticketTitle(ticket: TicketSummary): string {
    return ticket.recap_preview
      ? `${ticket.title} — ${ticket.recap_preview}`
      : ticket.title;
  }

  async function createTicket(): Promise<void> {
    if (!title.trim() || !project || !workerType) return;
    creating = true;
    createError = null;
    const payload: Record<string, unknown> = {
      worker_type: workerType,
      title: title.trim(),
      kickoff_note: kickoffNote,
      project_id: project,
      priority,
      sprint_id: null,
      sprint_item_id: null
    };
    if (deadline.trim()) payload.deadline = deadline.trim();
    try {
      await mutateJson("/api/tickets", { method: "POST", body: payload });
      title = "";
      deadline = "";
      kickoffNote = "";
    } catch (err) {
      createError = err;
    } finally {
      creating = false;
    }
  }
</script>

<section class="backlog-screen" data-screen="backlog">
  <div class="doc">
    <ScreenHeader title="Backlog">
      {#snippet meta()}
        <Pill keyLabel="tickets">{tickets.data?.page.match_count || 0}</Pill>
      {/snippet}
    </ScreenHeader>
    <div class="col">
      <Disclosure variant="make" chevron="none" data-create="ticket">
        {#snippet summary()}<span class="plus">+</span> New ticket{/snippet}
        <div class="form">
          {#if createError || projects.error || workerTypes.error}
            <ErrorLine error={createError || projects.error || workerTypes.error} />
          {/if}
          <input class="in title-in" type="text" placeholder="What needs doing?" data-input="title" bind:value={title} />
          <textarea class="in detail-in" rows="2" placeholder="Kickoff context — optional" data-input="kickoff-note" bind:value={kickoffNote}></textarea>
          <div class="foot">
            <select class="in" aria-label="Worker type" data-input="worker-type" bind:value={workerType}>
              {#each availableWorkerTypes as entry (entry.worker_type)}
                <option value={entry.worker_type}>{entry.label}</option>
              {/each}
            </select>
            <SegmentedControl name="project" options={projectOptions} bind:value={project} />
            <SegmentedControl name="priority" options={priorityOptions} bind:value={priority}>
              {#snippet optionContent(option)}
                <PriorityTile priority={option.label} />
              {/snippet}
            </SegmentedControl>
            <input class="in due-in" type="text" placeholder="due YYYY-MM-DD — optional" data-input="deadline" bind:value={deadline} />
            <div class="spacer"></div>
            <Button variant="primary" data-commit="" disabled={creating || !title.trim() || !project || !workerType} onclick={() => void createTicket()}>Add ticket</Button>
          </div>
        </div>
      </Disclosure>

      <section class="groups" data-backlog-tickets>
        <SectionHeading label="Tickets" count={tickets.data?.page.match_count || 0} />
        <ResourceState error={tickets.error} loading={tickets.isFetching} hasData={Boolean(tickets.data)} loadingText="Loading tickets...">
          {#if !(tickets.data?.tickets || []).length}
            <div class="quiet-line">No active unscheduled tickets.</div>
          {:else}
            {#each PRIORITY_ORDER as value}
              {#if ticketGroups[value]?.length}
                <div class="grp" data-priority-group={value}>
                  <SectionHeading count={ticketGroups[value].length}>
                    {#snippet labelContent()}<PriorityTile priority={value} />{/snippet}
                  </SectionHeading>
                  {#each ticketGroups[value] as ticket (ticket.id)}
                    <ListRow
                      variant="backlog"
                      title={ticketTitle(ticket)}
                      href={workspaceAddress({ kind: "ticket", id: ticket.id })}
                      data-ticket-id={ticket.id}
                    >
                      {#snippet trailing()}
                        <span class="chips">
                          {#if ticket.project}<Chip variant="project" value={ticket.project} />{/if}
                          <Chip variant="state" value={ticket.ticket_status} />
                          <Chip value={labelize(ticket.worker_type)} />
                        </span>
                      {/snippet}
                    </ListRow>
                  {/each}
                </div>
              {/if}
            {/each}
          {/if}
        </ResourceState>
        {#if tickets.data?.page}
          {@const page = tickets.data.page}
          <div class="form" data-pagination="tickets">
            <div class="foot">
              <Button disabled={page.offset === 0} onclick={() => (ticketOffset = previousOffset(page))}>Previous</Button>
              <span class="quiet-line" data-page-range>{pageRange(page)}</span>
              <Button disabled={page.next_offset === null} onclick={() => {
                if (page.next_offset !== null) ticketOffset = page.next_offset;
              }}>Next</Button>
            </div>
          </div>
        {/if}
      </section>

      <section class="groups" data-backlog-items>
        <SectionHeading label="Unscheduled briefs" count={items.data?.page.match_count || 0} />
        <ResourceState error={items.error} loading={items.isFetching} hasData={Boolean(items.data)} loadingText="Loading briefs...">
          {#if !(items.data?.items || []).length}
            <div class="quiet-line">No unscheduled briefs.</div>
          {:else}
            <div class="grp">
              {#each items.data?.items || [] as item (item.id)}
                <ListRow
                  variant="backlog"
                  title={item.title}
                  href={workspaceAddress({ kind: "item", id: item.id })}
                  data-item-id={item.id}
                >
                  {#snippet leading()}<PriorityTile priority={item.priority} />{/snippet}
                  {#snippet trailing()}
                    <span class="chips">
                      <Chip variant="project" value={item.project} />
                      {#if item.deadline}<Chip variant="deadline" value={item.deadline} />{/if}
                    </span>
                  {/snippet}
                </ListRow>
              {/each}
            </div>
          {/if}
        </ResourceState>
        {#if items.data?.page}
          {@const page = items.data.page}
          <div class="form" data-pagination="items">
            <div class="foot">
              <Button disabled={page.offset === 0} onclick={() => (itemOffset = previousOffset(page))}>Previous</Button>
              <span class="quiet-line" data-page-range>{pageRange(page)}</span>
              <Button disabled={page.next_offset === null} onclick={() => {
                if (page.next_offset !== null) itemOffset = page.next_offset;
              }}>Next</Button>
            </div>
          </div>
        {/if}
      </section>
    </div>
  </div>
</section>

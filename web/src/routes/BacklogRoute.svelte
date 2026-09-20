<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import { PRIORITY_ORDER } from "../lib/ui";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import type { ListPageFacts, Principal, TicketSummary } from "../lib/types";
  import { OWNER_HOLDER, holderOptionsFor, holderFromValue, holderValue } from "../lib/ceilingHolder";
  import Button from "../components/Button.svelte";
  import Chip from "../components/Chip.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import ListRow from "../components/ListRow.svelte";
  import PriorityTile from "../components/PriorityTile.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import ScreenHeader from "../components/ScreenHeader.svelte";
  import SectionHeading from "../components/SectionHeading.svelte";
  import SegmentedControl from "../components/SegmentedControl.svelte";

  const PAGE_SIZE = 30;

  let ticketOffset = $state(0);
  const tickets = createQuery(() => queries.backlogTicketSummaries(ticketOffset, PAGE_SIZE));
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
  // Who is asked when this Ticket reaches its ceiling. A Ticket made here has no Sprint
  // Item, so the choice is me or the Chief. It starts at me, which is who is making it.
  let holder = $state<Principal>(OWNER_HOLDER);
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
      sprint_item_id: null,
      ceiling_holder: holder
    };
    if (deadline.trim()) payload.deadline = deadline.trim();
    try {
      await mutateJson("/api/tickets", { method: "POST", body: payload });
      title = "";
      deadline = "";
      kickoffNote = "";
      // Who a Ticket is for is a fact about that Ticket, not a mode the form stays in.
      holder = OWNER_HOLDER;
    } catch (err) {
      createError = err;
    } finally {
      creating = false;
    }
  }

</script>

<section class="backlog-screen" data-screen="backlog">
  <div class="doc">
    <ScreenHeader title="Backlog" />
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
            <label class="in-holder">
              <span class="in-holder-word">then</span>
              <select
                class="in"
                aria-label="Who holds the ceiling"
                data-input="ceiling-holder"
                value={holderValue(holder)}
                onchange={(event) => (holder = holderFromValue(event.currentTarget.value, null))}
              >
                {#each holderOptionsFor(null, holder) as option}
                  <option value={option.value}>{option.label}</option>
                {/each}
              </select>
            </label>
            <div class="spacer"></div>
            <Button variant="primary" data-commit="" disabled={creating || !title.trim() || !project || !workerType} onclick={() => void createTicket()}>Add ticket</Button>
          </div>
        </div>
      </Disclosure>

      <section class="groups" data-backlog-tickets>
        <ResourceState error={tickets.error} loading={tickets.isFetching} hasData={Boolean(tickets.data)} loadingText="Loading tickets...">
          {#if !(tickets.data?.tickets || []).length}
            <div class="quiet-line">No active unscheduled tickets.</div>
          {:else}
            {#each PRIORITY_ORDER as value}
              {#if ticketGroups[value]?.length}
                <div class="grp" data-priority-group={value}>
                  <SectionHeading>
                    {#snippet labelContent()}<PriorityTile priority={value} />{/snippet}
                  </SectionHeading>
                  {#each ticketGroups[value] as ticket (ticket.id)}
                    <ListRow
                      variant="backlog"
                      title={ticket.title}
                      href={workspaceAddress({ kind: "ticket", id: ticket.id })}
                      data-ticket-id={ticket.id}
                    >
                      {#snippet trailing()}
                        <span class="chips">
                          {#if ticket.project}<Chip variant="project" value={ticket.project} />{/if}
                        </span>
                      {/snippet}
                    </ListRow>
                  {/each}
                </div>
              {/if}
            {/each}
          {/if}
        </ResourceState>
        {#if tickets.data?.page && !tickets.data.page.complete}
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
    </div>
  </div>
</section>

<script lang="ts">
  import { untrack } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { railPreview } from "../lib/workspaceRailInteraction";
  import { queries } from "../lib/queryCatalogue";
  import { filterOrchestratorChildren } from "../lib/orchestratorChildren";
  import { orchestratorPageGroups } from "../lib/orchestratorPage";
  import { workItemTicketOrder, type WorkItemTicketFacts } from "../lib/workItemPresentation";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import type { OrchestratorPrincipal, OrchestratorChildFilter, SprintItemWorkspaceTicket } from "../lib/types";
  import Disclosure from "./Disclosure.svelte";
  import ResourceState from "./ResourceState.svelte";
  import SprintTicketRow from "./SprintTicketRow.svelte";

  let { principal, tickets, filter, onHover, onLeave }: {
    principal: OrchestratorPrincipal;
    tickets?: readonly SprintItemWorkspaceTicket[];
    filter: OrchestratorChildFilter;
    onHover?: (element: HTMLElement, ticketId: string, immediate?: boolean) => void;
    onLeave: () => void;
  } = $props();
  let offset = $state(0);
  let groupOpen = $state<Record<string, boolean>>({});
  let root = $state<HTMLElement | null>(null);
  const children = createQuery(() => ({
    ...queries.orchestratorTicketSummaries(principal, filter, offset),
    enabled: tickets === undefined
  }));
  let rows = $derived(tickets === undefined ? children.data?.tickets ?? [] : filterOrchestratorChildren(tickets, filter));
  let page = $derived(tickets === undefined ? children.data?.page : undefined);
  let count = $derived(page?.match_count ?? rows.length);
  let label = $derived(filter === "backlogged" ? "Backlogged" : "Done");
  let groups = $derived(filter === "active" ? orchestratorPageGroups<WorkItemTicketFacts>(rows) : []);
  $effect(() => {
    // A membership change can empty the last page. Return to the last remaining page.
    if (page && page.return_count === 0 && page.offset > 0 && !children.isFetching) {
      offset = Math.max(0, Math.floor((page.match_count - 1) / page.limit) * page.limit);
    }
  });
  $effect.pre(() => {
    groups;
    // Native toggle events are queued. Capture the DOM before a refresh removes a group.
    untrack(() => {
      for (const group of root?.querySelectorAll<HTMLDetailsElement>("details[data-workspace-group]") ?? []) {
        const key = group.dataset.workspaceGroup;
        if (key) groupOpen[key] = group.open;
      }
    });
  });
</script>

<div bind:this={root} data-worker-section={filter}>
  {#snippet ticketRows(items: readonly WorkItemTicketFacts[])}
    <div class="sprint-workspace-status-body">
      {#each items as ticket (ticket.id)}
        <div use:railPreview={onHover ? {show:(element, immediate) => onHover?.(element, ticket.id, immediate), leave:onLeave} : null}>
          <SprintTicketRow title={ticket.title} signal={ticket.conversation_signal} conversationId={ticket.conversation_id}
            quiet={ticket.stage === "done"}
            href={workspaceAddress(principal.kind === "sprint_item"
              ? { kind: "ticket", id: ticket.id, openedFromItemId: principal.id } : { kind: "ticket", id: ticket.id })}
            data-sprint-ticket-id={ticket.id} />
        </div>
      {/each}
    </div>
  {/snippet}
  {#snippet contents()}
    <ResourceState error={tickets === undefined ? children.error : null} loading={tickets === undefined && children.isLoading}
      hasData={tickets !== undefined || children.data !== undefined} loadingText={`Loading ${filter === "active" ? "Workers" : label}…`}>
      {#if filter === "active"}
        {#each groups as group (group.key)}
          <Disclosure variant="orchestrator-group" class="sprint-workspace-status" data-workspace-group={group.key}
            defaultOpen={groupOpen[group.key] ?? true} onReaderToggle={(open) => groupOpen[group.key] = open} chevron="none">
            {#snippet summary()}
              <span class="sprint-workspace-status-label">{group.label}</span>
              <span class="sprint-workspace-count" data-group-count>{group.tickets.length}</span>
            {/snippet}
            {@render ticketRows(group.tickets)}
          </Disclosure>
        {/each}
      {:else}
        {@render ticketRows([...rows].sort(workItemTicketOrder))}
      {/if}
      {#if rows.length === 0}<div class="sprint-workspace-empty">No {filter === "active" ? "active Workers" : label === "Done" ? "completed Workers" : "backlogged Workers"}</div>{/if}
      {#if page && (page.omitted_before > 0 || page.omitted_after > 0)}
        <nav class="child-pages" aria-label={`${filter === "active" ? "Active" : label} Worker pages`}>
          <button type="button" disabled={page.omitted_before === 0} onclick={() => offset = Math.max(0, page!.offset - page!.limit)}>Previous</button>
          <span>{page.return_count ? page.omitted_before + 1 : 0}–{page.omitted_before + page.return_count} of {page.match_count}</span>
          <button type="button" disabled={page.next_offset === null} onclick={() => offset = page!.next_offset ?? offset}>Next</button>
        </nav>
      {/if}
    </ResourceState>
  {/snippet}
  {#if filter === "active"}
    {@render contents()}
  {:else}
    <Disclosure variant="orchestrator-group" class="sprint-workspace-status" data-workspace-group={filter === "backlogged" ? "backlog" : "done"}
      defaultOpen={false} chevron="none">
      {#snippet summary()}
        <span class="sprint-workspace-status-label">{label}</span>
        <span class="sprint-workspace-count" data-group-count>{count}</span>
      {/snippet}
      {@render contents()}
    </Disclosure>
  {/if}
</div>

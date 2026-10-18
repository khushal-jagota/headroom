<script lang="ts">
  import { railPreview } from "../lib/workspaceRailInteraction";
  import { onDestroy, tick } from "svelte";
  import { navigationApplication } from "../lib/navigation/application.svelte";
  import { orchestratorChildrenImpact } from "../lib/workspaceNavigationImpact";
  import { childrenDomMatches } from "../lib/orchestratorWorkspaceDom";
  import { chiefChildrenPage, filterOrchestratorChildren, orchestratorGroupDefaultOpen } from "../lib/orchestratorChildren";
  import { orchestratorPageGroups } from "../lib/orchestratorPage";
  import type { WorkItemTicketFacts } from "../lib/workItemPresentation";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import type { OrchestratorPrincipal, OrchestratorChildFilter } from "../lib/types";
  import Disclosure from "./Disclosure.svelte";
  import ResourceState from "./ResourceState.svelte";
  import SprintTicketRow from "./SprintTicketRow.svelte";

  let { principal, tickets, publication, navigationErrorHandledByParent = false, onHover = undefined, onLeave = () => undefined }: {
    principal: OrchestratorPrincipal;
    navigationErrorHandledByParent?: boolean;
    publication?: import("../lib/navigation/collection").NavigationPublication;
    tickets?: readonly (WorkItemTicketFacts & {is_backlogged:boolean})[];
    onHover?: (element: HTMLElement, ticketId: string, immediate?: boolean) => void;
    onLeave?: () => void;
  } = $props();
  let filter = $state<OrchestratorChildFilter>("active");
  let offset = $state(0);
  let groupOpen = $state<Record<string, boolean>>({});
  let groupRoot = $state<HTMLElement | null>(null);
  const filters: { value: OrchestratorChildFilter; label: string }[] = [
    { value: "active", label: "Active" }, { value: "backlogged", label: "Backlogged" },
    { value: "done", label: "Done" }
  ];
  const navigation=navigationApplication();
  const observer=navigation?.registerRenderBoundary(`orchestrator-children:${crypto.randomUUID()}`,impact => {
    const view=navigation?.state.effective;
    return orchestratorChildrenImpact(impact,principal,view && navigation ? navigation.presentationFor(view) : undefined,rows,groups,groupOpen);
  });
  onDestroy(()=>{if(observer) navigation?.disposeRenderBoundary(observer);});
  let effective=$state.raw<import('../lib/navigation/knowledge').EffectiveNavigationView | undefined>(undefined);
  $effect(()=>{const view=navigation?.selectors.effective;if(view) effective=view;});
  let shared=$derived(effective && navigation ? navigation.presentationFor(effective) : undefined);
  let chiefPage=$derived(shared ? chiefChildrenPage(shared.ticketsByParent.get('chief:chief') ?? [],filter,offset) : undefined);
  let rows = $derived(tickets === undefined ? chiefPage?.tickets ?? [] : filterOrchestratorChildren(tickets, filter));
  let groups = $derived(orchestratorPageGroups<WorkItemTicketFacts>(rows));
  let page = $derived(tickets === undefined ? chiefPage?.page : undefined);
  let scope = $derived(`${principal.kind}:${principal.id}`);
  let previousScope = $state("");
  $effect(() => {
    if (scope !== previousScope) {
      previousScope = scope;
      filter = "active";
      offset = 0;
      groupOpen = {};
    }
  });
  function chooseFilter(value: string): void {
    // Native toggle events are queued. Save the DOM choice before filtering removes it.
    for (const group of groupRoot?.querySelectorAll<HTMLDetailsElement>("details[data-workspace-group]") ?? []) {
      const key = group.dataset.workspaceGroup;
      if (key) groupOpen[key] = group.open;
    }
    filter = value as OrchestratorChildFilter;
    offset = 0;
  }
  $effect(()=>{
    const view=effective;
    const root=groupRoot;
    const generation=navigation?.state.generation;
    const expectedGroups=groups;
    const expectedPage=page;
    const expectedPrincipal=principal;
    const suppliedPublication=publication;
    const expectedFilter=filter;
    const open={...groupOpen};
    if(!navigation || !observer || !view?.runtimeCurrent || (tickets!==undefined && suppliedPublication!==view.publication) || navigation.selectors.effective!==view || !root || generation===undefined) return;
    const observation=observer.observeSelectors(view.publication,generation);
    let obsolete=false;
    void tick().then(()=>{
      if(obsolete || !root.isConnected || navigation.state.generation!==generation || navigation.selectors.effective!==view) return;
      if(childrenDomMatches(root,expectedPrincipal,expectedFilter,expectedGroups,expectedPage,open))
        observer.acknowledgeRender(view.publication,observation);
    });
    return ()=>{obsolete=true;};
  });

</script>

<section bind:this={groupRoot} class="orchestrator-children" aria-label="Workers" data-orchestrator-children={scope}>
  <label class="child-filter">Workers
    <select value={filter} onchange={(event) => chooseFilter(event.currentTarget.value)} aria-label="Worker state">
      {#each filters as option}<option value={option.value}>{option.label}</option>{/each}
    </select>
  </label>
  <ResourceState error={tickets === undefined && !navigationErrorHandledByParent ? navigation?.state.error : null} loading={tickets === undefined && !shared}
    hasData={tickets !== undefined || shared !== undefined} loadingText="Loading Workers…">
    {#each groups as group (`${scope}:${group.key}`)}
      <Disclosure variant="orchestrator-group" class="sprint-workspace-status" data-workspace-group={group.key}
        defaultOpen={groupOpen[group.key] ?? orchestratorGroupDefaultOpen(group.key)}
        onReaderToggle={(open) => groupOpen[group.key] = open} chevron="none">
        {#snippet summary()}
          <span class="sprint-workspace-status-label">{group.label}</span>
          <span class="sprint-workspace-count" data-group-count>{group.tickets.length}</span>
        {/snippet}
        <div class="sprint-workspace-status-body">
          {#each group.tickets as ticket (ticket.id)}
            <div use:railPreview={onHover ? {show:(element, immediate) => onHover?.(element, ticket.id, immediate), leave:onLeave} : null}>
            <SprintTicketRow priority={null} title={ticket.title} signal={ticket.conversation_signal} conversationId={ticket.conversation_id}
              quiet={ticket.stage === "done"}
              href={workspaceAddress(principal.kind === "sprint_item"
                ? { kind: "ticket", id: ticket.id, openedFromItemId: principal.id } : { kind: "ticket", id: ticket.id })}
              data-sprint-ticket-id={ticket.id} />
            </div>
          {/each}
        </div>
      </Disclosure>
    {/each}
    {#if !groups.length}<div class="sprint-workspace-empty">No Workers</div>{/if}
    {#if page && (page.omitted_before > 0 || page.omitted_after > 0)}
      <nav class="child-pages" aria-label="Worker pages">
        <button type="button" disabled={page.omitted_before === 0} onclick={() => offset = Math.max(0, page!.offset - page!.limit)}>Previous</button>
        <span>{page.omitted_before + 1}–{page.omitted_before + page.return_count} of {page.match_count}</span>
        <button type="button" disabled={page.next_offset === null} onclick={() => offset = page!.next_offset ?? offset}>Next</button>
      </nav>
    {/if}
  </ResourceState>
</section>
<style>
  .orchestrator-children :global(.sprint-workspace-status + .sprint-workspace-status) { margin-top:0; }
  .orchestrator-children :global(.sprint-workspace-status > summary) { display:flex; align-items:center; gap:var(--space-3); padding:7px var(--space-3); margin:0; font-family:var(--font-ui); line-height:1.6; letter-spacing:normal; border:0; border-radius:var(--radius-md); background:var(--surface-2); font-size:13px; font-weight:600; color:var(--text-faint); list-style:none; }
  .orchestrator-children :global(.sprint-workspace-status > summary:hover) { background-image:var(--interaction-hover); color:var(--text-strong); }
  .sprint-workspace-status-label { font-size:13px; text-transform:none; letter-spacing:normal; color:inherit; }
  .orchestrator-children :global(.sprint-workspace-status[open] > summary > .sprint-workspace-count) { display:block; }
  .sprint-workspace-count { margin-left:auto; font-size:13px; font-weight:400; color:var(--text-faintest); }
  .sprint-workspace-status-body { padding:var(--space-1) 0; }
  .orchestrator-children :global(.stage-mark) { width:7px; height:7px; }
  .orchestrator-children :global(a:focus-visible), .orchestrator-children :global(button:focus-visible), .orchestrator-children :global(summary:focus-visible) { outline:var(--border-strong) solid var(--accent-bright); outline-offset:2px; }
  .orchestrator-children :global(.ticket-row) { width:100%; margin:0; padding:7px var(--space-3); gap:var(--space-3); border-radius:var(--radius-md); font-size:15px; line-height:1.6; align-items:center; color:var(--text-muted); }
  .orchestrator-children :global(.ticket-row:hover), .orchestrator-children :global(.ticket-row:focus-visible) { color:var(--text-strong); }
  .orchestrator-children :global(.ticket-row-title) { flex:1; min-width:0; display:block; overflow:visible; white-space:normal; overflow-wrap:anywhere; line-height:inherit; line-clamp:unset; -webkit-line-clamp:unset; -webkit-box-orient:initial; }
  .child-filter, .child-pages { display:flex; align-items:center; gap:var(--space-3); color:var(--text-faint); font-family:var(--font-ui); font-size:var(--type-sm); }
  .child-filter { margin-bottom:var(--space-3); }
  .child-filter select, .child-pages button { color:var(--text-default); background:var(--surface-2); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-sm); padding:var(--space-1) var(--space-2); font:inherit; }
  .child-pages { margin-top:var(--space-3); }
</style>

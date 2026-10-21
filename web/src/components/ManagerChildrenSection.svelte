<script lang="ts">
  import { untrack, tick, onDestroy } from "svelte";
  import { navigationApplication } from "../lib/navigation/application.svelte";
  import { managerChildrenImpact } from "../lib/workspaceNavigationImpact";
  import { childrenSectionDomMatches } from "../lib/managerSectionDom";
  import { railPreview } from "../lib/workspaceRailInteraction";
  import { chiefChildrenPage, filterManagerChildren } from "../lib/managerChildren";
  import { managerPageGroups } from "../lib/managerPage";
  import { WORK_ITEM_TICKET_GROUPS, workItemTicketOrder, type WorkItemTicketFacts } from "../lib/workItemPresentation";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import type { ManagerPrincipal, ManagerChildFilter } from "../lib/types";
  import Disclosure from "./Disclosure.svelte";
  import { navigationScrollWindow } from "../lib/navigation/views/scrollWindow";
  import NavigationWindowPages from "./NavigationWindowPages.svelte";
  import { viewWindow } from "../lib/navigation/views/owner";
  import { navigationWindow } from "../lib/navigation/presentation";
  import ResourceState from "./ResourceState.svelte";
  import WorkerRow from "./WorkerRow.svelte";

  let { principal, tickets, publication, navigationErrorHandledByParent = false, filter, onHover, onLeave }: {
    principal: ManagerPrincipal;
    tickets?: readonly (WorkItemTicketFacts & {is_backlogged:boolean})[];
    publication?: import("../lib/navigation/collection").NavigationPublication;
    navigationErrorHandledByParent?: boolean;
    filter: ManagerChildFilter;
    onHover?: (element: HTMLElement, ticketId: string, immediate?: boolean) => void;
    onLeave: () => void;
  } = $props();
  let offset = $state(0);
  let groupOpen = $state<Record<string, boolean>>({});
  let root = $state<HTMLElement | null>(null);
  const navigation = navigationApplication();
  let effective = $state.raw<import("../lib/navigation/knowledge").EffectiveNavigationView | undefined>(undefined);
  $effect(() => { const view = navigation?.selectors.effective; if (view) effective = view; });
  let shared = $derived(effective && navigation ? navigation.presentationFor(effective) : undefined);
  let parentKey = $derived(principal.kind === "chief" ? "chief:chief" : `manager:${principal.id}`);
  let sharedPage = $derived(shared ? chiefChildrenPage(shared.ticketsByParent.get(parentKey) ?? [], filter, offset) : undefined);
  let scopeOwner=$derived({kind:principal.kind==='chief'?'chief' as const:'manager' as const,id:principal.id});
  let scopedWindow=$derived(navigationWindow(shared,'children',scopeOwner,filter));
  let scopedGroups=$derived(principal.kind!=='chief' && filter==='active' && scopedWindow!==undefined);
  let groupPages=$state<Record<string,{anchor:string|null;direction:'before'|'after'}>>({});
  const activeDefinitions=WORK_ITEM_TICKET_GROUPS.filter(group=>group.key!=='awaiting_reply');
  let ordinaryGroups=$derived(activeDefinitions.flatMap(definition=>{
    const window=navigationWindow(shared,'children',scopeOwner,'active',definition.key);
    if(!window?.total)return [];
    return [{...definition,count:window.total,window,tickets:window.members.flatMap(key=>{const row=shared?.ticketsById.get(key.id);return row?[row]:[];})}];
  }));
  let rows = $derived(scopedGroups?ordinaryGroups.flatMap(group=>group.tickets):scopedWindow ? scopedWindow.members.flatMap(key=>{const row=shared?.ticketsById.get(key.id);return row?[row]:[];}) : tickets === undefined ? sharedPage?.tickets ?? [] : filterManagerChildren(tickets, filter));
  let page = $derived(scopedWindow ? undefined : tickets === undefined ? sharedPage?.page : undefined);
  let groups = $derived(scopedGroups?ordinaryGroups:filter === "active" ? managerPageGroups<WorkItemTicketFacts>(rows).map(group=>({...group,count:group.tickets.length,window:undefined})) : []);
  let sectionOpen = $state(false);
  let anchor=$state<string|null>(null);
  let direction=$state<'before'|'after'>('after');
  const demand=`children:${crypto.randomUUID()}`;
  $effect(()=>{
    const grouped=principal.kind!=='chief' && filter==='active';
    const windows=[viewWindow('children',scopeOwner,{child_filter:filter,limit:grouped?0:filter==='active'||sectionOpen?(principal.kind==='chief'?30:60):0,anchor,direction})];
    if(grouped)for(const definition of activeDefinitions){
      const known=navigationWindow(shared,'children',scopeOwner,'active',definition.key);
      windows.push(viewWindow('children',scopeOwner,{child_filter:'active',group:definition.key,limit:(groupOpen[definition.key]??true)&&known?.total?60:0,...groupPages[definition.key]}));
    }
    navigation?.owner.setDemand(demand,{windows});
  });
  onDestroy(()=>navigation?.owner.setDemand(demand,undefined));
  let observedGroups = $derived(filter === "active" ? groups : [{key:filter === "backlogged" ? "backlog" : "done",tickets:rows}]);
  let observedOpen = $derived(filter === "active" ? groupOpen : {[filter === "backlogged" ? "backlog" : "done"]:sectionOpen});
  const observer = navigation?.registerRenderBoundary(`manager-section:${crypto.randomUUID()}`, impact => {
    const view = navigation?.state.effective;
    return managerChildrenImpact(impact,principal,view && navigation ? navigation.presentationFor(view) : undefined,rows,observedGroups,observedOpen);
  });
  onDestroy(() => { if (observer) navigation?.disposeRenderBoundary(observer); });
  let count = $derived(scopedWindow?.total ?? page?.match_count ?? rows.length);
  let label = $derived(filter === "backlogged" ? "Backlogged" : "Done");
  $effect(() => {
    // A membership change can empty the last page. Return to the last remaining page.
    if (page && page.return_count === 0 && page.offset > 0) {
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
  $effect(() => {
    const view = effective, element = root, generation = navigation?.state.generation;
    const expectedRows = rows, expectedGroups = groups, expectedPage = page;
    const expectedFilter = filter, expectedPrincipal = principal;
    const expectedOpen = {...groupOpen}, expectedSectionOpen = sectionOpen;
    const expectedCount = count;
    if (!navigation || !observer || !view?.runtimeCurrent || !element || generation === undefined
      || navigation.selectors.effective !== view || (tickets !== undefined && publication !== view.publication)) return;
    const observation = observer.observeSelectors(view.publication,generation);
    let obsolete = false;
    void tick().then(() => {
      if (obsolete || !element.isConnected || navigation.state.generation !== generation || navigation.selectors.effective !== view) return;
      if (childrenSectionDomMatches(element,expectedPrincipal,expectedFilter,expectedRows,expectedGroups,expectedPage,expectedOpen,expectedSectionOpen,expectedCount,scopedWindow,principal.kind!=='chief'))
        observer.acknowledgeRender(view.publication,observation);
    });
    return () => { obsolete = true; };
  });
</script>

<div bind:this={root} data-worker-section={filter} use:navigationScrollWindow={{window:principal.kind==='chief'||scopedGroups||(filter!=='active'&&!sectionOpen)?undefined:scopedWindow,activate:()=>navigation?.owner.prioritizeWindow(scopedWindow?.request),selector:'[data-worker-ticket-id]',key:node=>node.dataset.workerTicketId ?? '',move:(next,nextDirection)=>{anchor=next;direction=nextDirection;}}}>
  {#snippet ticketRows(items: readonly WorkItemTicketFacts[])}
    <div class="manager-workspace-status-body">
      {#each items as ticket (ticket.id)}
        <div use:railPreview={onHover ? {show:(element, immediate) => onHover?.(element, ticket.id, immediate), leave:onLeave} : null}>
          <WorkerRow title={ticket.title} signal={ticket.conversation_signal} conversationId={ticket.conversation_id}
            quiet={ticket.stage === "done"}
            href={workspaceAddress(principal.kind === "manager"
              ? { kind: "ticket", id: ticket.id, openedFromManagerId: principal.id } : { kind: "ticket", id: ticket.id })}
            data-worker-ticket-id={ticket.id} />
        </div>
      {/each}
    </div>
  {/snippet}
  {#snippet contents()}
    <ResourceState shape="rows" error={tickets === undefined && !navigationErrorHandledByParent ? navigation?.state.error : null} loading={tickets === undefined && !shared}
      hasData={tickets !== undefined || shared !== undefined} loadingText={`Loading ${filter === "active" ? "Workers" : label}…`}>
      {#if filter === "active"}
        {#each groups as group (group.key)}
          <div use:navigationScrollWindow={{window:(groupOpen[group.key]??true)?group.window:undefined,activate:()=>navigation?.owner.prioritizeWindow(group.window?.request),selector:'[data-worker-ticket-id]',key:node=>node.dataset.workerTicketId ?? '',move:(next,nextDirection)=>{groupPages[group.key]={anchor:next,direction:nextDirection};}}}>
          <Disclosure variant="manager-group" class="manager-workspace-status" data-workspace-group={group.key}
            defaultOpen={groupOpen[group.key] ?? true} onReaderToggle={(open) => groupOpen[group.key] = open} chevron="none">
            {#snippet summary()}
              <span class="manager-workspace-status-label">{group.label}</span>
              <span class="manager-workspace-count" data-group-count>{group.count}</span>
            {/snippet}
            {#if groupOpen[group.key] ?? true}{@render ticketRows(group.tickets)}{/if}
          </Disclosure>
          </div>
        {/each}
      {:else}
        {@render ticketRows([...rows].sort(workItemTicketOrder))}
      {/if}
      {#if (scopedGroups?count:rows.length) === 0}<div class="manager-workspace-empty">No {filter === "active" ? "active Workers" : label === "Done" ? "completed Workers" : "backlogged Workers"}</div>{/if}
      {#if scopedWindow && principal.kind==='chief'}
        <NavigationWindowPages window={scopedWindow} label={filter==='active'?'Active Workers':label} onPage={(next,nextDirection)=>{anchor=next;direction=nextDirection;}} />
      {:else if page && (page.omitted_before > 0 || page.omitted_after > 0)}
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
    <Disclosure variant="manager-group" class="manager-workspace-status" data-workspace-group={filter === "backlogged" ? "backlog" : "done"}
      defaultOpen={false} onReaderToggle={(open) => sectionOpen = open} chevron="none">
      {#snippet summary()}
        <span class="manager-workspace-status-label">{label}</span>
        <span class="manager-workspace-count" data-group-count>{count}</span>
      {/snippet}
      {#if sectionOpen}{@render contents()}{/if}
    </Disclosure>
  {/if}
</div>

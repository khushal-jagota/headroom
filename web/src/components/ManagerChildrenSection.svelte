<script lang="ts">
  import { untrack, tick, onDestroy } from "svelte";
  import { navigationApplication } from "../lib/navigation/application.svelte";

  import { railPreview } from "../lib/workspaceRailInteraction";
  import { managerPageGroups } from "../lib/managerPage";
  import { WORK_ITEM_TICKET_GROUPS, workItemTicketOrder, type WorkItemTicketFacts } from "../lib/workItemPresentation";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import type { ManagerPrincipal, ManagerChildFilter } from "../lib/types";
  import type { ViewWindow } from "../lib/navigation/contracts";
  import Disclosure from "./Disclosure.svelte";
  import { navigationScrollWindow } from "../lib/navigation/views/scrollWindow";
  import NavigationWindowPages from "./NavigationWindowPages.svelte";
  import { viewWindow } from "../lib/navigation/views/owner";
  import { navigationWindow } from "../lib/navigation/presentation";
  import ResourceState from "./ResourceState.svelte";
  import WorkerRow from "./WorkerRow.svelte";

  let { principal, publication, filter, onHover, onLeave }: {
    principal: ManagerPrincipal;
    publication?: import("../lib/navigation/publication").NavigationPublication;
    filter: ManagerChildFilter;
    onHover?: (element: HTMLElement, ticketId: string, immediate?: boolean) => void;
    onLeave: () => void;
  } = $props();
  let groupOpen = $state<Record<string, boolean>>({});
  let root = $state<HTMLElement | null>(null);
  const navigation = navigationApplication();
  let effective = $state.raw<import("../lib/navigation/knowledge").EffectiveNavigationView | undefined>(undefined);
  $effect(() => { const view = navigation?.selectors.effective; if (view) effective = view; });
  let shared = $derived(effective && navigation ? navigation.presentationFor(effective) : undefined);
  let scopeOwner=$derived({kind:principal.kind==='chief'?'chief' as const:'manager' as const,id:principal.id});
  let scopedWindow=$derived(navigationWindow(shared,'children',scopeOwner,filter));
  let scopedGroups=$derived(principal.kind!=='chief' && filter==='active');
  let groupPages=$state<Record<string,{anchor:string|null;direction:'before'|'after'}>>({});
  const activeDefinitions=WORK_ITEM_TICKET_GROUPS.filter(group=>group.key!=='awaiting_reply');
  const windowRows=(window:ViewWindow)=>window.members.flatMap(key=>{const row=shared?.ticketsById.get(key.id);return row?[row]:[];});
  // A count arrives before its rows: a group asks for rows only once it knows it has some.
  // An open group with a count and no rows yet is unanswered.
  const answered=(window:ViewWindow,open:boolean)=>!open || window.total===0 || window.request.limit>0;
  let groupWindows=$derived(scopedGroups ? activeDefinitions.map(definition=>({definition,window:navigationWindow(shared,'children',scopeOwner,'active',definition.key)})) : []);
  let ordinaryGroups=$derived(groupWindows.flatMap(({definition,window})=>{
    if(!window?.total)return [];
    return [{...definition,count:window.total,window,tickets:windowRows(window)}];
  }));
  let rows = $derived(scopedGroups?ordinaryGroups.flatMap(group=>group.tickets):scopedWindow ? windowRows(scopedWindow) : []);
  let groups = $derived(scopedGroups?ordinaryGroups:filter === "active" ? managerPageGroups<WorkItemTicketFacts>(rows).map(group=>({...group,count:group.tickets.length,window:undefined})) : []);
  let sectionOpen = $state(false);
  // The section first draws when its own windows have all answered. It never draws from
  // rows that another surface happened to load.
  let settled = $derived(scopedWindow!==undefined && (scopedGroups
    ? groupWindows.every(({definition,window})=>window!==undefined && answered(window,groupOpen[definition.key]??true))
    : answered(scopedWindow,filter==='active' || sectionOpen)));
  // Grouped sections hold that first draw. Other sections ask for every row up front,
  // so they wait for their window whenever the reader opens them.
  let drawn = $state(false);
  $effect(() => { if (settled) drawn = true; });
  let ready = $derived(scopedGroups ? drawn : settled);
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

  let count = $derived(scopedWindow?.total);
  let label = $derived(filter === "backlogged" ? "Backlogged" : "Done");
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

<div bind:this={root} data-worker-section={filter} data-empty={filter !== "active" && count === 0 ? "true" : undefined} use:navigationScrollWindow={{window:principal.kind==='chief'||scopedGroups||(filter!=='active'&&!sectionOpen)?undefined:scopedWindow,activate:()=>navigation?.owner.prioritizeWindow(scopedWindow?.request),selector:'[data-worker-ticket-id]',key:node=>node.dataset.workerTicketId ?? '',move:(next,nextDirection)=>{anchor=next;direction=nextDirection;}}}>
  {#snippet ticketRows(items: readonly WorkItemTicketFacts[])}
    <div class="manager-workspace-status-body">
      {#each items as ticket (ticket.id)}
        <div use:railPreview={onHover ? {show:(element, immediate) => onHover?.(element, ticket.id, immediate), leave:onLeave} : null}>
          <WorkerRow locale={ticket.locale} title={ticket.title} signal={ticket.conversation_signal} conversationId={ticket.conversation_id}
            quiet={ticket.stage === "done"}
            href={workspaceAddress(principal.kind === "manager"
              ? { kind: "ticket", id: ticket.id, openedFromManagerId: principal.id } : { kind: "ticket", id: ticket.id })}
            data-worker-ticket-id={ticket.id} />
        </div>
      {/each}
    </div>
  {/snippet}
  {#snippet contents()}
    <ResourceState shape="rows" loading={!ready} hasData={ready} loadingText={`Loading ${filter === "active" ? "Workers" : label}…`}>
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
        <span class="manager-workspace-count" data-group-count>{count ?? ""}</span>
      {/snippet}
      {#if sectionOpen}{@render contents()}{/if}
    </Disclosure>
  {/if}
</div>

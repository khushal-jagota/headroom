<script lang="ts">
  import { viewWindow } from "../lib/navigation/views/owner";
  import { navigationScrollWindow } from "../lib/navigation/views/scrollWindow";
  import { createScrollWindowCoordinator } from "../lib/navigation/views/scrollWindowRestoration";
  import type { ViewWindowRequest } from "../lib/navigation/contracts";
  import { selectedRecap } from "../lib/navigation/views/recap.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import { untrack, tick, onDestroy } from "svelte";
  import { navigationApplication } from "../lib/navigation/application.svelte";
  import { navigationWindow, type NavigationPresentation } from "../lib/navigation/presentation";
  import { stabilizeWorkspaceRail } from "../lib/workspaceRail";
  import { workspaceWorkerStageSentence } from "../lib/workspaceRail";
  import { lifecycleFor } from "../lib/lifecycle";
  import type { RenderObserver } from "../lib/navigation/visibility";
  import type { NavigationPublication } from "../lib/navigation/collection";
  import { railDomMatches, hoverDomMatches } from "../lib/workspaceRailDom";
  import { workspaceRailImpact, workspaceHoverImpact, managerSettleImpact } from "../lib/workspaceNavigationImpact";
  import { createRailMotionController, type WorkspaceRailPresentation } from "../lib/workspaceRailMotion";
  import { onMount } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../lib/queryCatalogue";
  import { mutateJson } from "../lib/mutate";
  import { buildWorkspaceRail, workspaceListRecap, type WorkspaceRailItem } from "../lib/workspaceRail";
  import { RAIL_MIN_WIDTH, RAIL_MAX_WIDTH, RAIL_DEFAULT_WIDTH, RAIL_SETTLED_MAX_HEIGHT_SHARE, clampRailWidth, readRailWidth, saveRailWidth, railWidthForKey, createRailHoverController } from "../lib/workspaceRailInteraction";
  import { correctedWorkspaceAddress, whatTheAddressOpens, workspaceAddress, type WorkspaceAddress } from "../lib/workspaceAddress";
  import { parseUtilityPath, type UtilityHref, type WorkspaceUtility } from "../lib/utilityAddress";
  import type { ManagedFileTarget } from "../lib/filePreview";
  import type { WorkItemTicketFacts } from "../lib/workItemPresentation";
  import ConversationSignalMark from "../components/ConversationSignalMark.svelte";
  import ChiefConversation from "../components/ChiefConversation.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import LazyRegion from "../components/LazyRegion.svelte";
  import { cachedImport } from "../lib/lazyViews";
  import WorkspaceRailCard from "../components/WorkspaceRailCard.svelte";
  import WorkspaceRailHover from "../components/WorkspaceRailHover.svelte";

  import type { Snippet } from "svelte";
  let { address, externalPane, railFoot, onDismissed = () => undefined }: { address: WorkspaceAddress; externalPane?: Snippet; railFoot?: Snippet; onDismissed?: (noteId: string) => void } = $props();
  const navigation = (() => {
    const context = navigationApplication();
    if (!context) throw new Error("Workspace requires the application navigation owner");
    return context;
  })();
  const manifests = createQuery(() => queries.workerTypeManifests());
  // Chief recap is dedicated content outside navigation records, so the popover
  // reads it from the same settings query the Chief conversation pane already uses.
  const chiefSettings = createQuery(() => queries.chiefSettings());
  let workspace = $state.raw<NavigationPresentation | null>(null);
  let displayedPublication = $state.raw<NavigationPublication | null>(null);
  $effect(() => {
    const effective = navigation.selectors.effective;
    if (effective) {
      workspace = navigation.presentationFor(effective);
      displayedPublication = effective.publication;
    }
  });
  const railObserver = navigation.registerRenderBoundary(`workspace-rail:${crypto.randomUUID()}`,impact => {
    const view=navigation.state.effective;return workspaceRailImpact(impact,view ? navigation.presentationFor(view) : undefined,rail,settledOpen);
  });
  onDestroy(() => navigation.disposeRenderBoundary(railObserver));
  let opening = $derived(whatTheAddressOpens(address));
  const selectedPaneModule = $derived.by(() => {
    if (opening.markedTicketId) return cachedImport("./TicketRoute.svelte", () => import("./TicketRoute.svelte"));
    if (opening.openManagerId) return cachedImport("../components/ManagerWorkspace.svelte", () => import("../components/ManagerWorkspace.svelte"));
    switch (opening.markedUtility) {
      case "usage": return cachedImport("./BackendsRoute.svelte", () => import("./BackendsRoute.svelte"));
      case "settings": return cachedImport("./SettingsRoute.svelte", () => import("./SettingsRoute.svelte"));
      case "ideas": return cachedImport("./IdeasRoute.svelte", () => import("./IdeasRoute.svelte"));
      case "feedback": return cachedImport("./FeedbackRoute.svelte", () => import("./FeedbackRoute.svelte"));
      default: return null;
    }
  });
  let paneShape = $derived(opening.markedUtility === "usage" || opening.markedUtility === "ideas" || opening.markedUtility === "feedback" ? "rows" as const : "document" as const);
  // The screen's own position, read the same way its standalone page reads it. The
  // address is parsed before it reaches here, so a placement is always present.
  let placement = $derived(
    opening.markedUtility === null
      ? null
      : parseUtilityPath(opening.markedUtility, opening.markedUtilityPath)
  );
  // An embedded screen links through this instead of writing its page address. The
  // whole Workspace address is rebuilt each time, so the rail's current list survives
  // every click inside the screen.
  function utilityHref(utility: WorkspaceUtility): UtilityHref {
    return segments => workspaceAddress({ kind: "utility", utility, path: segments }, opening.view);
  }
  // A link of the shape #/workspace/<id> is read as a Ticket, and a manager id of
  // that shape used to open the Ticket page saying "ticket not found". The publication
  // knows which ids are managers, so the address is corrected here, where that is
  // known. replace() rather than assignment: the broken address was never a place the
  // reader chose to be, so it does not earn an entry in their history.
  let managerIds = $derived(
    new Set([
      ...(workspace?.managers ?? []).map((item) => item.id),
      ...(workspace?.settledManagers ?? []).map((item) => item.id)
    ])
  );
  $effect(() => {
    const corrected = correctedWorkspaceAddress(address, (id) => managerIds.has(id));
    if (corrected !== null) window.location.replace(corrected);
  });
  const selectedItemWorkspace = createQuery(() => ({ ...queries.managerWorkspaceContent(opening.openManagerId ?? ""), enabled: opening.openManagerId !== null }));
  let allCards = $derived(workspace?.tickets ?? []);
  let visibleItems=$state<string[]>([]);
  let railWindows=$state<Record<string,{anchor:string;direction:'before'|'after'}>>({});
  function railPage(key:string,anchor:string,direction:'before'|'after'){railWindows={...railWindows,[key]:{anchor,direction}};}
  function itemVisibility(id:string,visible:boolean){if(visible===visibleItems.includes(id))return;visibleItems=visible?[...visibleItems,id]:visibleItems.filter(value=>value!==id);}
  let parentWindow=$derived(navigationWindow(workspace,'rail_parents'));
  let settledWindow=$derived(navigationWindow(workspace,'settled'));
  let desiredRail = $derived.by(()=>{
    if(!workspace?.globalCounts)return buildWorkspaceRail(allCards,[...(workspace?.managers ?? []),...(workspace?.settledManagers ?? [])]);
    const parents=new Set([...(parentWindow?.members ?? []),...(settledWindow?.members ?? [])].map(key=>key.id));
    const childIds=new Set(workspace.viewWindows.filter(window=>window.request.section==='rail_tickets').flatMap(window=>window.members.map(key=>key.id)));
    const cards=allCards.filter(card=>childIds.has(card.id));
    const projected=buildWorkspaceRail(cards,[...workspace.managers,...workspace.settledManagers].filter(item=>parents.has(item.id)).map(item=>({...item,tickets:item.tickets.filter(ticket=>childIds.has(ticket.id))})));
    // The scoped window owns order, including the server identity tie-break.
    const settledById=new Map(projected.settledItems.map(item=>[item.id,item]));
    return {...projected,settledItems:(settledWindow?.members ?? []).flatMap(key=>{
      const item=settledById.get(key.id);return item ? [item] : [];
    })};
  });
  let presentation = $state<WorkspaceRailPresentation>({rail:buildWorkspaceRail([]), settledOpen:false});
  const scrollCoordinator = createScrollWindowCoordinator();
  function renderedRailWindow(section:ViewWindowRequest['section'],owner:ViewWindowRequest['owner']=null) {
    return presentation.scrollPublication?.windows.find(window=>window.request.section===section
      && window.request.owner?.kind===owner?.kind && window.request.owner?.id===owner?.id
      && window.request.child_filter==='all' && window.request.group==='all');
  }
  let rail = $derived(presentation.rail);
  let settledOpen = $derived(presentation.settledOpen);
  let requestedSettledOpen = $state(false);
  $effect(()=>{
    const windows:ViewWindowRequest[]=[viewWindow('rail_parents',null,{limit:60,...railWindows.parents}),viewWindow('rail_tickets',{kind:'chief',id:'chief'},{...railWindows.chief})];
    for(const id of visibleItems)windows.push(viewWindow('rail_tickets',{kind:'manager',id},{limit:60,...railWindows[id]}));
    if(requestedSettledOpen)windows.push(viewWindow('settled',null,{limit:60,...railWindows.settled}));
    navigation.owner.setDemand('workspace-rail',{windows});
  });
  onDestroy(()=>navigation.owner.setDemand('workspace-rail',undefined));
  let itemPane = $derived(opening.markedManagerId !== null);
  let railElement = $state<HTMLElement | null>(null);
  const motion = createRailMotionController(() => railElement, () => presentation, (next) => presentation = next,
    () => scrollCoordinator.flush(), () => scrollCoordinator.captureBeforePublication());
  $effect(() => {
    const next = {rail:desiredRail, settledOpen:requestedSettledOpen,
      scrollPublication:{publicationId:displayedPublication?.publicationId ?? null,windows:workspace?.viewWindows ?? []}};
    untrack(() => motion.update(next));
  });
  function freezeRows(event: PointerEvent, leaving: boolean): void {
    if (event.pointerType === "touch") return;
    const target = (leaving ? event.relatedTarget : event.target) as HTMLElement | null;
    motion.freeze(Boolean(target?.closest?.(".worker-row")));
  }
  let shellElement = $state<HTMLDivElement | null>(null);
  let railWidth = $state<number | null>(null);
  let measuredRailWidth = $state(272);
  let resizing = $state(false);
  let busyItem = $state<string | null>(null);
  let settleError = $state("");
  let hoverVisible = $state(false);
  let hoverElement = $state<HTMLDivElement | null>(null);
  let hoverObserver = $state.raw<RenderObserver | null>(null);
  let hoverInstant = $state(false);
  let hoverTarget = $state<HTMLElement | null>(null);
  let hoverTitle = $state("");
  let hoverKicker = $state("");
  let hoverWorkerId = $state<string | null>(null);
  let hoverItemId = $state<string | null>(null);
  let hoverChief = $state(false);
  let rawHoverWorker = $derived.by(() => {
    const ticket = allCards.find((card) => card.id === hoverWorkerId)
      ?? [...desiredRail.items,...desiredRail.settledItems].flatMap(item => item.tickets).find(ticket => ticket.id === hoverWorkerId);
    if (!ticket) return null;
    return ticket;
  });
  const hoverBody=selectedRecap(()=>rawHoverWorker?.recap_reference ?? hoveredItem?.recap_reference,()=>hoverVisible);
  let hoverWorker=$derived(rawHoverWorker && hoverBody.body!==undefined?{...rawHoverWorker,recap:hoverBody.body}:rawHoverWorker);
  let hoveredItem = $derived.by(()=>{
    // Only a full consumer can supply a manager popover, including unread owners.
    const full=hoverItemId ? workspace?.managersById.get(hoverItemId) : undefined;
    if(!full)return undefined;
    const projected=buildWorkspaceRail([], [full]);
    return [...projected.items,...projected.settledItems][0];
  });
  let hoverRecap = $derived(hoverChief ? workspaceListRecap(chiefSettings.data?.recap)
    : workspaceListRecap(hoverBody.body ?? hoveredItem?.recap));
  let hoverSignal = $derived(hoverChief ? workspace?.chief?.conversation_signal : hoveredItem?.conversationSignal);
  let hoverConversationId = $derived(hoverChief ? workspace?.chief?.conversation_id : hoveredItem?.conversationId);
  // Every agent the panel can show keeps a recap, so every one of them can be asked.
  let hoverRecapTarget = $derived<{ kind: "ticket" | "manager" | "chief"; id: string } | null>(
    hoverWorker ? { kind: "ticket", id: hoverWorker.id }
      : hoverChief ? { kind: "chief", id: "chief" }
      : hoveredItem ? { kind: "manager", id: hoveredItem.id } : null);
  let currentHoverTitle = $derived(hoverWorker?.title ?? (hoverChief ? workspace?.chief?.label : hoveredItem?.title) ?? hoverTitle);
  let currentHoverKicker = $derived.by(() => {
    if (!hoverWorker) return hoverKicker;
    const parent = [...desiredRail.items, ...desiredRail.settledItems]
      .find((item) => item.tickets.some((ticket) => ticket.id === hoverWorker.id));
    if (parent) return parent.title;
    const card = allCards.find((ticket) => ticket.id === hoverWorker.id);
    return card && (card.manager?.kind === "chief" || (!card.manager && !card.manager_id))
      ? workspace?.chief?.label ?? "Chief of Staff" : "Worker";
  });
  const hover = createRailHoverController<{ target: HTMLElement; title: string; kicker: string; worker?: WorkItemTicketFacts; itemId?: string; chief?: boolean }>((value, instant) => {
    if (!value.target.isConnected) return;
    hoverTarget = value.target; hoverTitle = value.title; hoverKicker = value.kicker;
    hoverWorkerId = value.worker?.id ?? null; hoverItemId = value.itemId ?? null; hoverChief = value.chief ?? false;
    hoverInstant = instant; hoverVisible = true;
    if (instant) requestAnimationFrame(() => requestAnimationFrame(() => hoverInstant = false));
  }, () => { hoverVisible = false; });

  onMount(() => {
    railWidth = readRailWidth(preferenceStorage());
    return () => { hover.dispose(); motion.dispose(); scrollCoordinator.dispose(); };
  });
  $effect(() => {
    if (!railElement) return;
    const observedRail = railElement;
    const measure = () => measuredRailWidth = observedRail.getBoundingClientRect().width;
    const observer = new ResizeObserver(measure);
    measure();
    observer.observe(observedRail);
    return () => observer.disconnect();
  });
  function preferenceStorage(): Storage | null {
    try { return window.localStorage; } catch { return null; }
  }
  function setWidth(value: number | null): void {
    railWidth = value === null ? null : clampRailWidth(value);
    saveRailWidth(preferenceStorage(), railWidth);
  }
  function resizePointer(event: PointerEvent): void {
    event.preventDefault();
    const grip = event.currentTarget as HTMLElement;
    grip.setPointerCapture(event.pointerId); resizing = true;
  }
  function resizeMove(event: PointerEvent): void {
    if (resizing && shellElement) setWidth(event.clientX - shellElement.getBoundingClientRect().left);
  }
  function resizeKey(event: KeyboardEvent): void {
    const currentWidth = railElement?.getBoundingClientRect().width ?? measuredRailWidth;
    const next = railWidthForKey(currentWidth, event.key, event.shiftKey);
    if (next !== null) { event.preventDefault(); setWidth(next); }
    if (event.key === "Home") { event.preventDefault(); setWidth(null); }
  }
  function openFileOnTicket(file: ManagedFileTarget | null): void {
    window.location.hash = workspaceAddress(address.selection, address.view, file, address.selectedConversationId);
  }
  function selectCard(id: string, insideItemId: string | null): void {
    window.location.hash = workspaceAddress(insideItemId ? { kind: "ticket", id, openedFromManagerId: insideItemId } : { kind: "ticket", id }, opening.view);
  }
  function selectItem(id: string): void { window.location.hash = workspaceAddress({ kind: "manager", id }); }
  function hoverItem(target: HTMLElement, item: WorkspaceRailItem, immediate = false): void {
    if(item.settled)return;
    hover.enter({ target, title:item.title, kicker:item.project ?? "Manager", itemId:item.id }, immediate);
  }
  function hoverWorkerRow(target: HTMLElement, worker: WorkItemTicketFacts, immediate = false): void {
    hover.enter({ target, title:worker.title, kicker:"Worker", worker }, immediate);
  }
  $effect(() => {
    const publication = displayedPublication;
    const effective = navigation.selectors.effective;
    const generation = navigation.state.generation;
    const root = railElement;
    const target = stabilizeWorkspaceRail(desiredRail, rail);
    const chief = workspace?.chief;
    const open = settledOpen;
    // The observer never credits retained knowledge or a deferred motion structure.
    if (!publication || !root || !effective?.runtimeCurrent || effective.publication !== publication) return;
    const observation = railObserver.observeSelectors(publication, generation);
    let obsolete = false;
    void tick().then(() => {
      if (obsolete || !root.isConnected || displayedPublication !== publication
        || navigation.state.generation !== generation || navigation.selectors.effective !== effective) return;
      if (railDomMatches(root,target,chief,open,workspace?.globalCounts?.settled)) railObserver.acknowledgeRender(publication,observation);
    });
    return () => { obsolete = true; };
  });
  $effect(() => {
    if (!hoverVisible || !hoverElement) return;
    const observer = navigation.registerRenderBoundary(`workspace-hover:${crypto.randomUUID()}`,impact => {
      const kind=hoverWorkerId ? 'ticket' : hoverItemId ? 'manager' : 'chief';
      const id=hoverWorkerId ?? hoverItemId ?? 'chief';
      const view=navigation.state.effective;
      return workspaceHoverImpact(impact,kind,id,hoverWorker?.conversation_id ?? hoverConversationId,view ? navigation.presentationFor(view) : undefined,
        kind==='chief' || kind==='manager' && !hoveredItem?.recap?.trim());
    });
    hoverObserver = observer;
    return () => { navigation.disposeRenderBoundary(observer); hoverObserver = null; };
  });
  $effect(() => {
    const publication = displayedPublication;
    const effective = navigation.selectors.effective;
    const generation = navigation.state.generation;
    const root = hoverElement;
    const observer = hoverObserver;
    const target = hoverTarget;
    const sourceKind = hoverWorkerId ? 'ticket' : hoverItemId ? 'manager' : hoverChief ? 'chief' : null;
    const sourceId = hoverWorkerId ?? hoverItemId;
    const sourceExists = sourceKind !== null && publication?.records.some(record => record.kind === sourceKind && (sourceKind === 'chief' || record.id === sourceId));
    const worker = hoverWorker;
    const title = currentHoverTitle;
    const kicker = currentHoverKicker;
    const recap = worker ? worker.recap ?? '' : hoverRecap;
    const stage = worker ? workspaceWorkerStageSentence(worker,lifecycleFor(manifests.data,worker.worker_type)) : '';
    const signal = worker?.conversation_signal ?? hoverSignal;
    const conversation = worker?.conversation_id ?? hoverConversationId;
    if (!hoverVisible || !root || !target?.isConnected || !sourceExists || !observer || !publication || !effective?.runtimeCurrent || effective.publication !== publication) return;
    const observation = observer.observeSelectors(publication,generation);
    let obsolete = false;
    void tick().then(() => {
      if (obsolete || !root.isConnected || !target.isConnected || hoverTarget !== target || !hoverVisible || displayedPublication !== publication
        || navigation.state.generation !== generation || navigation.selectors.effective !== effective) return;
      if (hoverDomMatches(root,title,recap,stage,signal,conversation,kicker)) observer.acknowledgeRender(publication,observation);
    });
    return () => { obsolete = true; };
  });
  async function settle(item: WorkspaceRailItem, settled: boolean): Promise<void> {
    busyItem = item.id; settleError = ""; hoverVisible = false; hover.leave();
    try {
      await navigation.runCommand(managerSettleImpact(item.id),responseMetadata =>
        mutateJson(`/api/managers/${encodeURIComponent(item.id)}/settled`, {method:"PATCH",body:{settled},responseMetadata}),"set manager settled");
    }
    catch (error) { settleError = error instanceof Error ? error.message : "The manager could not be updated."; }
    finally { busyItem = null; }
  }
</script>

<section class="board-screen" data-screen="workspace">
    <div class="board-workspace-wrap">
      <div bind:this={shellElement} class="board-workspace-shell approved-rail-shell" class:resizing class:board-workspace-shell--destination={Boolean(externalPane) || opening.markedUtility !== null} class:board-workspace-shell--chief={opening.chiefMarked} class:board-workspace-shell--item={itemPane} class:board-workspace-shell--ticket={Boolean(opening.markedTicketId)} style:--rail-width={railWidth === null ? RAIL_DEFAULT_WIDTH : `${railWidth}px`}>
        <section bind:this={railElement} class="board-workspace-left approved-rail workspace-rail" aria-label="Workspace" style:--settled-max-height={`${RAIL_SETTLED_MAX_HEIGHT_SHARE * 100}%`}>
          <div class="rail-work">

          <ResourceState shape="block" layout="fill" error={navigation.state.error} loading={!workspace} hasData={Boolean(workspace)} loadingText="Loading workspace...">
          <div class="rail-work">
            <div role="presentation" class="active-work-scroll" onpointerover={(event) => freezeRows(event,false)} onpointerout={(event) => freezeRows(event,true)} data-active-work-scroll use:navigationScrollWindow={{window:renderedRailWindow('rail_parents'),pageRestoration:{coordinator:scrollCoordinator,priority:'parent',publicationId:presentation.scrollPublication?.publicationId ?? null,members:rail.items.map(item=>item.id)},activate:()=>navigation?.owner.prioritizeWindow(parentWindow?.request),selector:'.manager-card:not(.chief)',key:node=>node.dataset.manager ?? '',move:(anchor,direction)=>railPage('parents',anchor,direction)}} onscroll={() => hover.leave()}>
            <WorkspaceRailCard {scrollCoordinator} scrollPublicationId={presentation.scrollPublication?.publicationId ?? null} navigationWindow={renderedRailWindow('rail_tickets',{kind:'chief',id:'chief'})} onWindow={(anchor,direction)=>railPage('chief',anchor,direction)} id="chief" title={workspace?.chief?.label ?? "Chief of Staff"} chief signal={workspace?.chief?.conversation_signal} conversationId={workspace?.chief?.conversation_id} selected={opening.chiefMarked} selectedTicketId={opening.markedTicketId} groups={rail.chiefGroups} onSelect={() => window.location.hash = workspaceAddress({kind:"chief"}, opening.view)} onTicket={(id) => selectCard(id,null)} onSettle={() => undefined} onHover={(target, immediate) => hover.enter({target,title:workspace?.chief?.label ?? "Chief of Staff",kicker:"Chief of Staff",chief:true}, immediate)} onWorkerHover={hoverWorkerRow} onLeave={() => hover.leave()} />
            {#each rail.items as item (item.id)}
              <WorkspaceRailCard {scrollCoordinator} scrollPublicationId={presentation.scrollPublication?.publicationId ?? null} onVisibility={itemVisibility} navigationWindow={renderedRailWindow('rail_tickets',{kind:'manager',id:item.id})} onWindow={(anchor,direction)=>railPage(item.id,anchor,direction)} id={item.id} title={item.title} project={item.project} signal={item.conversationSignal} conversationId={item.conversationId} selected={opening.markedManagerId === item.id} selectedTicketId={opening.markedTicketId} groups={item.groups} busy={busyItem === item.id} onSelect={() => selectItem(item.id)} onTicket={(id) => selectCard(id,item.id)} onSettle={() => void settle(item,true)} onHover={(target, immediate) => hoverItem(target,item,immediate)} onWorkerHover={hoverWorkerRow} onLeave={() => hover.leave()} />
            {/each}
            {#if settleError}<p role="alert">{settleError}</p>{/if}
            </div>
            <div class="settled" data-rail-motion="settled-section" class:settled--open={settledOpen}>
              <button type="button" class="settled-head" aria-expanded={settledOpen} onclick={() => { requestedSettledOpen = !requestedSettledOpen; motion.setSettledOpen(requestedSettledOpen); }}><span>Settled <span class="settled-count"><span class="count" data-settled-count>({workspace?.globalCounts?.settled ?? rail.settledItems.length})</span></span></span><span class="rule"></span><svg class="settled-chevron" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m5 10 3-3 3 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg></button>
              {#if presentation.settledBodyOpen ?? settledOpen}
                <div class="settled-body" data-settled-scroll use:navigationScrollWindow={{window:settledWindow,activate:()=>navigation?.owner.prioritizeWindow(settledWindow?.request),selector:'[data-settled-item]',key:node=>node.dataset.settledItem ?? '',move:(anchor,direction)=>railPage('settled',anchor,direction)}} onscroll={() => hover.leave()}>
                {#each rail.settledItems as item (item.id)}
                  <div class="settled-item" data-settled-item={item.id} data-rail-motion={`settled:${item.id}`}>
                  <div class="settled-row">
                    <button class="settled-title" type="button" aria-label={`Open ${item.title}`} onclick={() => selectItem(item.id)}><span>{item.title}</span><ConversationSignalMark signal={item.conversationSignal} conversationId={item.conversationId} /></button>
                    <button class="reopen" type="button" disabled={busyItem === item.id} aria-label={`Reopen ${item.title}`} onclick={() => void settle(item,false)}>Reopen</button>
                  </div>
                  </div>
                {/each}
                </div>
              {/if}
            </div>
          </div>
          </ResourceState>
          </div>
          {#if railFoot}{@render railFoot()}{/if}
        </section>
        <!-- A focusable separator implements the WAI-ARIA window splitter keyboard pattern. -->
        <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
        <div tabindex="0" class="rail-grip" role="separator" aria-label="Resize workspace panel" aria-orientation="vertical" aria-valuemin={RAIL_MIN_WIDTH} aria-valuemax={RAIL_MAX_WIDTH} aria-valuenow={Math.round(measuredRailWidth)} onpointerdown={resizePointer} onpointermove={resizeMove} onpointerup={() => resizing = false} onpointercancel={() => resizing = false} onlostpointercapture={() => resizing = false} onkeydown={resizeKey} ondblclick={() => setWidth(null)} style:left={`${measuredRailWidth}px`}></div>
        <section
          class:board-workspace-right--destination={Boolean(externalPane)}
          class:board-workspace-right--ticket={Boolean(opening.markedTicketId)}
          class:board-workspace-right--chief={opening.chiefMarked}
          class:board-workspace-right--item={itemPane}
          class:board-workspace-right--utility={opening.markedUtility !== null}
          class="board-workspace-right"
          aria-label={opening.chiefMarked ? "Chief of Staff conversation" : itemPane ? "Manager workspace" : "Workspace inspector"}
        >
          {#if externalPane}
            {@render externalPane()}
          {:else if opening.chiefMarked}
            <ChiefConversation showRequestErrorLine={false} requestedConversationId={address.selectedConversationId ?? null} openedFile={address.openFile} onOpenFile={openFileOnTicket} />
          {:else if opening.markedUtility !== null && selectedPaneModule && placement}
          <!-- The pane owns the scroll for a utility screen. These screens are written as
               pages that let the document scroll, so without this they have no scroller of
               their own, and borrowing one from the loading region would make the pane
               depend on how a skeleton happens to be styled. -->
          <div class="board-workspace-utility" data-utility-scroll>
          <LazyRegion promise={selectedPaneModule} shape={paneShape}>
          {#if placement.utility === "usage"}
            {#await import("./BackendsRoute.svelte") then view}
<view.default {...placement.usage} href={utilityHref("usage")} />
{:catch error}
<ErrorLine {error} />
{/await}
          {:else if placement.utility === "settings"}
            {#await import("./SettingsRoute.svelte") then view}
<view.default {...placement.settings} href={utilityHref("settings")} />
{:catch error}
<ErrorLine {error} />
{/await}
          {:else if placement.utility === "ideas"}
            {#await import("./IdeasRoute.svelte") then view}
<view.default />
{:catch error}
<ErrorLine {error} />
{/await}
          {:else if placement.utility === "feedback"}
            {#await import("./FeedbackRoute.svelte") then view}
<view.default {onDismissed} />
{:catch error}
<ErrorLine {error} />
{/await}
          {/if}
          </LazyRegion>
          </div>
          {:else if selectedPaneModule}
          <LazyRegion promise={selectedPaneModule} shape={paneShape} layout="fill">
          {#if opening.markedTicketId}
            {#key opening.markedTicketId}
              {#await import("./TicketRoute.svelte") then view}
<view.default
                navigationErrorHandledByParent
                id={opening.markedTicketId}
                requestedConversationId={address.selectedConversationId ?? null}
                openFile={address.openFile}
                onOpenFile={openFileOnTicket}
              />
{:catch error}
<ErrorLine {error} />
{/await}
            {/key}
          {:else if itemPane && opening.openManagerId}
            {#key opening.openManagerId}
              {#await import("../components/ManagerWorkspace.svelte") then view}
<view.default
                navigationErrorHandledByParent
                itemId={opening.openManagerId}
                requestedConversationId={address.selectedConversationId ?? null}
                workspaceName="Workspace"
                backHref="#/workspace"
                workspaceResource={{
                  data: selectedItemWorkspace.data,
                  error: selectedItemWorkspace.error,
                  isLoading: selectedItemWorkspace.isLoading
                }}
              />
{:catch error}
<ErrorLine {error} />
{/await}
            {/key}
          {/if}
          </LazyRegion>
          {:else}
            <div class="board-workspace-empty-inspector">
              <span>Select a manager or Worker to inspect it.</span>
            </div>
          {/if}
        </section>
      </div>
    </div>
</section>

<WorkspaceRailHover bind:element={hoverElement} visible={hoverVisible} instant={hoverInstant} target={hoverTarget} title={currentHoverTitle} kicker={currentHoverKicker} signal={hoverSignal} conversationId={hoverConversationId} worker={hoverWorker} recap={hoverRecap} rail={railElement} recapTarget={hoverRecapTarget} onKeepOpen={() => hover.keepOpen()} onLeave={() => hover.leave()} />
<style>
  .board-workspace-wrap { display:flex; flex-direction:column; }
  .board-workspace-shell { flex:1; height:auto; }
  /* The pane's own scroller for a utility screen. It is here, scoped to this component,
     rather than in the shared stylesheet, so no skeleton rule can take it away. */
  .board-workspace-utility { flex:1; min-height:0; overflow:auto; display:flex; flex-direction:column; }
  /* Whatever stands in the pane takes the pane's height, so a screen that is still
     arriving reserves the whole pane rather than stopping part of the way down. The
     selector names no class: the only element this reaches is the block the screen is
     mounted in, beside a text-only status node. */
  .board-workspace-utility > :global(div) { flex:1 0 auto; min-height:0; }
  .approved-rail-shell { position:relative; isolation:isolate; grid-template-columns:var(--rail-width) minmax(0,1fr); }
  .approved-rail { display:flex; flex-direction:column; height:100%; min-height:0; overflow:hidden; font-size:var(--type-sm); line-height:normal; padding:var(--space-3); }
  .rail-work { display:flex; flex-direction:column; flex:1; min-height:0; }
  .active-work-scroll { flex:1; min-height:0; overflow-x:hidden; overflow-y:auto; scrollbar-gutter:stable; scrollbar-width:thin; margin-right:calc(0px - var(--space-3)); padding-right:var(--space-3); }
  .settled-body { min-height:0; overflow-x:hidden; overflow-y:auto; scrollbar-gutter:stable; scrollbar-width:thin; margin-right:calc(0px - var(--space-3)); padding-right:var(--space-3); }
  .rail-grip { position:absolute; top:0; bottom:0; width:9px; z-index:20; transform:translateX(-4px); cursor:col-resize; background:transparent; border:0; padding:0; touch-action:none; }
  .rail-grip::after { content:""; position:absolute; inset:0 4px; background:transparent; transition:background var(--motion-fast) var(--motion-ease); }
  .rail-grip:hover::after,.rail-grip:focus-visible::after,.resizing .rail-grip::after { background:var(--accent-bright); }
  .resizing { user-select:none; cursor:col-resize; }
  .settled { display:flex; flex-direction:column; flex:0 1 auto; min-height:0; max-height:var(--settled-max-height); }
  .settled-head { flex:none; display:flex; align-items:center; gap:var(--space-3); padding:var(--space-2) 0; border:0; background:none; width:100%; font:inherit; color:var(--text-faint); cursor:pointer; }
  .settled-head:hover { color:var(--text-strong); }
  .settled-count { position:relative; display:inline-block; }
  .settled-chevron { width:16px; height:16px; flex:none; }
  .settled--open .settled-chevron { transform:rotate(180deg); }
  .rule { flex:1; height:var(--border-hairline); background:var(--border-color); }
  .count { flex:none; font-size:var(--type-xs); color:var(--text-faintest); font-variant-numeric:tabular-nums; }
  .settled-item + .settled-item { margin-top:var(--space-3); }
  .settled-row { display:flex; align-items:center; gap:var(--space-2); padding:var(--space-1) var(--space-2); margin:0 calc(var(--space-2) * -1); border-radius:var(--radius-sm); }
  .settled-row:hover { background-image:var(--interaction-hover); }
  .settled-title,.reopen { border:0; background:none; font:inherit; cursor:pointer; color:var(--text-faintest); padding:0; }
  .settled-title { flex:1; display:flex; align-items:center; gap:var(--space-2); text-align:left; min-width:0; }
  .settled-title > span { flex:1; min-width:0; overflow-wrap:anywhere; }
  .reopen { opacity:0; margin-left:auto; color:var(--text-faint); font-size:12px; }
  .settled-row:hover .reopen,.settled-row:focus-within .reopen { opacity:1; }
  button:focus-visible { outline:var(--border-strong) solid var(--accent-bright); outline-offset:2px; }
  @media(max-width:720px) {
    .approved-rail-shell { grid-template-columns:1fr; }
    .rail-grip { display:none; }
    .board-workspace-shell--chief .approved-rail,
    .board-workspace-shell--item .approved-rail,
    .board-workspace-shell--ticket .approved-rail,
    .board-workspace-shell--destination .approved-rail { display:none; }
    .board-workspace-right--destination { display:flex; padding:0; }
  }
</style>

<script lang="ts">
  import { workspaceRailWash } from "../lib/workspaceRailWash";
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
  import {goalsForHolder,goalRailWindowLimit} from "../lib/goals/holders";
  import {refreshAtBoundary} from "../lib/goals/presentation";
  import {goalQueries} from "../lib/goals/queries";
  import type { NavigationPublication } from "../lib/navigation/publication";
  import { createRailMotionController, type WorkspaceRailPresentation } from "../lib/workspaceRailMotion";
  import { onMount } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../lib/queryCatalogue";
  import { mutateJson } from "../lib/mutate";
  import { buildWorkspaceRail, workspaceListRecap, type WorkspaceRailItem } from "../lib/workspaceRail";
  import { RAIL_MIN_WIDTH, RAIL_MAX_WIDTH, RAIL_DEFAULT_WIDTH, RAIL_SETTLED_MAX_HEIGHT_SHARE, clampRailWidth, readRailWidth, saveRailWidth, railWidthForKey, createRailHoverController, listenForUserScroll } from "../lib/workspaceRailInteraction";
  import { correctedWorkspaceAddress, whatTheAddressOpens, workspaceAddress, type WorkspaceAddress } from "../lib/workspaceAddress";
  import { parseUtilityPath, type UtilityHref, type WorkspaceUtility } from "../lib/utilityAddress";
  import type { ManagedFileTarget } from "../lib/filePreview";
  import type { WorkItemTicketFacts } from "../lib/workItemPresentation";
  import ConversationSignalMark from "../components/ConversationSignalMark.svelte";
  import GoalRail from "../components/goals/GoalRail.svelte";
  import { blurGoalPreviewInput, createGoalRailPreviewInput } from "../lib/goals/railPreview";
  import GoalWorkspace from "../components/goals/GoalWorkspace.svelte";
  import ChiefConversation from "../components/ChiefConversation.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import LazyRegion from "../components/LazyRegion.svelte";
  import { cachedImport } from "../lib/lazyViews";
  import WorkspaceRailCard from "../components/WorkspaceRailCard.svelte";
  import WorkspaceRailHover from "../components/WorkspaceRailHover.svelte";

  import type { Snippet } from "svelte";
  let { address, externalPane, railFoot, railStatus }: { address: WorkspaceAddress; externalPane?: Snippet; railFoot?: Snippet; railStatus?: Snippet } = $props();
  const navigation = (() => {
    const context = navigationApplication();
    if (!context) throw new Error("Workspace requires the application navigation owner");
    return context;
  })();
  // Chief recap is dedicated content outside navigation records, so the popover
  // reads it from the same settings query the Chief conversation pane already uses.
  const chiefSettings = createQuery(() => queries.chiefSettings());
  const goalBodies=createQuery(()=>goalQueries.list());
  $effect(()=>{if(goalBodies.data)return refreshAtBoundary(goalBodies.data.next_refresh_at,()=>void goalBodies.refetch());});
  let workspace = $state.raw<NavigationPresentation | null>(null);
  let displayedPublication = $state.raw<NavigationPublication | null>(null);
  $effect(() => {
    const effective = navigation.selectors.effective;
    if (effective) {
      workspace = navigation.presentationFor(effective);
      displayedPublication = effective.publication;
    }
  });

  let opening = $derived(whatTheAddressOpens(address));
  let selectedGoalHolder=$derived(opening.markedGoalId?goalBodies.data?.goals.find(goal=>goal.id===opening.markedGoalId):undefined);
  let soleGoalManager=$derived(selectedGoalHolder?.holder_kind==='manager'&&goalsForHolder(goalBodies.data?.goals??[],{kind:'manager',id:selectedGoalHolder.holder_id}).sole?selectedGoalHolder.holder_id:null);
  let managerId=$derived(opening.openManagerId??soleGoalManager);
  const selectedPaneModule = $derived.by(() => {
    if (opening.markedTicketId) return cachedImport("./TicketRoute.svelte", () => import("./TicketRoute.svelte"));
    if (managerId) return cachedImport("../components/ManagerWorkspace.svelte", () => import("../components/ManagerWorkspace.svelte"));
    switch (opening.markedUtility) {
      case "usage": return cachedImport("./BackendsRoute.svelte", () => import("./BackendsRoute.svelte"));
      case "settings": return cachedImport("./SettingsRoute.svelte", () => import("./SettingsRoute.svelte"));
      case "ideas": return cachedImport("./IdeasRoute.svelte", () => import("./IdeasRoute.svelte"));
      default: return null;
    }
  });
  let paneShape = $derived(opening.markedUtility === "usage" || opening.markedUtility === "ideas" ? "rows" as const : "document" as const);
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
  const selectedItemWorkspace = createQuery(() => ({ ...queries.managerWorkspaceContent(managerId ?? ""), enabled: managerId !== null }));
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
  let goalRails=$derived.by(()=>{
    const bodies=goalBodies.data?.goals??[];
    const goals=bodies.map(goal=>({...goal,...workspace?.rawGoalsById.get(goal.id)?.value}));
    return [{kind:'chief' as const,id:'chief'},...desiredRail.items.map(item=>({kind:'manager' as const,id:item.id}))].map(owner=>{
      const {sole}=goalsForHolder(goals,owner);
      const window=navigationWindow(workspace,'goals',owner);
      return {owner,sole:owner.kind==='manager'?sole:null,
        rows:(window?.members??[]).flatMap(member=>{const goal=goals.find(goal=>goal.id===member.id);return goal?[goal]:[];}),window};
    });
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
    // Rows share one budget, so only holders with goals ask, and only for as many rows as they hold.
    const held=new Map<string,number>();
    for(const goal of goalBodies.data?.goals??[]){const key=goal.holder_kind==='manager'?goal.holder_id!:'chief';held.set(key,(held.get(key)??0)+1);}
    for(const owner of [{kind:'chief' as const,id:'chief'},...desiredRail.items.map(item=>({kind:'manager' as const,id:item.id}))]){
      const count=held.get(owner.id)??0;
      if(count)windows.push(viewWindow('goals',owner,{limit:goalRailWindowLimit(count,navigationWindow(workspace,'goals',owner)),...railWindows[`goals:${owner.id}`]}));
    }
    if(requestedSettledOpen)windows.push(viewWindow('settled',null,{limit:60,...railWindows.settled}));
    navigation.owner.setDemand('workspace-rail',{windows});
  });
  onDestroy(()=>navigation.owner.setDemand('workspace-rail',undefined));
  let itemPane = $derived(opening.markedManagerId !== null || soleGoalManager !== null);
  let railElement = $state<HTMLElement | null>(null);
  const motion = createRailMotionController(() => railElement, () => presentation, (next) => presentation = next,
    () => scrollCoordinator.flush(), () => scrollCoordinator.captureBeforePublication());
  $effect(() => {
    const next = {rail:desiredRail, settledOpen:requestedSettledOpen,goalRails,
      scrollPublication:{publicationId:displayedPublication?.publicationId ?? null,windows:workspace?.viewWindows ?? []}};
    untrack(() => motion.update(next));
  });
  function freezeRows(event: PointerEvent, leaving: boolean): void {
    if (event.pointerType === "touch") return;
    const target = (leaving ? event.relatedTarget : event.target) as HTMLElement | null;
    motion.freeze(Boolean(target?.closest?.(".worker-row,[data-goal-id]")));
  }
  let shellElement = $state<HTMLDivElement | null>(null);
  let railWidth = $state<number | null>(null);
  let measuredRailWidth = $state(272);
  let resizing = $state(false);
  let busyItem = $state<string | null>(null);
  let settleError = $state("");
  let hoverVisible = $state(false);
  let hoverElement = $state<HTMLDivElement | null>(null);

  let hoverInstant = $state(false);
  let hoverTarget = $state<HTMLElement | null>(null);
  let hoverTitle = $state("");
  let hoverKicker = $state("");
  let hoverWorkerId = $state<string | null>(null);
  let hoverItemId = $state<string | null>(null);
  let hoverChief = $state(false);
  let hoverGoalId = $state<string | null>(null);
  let goalHoverElement = $state<HTMLElement | null>(null);
  let hoverIntentTarget: HTMLElement | null = null;
  let restoringGoalFocus = false;
  const goalPreviewInput = createGoalRailPreviewInput();
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
  type HoverIntent = { target: HTMLElement; title: string; kicker: string; worker?: WorkItemTicketFacts; itemId?: string; chief?: boolean; goalId?: string };
  const hover = createRailHoverController<HoverIntent>((value, instant) => {
    if (!value.target.isConnected) return;
    if (hoverGoalId && value.goalId !== hoverGoalId) blurGoalPreviewInput(goalHoverElement);
    hoverTarget = value.target; hoverTitle = value.title; hoverKicker = value.kicker;
    hoverWorkerId = value.worker?.id ?? null; hoverItemId = value.itemId ?? null; hoverChief = value.chief ?? false;
    hoverGoalId = value.goalId ?? null;
    hoverInstant = instant; hoverVisible = true;
    if (instant) requestAnimationFrame(() => requestAnimationFrame(() => hoverInstant = false));
  }, () => {
    blurGoalPreviewInput(goalHoverElement);
    hoverVisible = false; hoverGoalId = null; hoverIntentTarget = null; goalPreviewInput.clearTouch();
  });
  function enterHover(value: HoverIntent, immediate = false): void {
    hoverIntentTarget = value.target;
    hover.enter(value, immediate);
  }
  function leaveGoal(target: HTMLElement): void {
    if (hoverIntentTarget === target) hover.leave();
  }
  function dismissHover(): void {
    blurGoalPreviewInput(goalHoverElement);
    hover.dispose(); hoverVisible = false; hoverGoalId = null; hoverIntentTarget = null; goalPreviewInput.clearTouch();
  }
  function hoverGoal(target: HTMLElement, goalId: string, immediate: boolean): void {
    if (restoringGoalFocus) return;
    enterHover({target,title:'',kicker:'',goalId},immediate);
  }
  $effect(() => { address.selection; untrack(() => { if (hoverGoalId) dismissHover(); }); });
  $effect(()=>{const id=hoverGoalId;if(id&&!presentation.goalRails?.some(holder=>holder.sole?.id===id||holder.rows.some(goal=>goal.id===id)))untrack(dismissHover);});
  function scrollHover(event: Event): void {
    // Only the person's own scrolling dismisses a row preview (listenForUserScroll).
    if (!hoverGoalId || goalHoverElement?.contains(event.target as Node)) return;
    // Layout and query refreshes can move outer scroll positions. Explicit user
    // input below owns dismissal; the panel's scroll observer owns repositioning.
    hover.keepOpen();
  }

  onMount(() => {
    railWidth = readRailWidth(preferenceStorage());
    const outside = (event: PointerEvent) => {
      goalPreviewInput.clearTouch();
      // A touch hold keeps every row preview after the finger lifts, so a press outside
      // the preview and its row is what dismisses it.
      const panel = hoverGoalId ? goalHoverElement : hoverElement;
      if (hoverVisible && !panel?.contains(event.target as Node) && !hoverTarget?.contains(event.target as Node)) dismissHover();
    };
    const wheel = (event: WheelEvent) => { if (hoverGoalId && !goalHoverElement?.contains(event.target as Node)) dismissHover(); };
    const key = (event: KeyboardEvent) => {
      goalPreviewInput.clearTouch();
      const source=event.target;
      if (hoverGoalId && !event.defaultPrevented && !event.altKey && !event.ctrlKey && !event.metaKey
        && ['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','PageUp','PageDown','Home','End',' '].includes(event.key)
        && source instanceof HTMLElement && !goalHoverElement?.contains(source) && !hoverTarget?.contains(source)
        && !source.closest('input,textarea,select,[contenteditable="true"]')
        && !(event.key === ' ' && source.closest('button,summary,a'))) dismissHover();
      if (event.key !== 'Escape' || !hoverVisible || !hoverGoalId || event.defaultPrevented) return;
      event.preventDefault();
      const anchor = hoverTarget;
      dismissHover(); restoringGoalFocus = true;
      if (anchor?.isConnected) anchor.focus({preventScroll:true});
      restoringGoalFocus = false;
    };
    const click = (event: MouseEvent) => goalPreviewInput.click(event);
    window.addEventListener('pointerdown',outside); window.addEventListener('wheel',wheel,{passive:true});
    window.addEventListener('scroll',scrollHover,true); window.addEventListener('keydown',key); window.addEventListener('click',click,true);
    const stopUserScroll = listenForUserScroll(window, event => {
      const target = event.target as Node | null;
      // A key on the previewed row itself moves focus, which opens the next preview.
      if (hoverGoalId || hoverElement?.contains(target) || (event.type === 'keydown' && hoverTarget?.contains(target))) return;
      hover.leave();
    });
    return () => {
      stopUserScroll();
      blurGoalPreviewInput(goalHoverElement);
      window.removeEventListener('pointerdown',outside); window.removeEventListener('wheel',wheel);
      window.removeEventListener('scroll',scrollHover,true); window.removeEventListener('keydown',key); window.removeEventListener('click',click,true);
      hover.dispose(); motion.dispose(); scrollCoordinator.dispose();
    };
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
  // The list's padding gives back what its scrollbar takes, so its two insets match.
  function railOverflow(node: HTMLElement) {
    const rail = node.closest<HTMLElement>('.workspace-rail');
    const update = () => { rail?.style.setProperty('--rail-scrollbar', `${node.offsetWidth - node.clientWidth}px`); };
    const resize = new ResizeObserver(update);
    resize.observe(node);
    update();
    return { destroy() { resize.disconnect(); } };
  }
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
    enterHover({ target, title:item.title, kicker:item.project ?? "Manager", itemId:item.id }, immediate);
  }
  function hoverWorkerRow(target: HTMLElement, worker: WorkItemTicketFacts, immediate = false): void {
    enterHover({ target, title:worker.title, kicker:"Worker", worker }, immediate);
  }

  async function settle(item: WorkspaceRailItem, settled: boolean): Promise<void> {
    busyItem = item.id; settleError = ""; dismissHover();
    try {
      await mutateJson(`/api/managers/${encodeURIComponent(item.id)}/settled`, {method:"PATCH",body:{settled}});
    }
    catch (error) { settleError = error instanceof Error ? error.message : "The manager could not be updated."; }
    finally { busyItem = null; }
  }
</script>

<section class="board-screen" data-screen="workspace">
    <div class="board-workspace-wrap">
      <div bind:this={shellElement} class="board-workspace-shell approved-rail-shell" class:resizing class:board-workspace-shell--destination={Boolean(externalPane) || opening.markedUtility !== null} class:board-workspace-shell--chief={opening.chiefMarked && !managerId} class:board-workspace-shell--item={itemPane} class:board-workspace-shell--ticket={Boolean(opening.markedTicketId)} style:--rail-width={railWidth === null ? RAIL_DEFAULT_WIDTH : `${railWidth}px`}>
        <section use:workspaceRailWash bind:this={railElement} class="board-workspace-left approved-rail workspace-rail" aria-label="Workspace" style:--settled-max-height={`${RAIL_SETTLED_MAX_HEIGHT_SHARE * 100}%`}>
          <div class="rail-work">
          <ResourceState shape="block" layout="fill" loading={!workspace} hasData={Boolean(workspace)} loadingText="Loading workspace...">
          <div class="rail-work">
            <div role="presentation" class="active-work-scroll" use:railOverflow onpointerover={(event) => freezeRows(event,false)} onpointerout={(event) => freezeRows(event,true)} data-active-work-scroll use:navigationScrollWindow={{window:renderedRailWindow('rail_parents'),pageRestoration:{coordinator:scrollCoordinator,priority:'parent',publicationId:presentation.scrollPublication?.publicationId ?? null,members:rail.items.map(item=>item.id)},activate:()=>navigation?.owner.prioritizeWindow(parentWindow?.request),selector:'.manager-card:not(.chief)',key:node=>node.dataset.manager ?? '',move:(anchor,direction)=>railPage('parents',anchor,direction)}} onscroll={scrollHover}>
            <WorkspaceRailCard {scrollCoordinator} scrollPublicationId={presentation.scrollPublication?.publicationId ?? null} navigationWindow={renderedRailWindow('rail_tickets',{kind:'chief',id:'chief'})} onWindow={(anchor,direction)=>railPage('chief',anchor,direction)} id="chief" suppressBackgroundPreview={Boolean(hoverGoalId)} title={workspace?.chief?.label ?? "Chief of Staff"} chief signal={workspace?.chief?.conversation_signal} conversationId={workspace?.chief?.conversation_id} selected={opening.chiefMarked && !opening.markedGoalId} selectedTicketId={opening.markedTicketId} groups={rail.chiefGroups} onSelect={() => window.location.hash = workspaceAddress({kind:"chief"}, opening.view)} onTicket={(id) => selectCard(id,null)} onSettle={() => undefined} onHover={(target, immediate) => enterHover({target,title:workspace?.chief?.label ?? "Chief of Staff",kicker:"Chief of Staff",chief:true}, immediate)} onWorkerHover={hoverWorkerRow} onLeave={() => {if (!hoverGoalId) hover.leave();}} >{#snippet beforeGroups()}<GoalRail structure={presentation.goalRails?.find(holder=>holder.owner.kind==='chief')} onPage={(anchor,direction)=>railPage('goals:chief',anchor,direction)} selectedId={opening.markedGoalId} previewId={hoverVisible ? hoverGoalId : null} previewTarget={hoverTarget} rail={railElement} instant={hoverInstant} previewInput={goalPreviewInput} bind:element={goalHoverElement} onHover={hoverGoal} onLeave={leaveGoal} onKeepOpen={()=>hover.keepOpen()} onDismiss={dismissHover}/>{/snippet}</WorkspaceRailCard>
            {#each rail.items as item (item.id)}
              <WorkspaceRailCard {scrollCoordinator} scrollPublicationId={presentation.scrollPublication?.publicationId ?? null} onVisibility={itemVisibility} navigationWindow={renderedRailWindow('rail_tickets',{kind:'manager',id:item.id})} onWindow={(anchor,direction)=>railPage(item.id,anchor,direction)} id={item.id} title={item.title} project={item.project} signal={item.conversationSignal} conversationId={item.conversationId} selected={opening.markedManagerId === item.id} selectedTicketId={opening.markedTicketId} groups={item.groups} busy={busyItem === item.id} onSelect={() => selectItem(item.id)} onTicket={(id) => selectCard(id,item.id)} onSettle={() => void settle(item,true)} onHover={(target, immediate) => hoverItem(target,item,immediate)} onWorkerHover={hoverWorkerRow} onLeave={() => {if (!hoverGoalId) hover.leave();}} >{#snippet beforeGroups()}<GoalRail structure={presentation.goalRails?.find(holder=>holder.owner.kind==='manager'&&holder.owner.id===item.id)} onPage={(anchor,direction)=>railPage(`goals:${item.id}`,anchor,direction)} selectedId={opening.markedGoalId} previewId={hoverVisible ? hoverGoalId : null} previewTarget={hoverTarget} rail={railElement} instant={hoverInstant} previewInput={goalPreviewInput} bind:element={goalHoverElement} onHover={hoverGoal} onLeave={leaveGoal} onKeepOpen={()=>hover.keepOpen()} onDismiss={dismissHover}/>{/snippet}</WorkspaceRailCard>
            {/each}
            {#if settleError}<p role="alert">{settleError}</p>{/if}
            </div>
          </div>
          </ResourceState>
          <!-- Outside the loading region, so the one card never remounts and shows while the workspace cannot load. -->
          {#if railStatus}<div class="rail-status">{@render railStatus()}</div>{/if}
          {#if workspace}
            <div class="settled" data-rail-motion="settled-section" class:settled--open={settledOpen}>
              <button type="button" class="settled-head" aria-expanded={settledOpen} onclick={() => { requestedSettledOpen = !requestedSettledOpen; motion.setSettledOpen(requestedSettledOpen); }}><span>Settled <span class="settled-count"><span class="count" data-settled-count>{workspace?.globalCounts?.settled ?? rail.settledItems.length}</span></span></span><svg class="settled-chevron" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m6 4 4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg></button>
              {#if presentation.settledBodyOpen ?? settledOpen}
                <div class="settled-body" data-settled-scroll use:navigationScrollWindow={{window:settledWindow,activate:()=>navigation?.owner.prioritizeWindow(settledWindow?.request),selector:'[data-settled-item]',key:node=>node.dataset.settledItem ?? '',move:(anchor,direction)=>railPage('settled',anchor,direction)}} onscroll={scrollHover}>
                {#each rail.settledItems as item (item.id)}
                  <div class="settled-item" data-settled-item={item.id} data-rail-motion={`settled:${item.id}`}>
                  <div class="settled-row">
                    <button class="settled-title" type="button" aria-current={opening.markedManagerId === item.id ? "page" : undefined} aria-label={`Open ${item.title}`} onclick={() => selectItem(item.id)}><span>{item.title}</span><ConversationSignalMark signal={item.conversationSignal} conversationId={item.conversationId} /></button>
                    <button class="reopen" type="button" disabled={busyItem === item.id} aria-label={`Reopen ${item.title}`} onclick={() => void settle(item,false)}>Reopen</button>
                  </div>
                  </div>
                {/each}
                </div>
              {/if}
            </div>
          {/if}
          </div>
          {#if railFoot}{@render railFoot()}{/if}
        </section>
        <!-- A focusable separator implements the WAI-ARIA window splitter keyboard pattern. -->
        <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
        <div tabindex="0" class="rail-grip" role="separator" aria-label="Resize workspace panel" aria-orientation="vertical" aria-valuemin={RAIL_MIN_WIDTH} aria-valuemax={RAIL_MAX_WIDTH} aria-valuenow={Math.round(measuredRailWidth)} onpointerdown={resizePointer} onpointermove={resizeMove} onpointerup={() => resizing = false} onpointercancel={() => resizing = false} onlostpointercapture={() => resizing = false} onkeydown={resizeKey} ondblclick={() => setWidth(null)} style:left={`${measuredRailWidth}px`}></div>
        <section
          class:board-workspace-right--destination={Boolean(externalPane)}
          class:board-workspace-right--ticket={Boolean(opening.markedTicketId)}
          class:board-workspace-right--chief={opening.chiefMarked && !managerId}
          class:board-workspace-right--item={itemPane}
          class:board-workspace-right--utility={opening.markedUtility !== null}
          class="board-workspace-right"
          aria-label={opening.chiefMarked && !managerId ? "Chief of Staff conversation" : itemPane ? "Manager workspace" : "Workspace inspector"}
        >
          {#if externalPane}
            {@render externalPane()}
          {:else if opening.markedGoalId && !goalBodies.data}
            <ResourceState shape="document" layout="fill" error={goalBodies.error} loading={goalBodies.isPending} loadingText="Loading goal…"/>
          {:else if opening.markedGoalId && !managerId}
            {#key opening.markedGoalId}<GoalWorkspace id={opening.markedGoalId} requestedConversationId={address.selectedConversationId ?? null} openedFile={address.openFile} onOpenFile={openFileOnTicket}/>{/key}
          {:else if opening.chiefMarked && !managerId}
            <ChiefConversation requestedConversationId={address.selectedConversationId ?? null} openedFile={address.openFile} onOpenFile={openFileOnTicket} />
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
          {/if}
          </LazyRegion>
          </div>
          {:else if selectedPaneModule}
          <LazyRegion promise={selectedPaneModule} shape={paneShape} layout="fill">
          {#if opening.markedTicketId}
            {#key opening.markedTicketId}
              {#await import("./TicketRoute.svelte") then view}
<view.default
                id={opening.markedTicketId}
                requestedConversationId={address.selectedConversationId ?? null}
                openFile={address.openFile}
                onOpenFile={openFileOnTicket}
              />
{:catch error}
<ErrorLine {error} />
{/await}
            {/key}
          {:else if itemPane && managerId}
            {#key managerId}
              {#await import("../components/ManagerWorkspace.svelte") then view}
<view.default
                itemId={managerId}
                openedFile={opening.markedGoalId?address.openFile:null}
                onOpenFile={opening.markedGoalId?openFileOnTicket:undefined}
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

<WorkspaceRailHover bind:element={hoverElement} visible={hoverVisible && !hoverGoalId} instant={hoverInstant} target={hoverTarget} title={currentHoverTitle} kicker={currentHoverKicker} signal={hoverSignal} conversationId={hoverConversationId} worker={hoverWorker} recap={hoverRecap} rail={railElement} recapTarget={hoverRecapTarget} onKeepOpen={() => hover.keepOpen()} onLeave={() => {if (!hoverGoalId) hover.leave();}} />
<style>
  /* The pane's own scroller for a utility screen. It is here, scoped to this component,
     rather than in the shared stylesheet, so no skeleton rule can take it away. */
  .board-workspace-utility { flex:1; min-height:0; overflow:auto; display:flex; flex-direction:column; }
  /* Whatever stands in the pane takes the pane's height, so a screen that is still
     arriving reserves the whole pane rather than stopping part of the way down. The
     selector names no class: the only element this reaches is the block the screen is
     mounted in, beside a text-only status node. */
  .board-workspace-utility > :global(div) { flex:1 0 auto; min-height:0; }
  .approved-rail-shell { position:relative; isolation:isolate; grid-template-columns:var(--rail-width) minmax(0,1fr); }
  /* The rail's isolated wash layers also contain its fixed goal preview. Keep that
     whole stack above pane content without moving native controls out of DOM order. */
  .workspace-rail { position:relative; z-index:1; }
  .board-workspace-right { position:relative; z-index:0; }
  /* No line divides the rail from the workspace: the rail's own surface (#1b1b1b against the
     page's #121212) is the whole division. The list's first fill starts --rail-top down the rail: the list keeps --rail-ring-room of
     that inside itself, so a focus ring around the first header is never cut at the top. */
  .approved-rail { display:flex; flex-direction:column; height:100%; min-height:0; overflow:hidden; scrollbar-gutter:auto; font-size:var(--type-sm); line-height:normal; padding:calc(var(--rail-top) - var(--rail-ring-room)) 0 var(--space-2); border-right:0; }
  .rail-work { position:relative; display:flex; flex-direction:column; flex:1; min-height:0; }
  /* The list's two edges always fade into the rail over 8px: a plain layer over the list's
     box, the same whatever is scrolled where, never a mask on the list (so a preview that
     opens from a row is never masked with it). Every row is always drawn; the list scrolls. */
  .rail-work .rail-work::before, .rail-work .rail-work::after { content:""; position:absolute; z-index:3; left:0; right:0; height:var(--rail-edge-fade); pointer-events:none; }
  .rail-work .rail-work::before { top:0; bottom:auto; background:linear-gradient(to bottom, var(--surface-rail), transparent); }
  .rail-work .rail-work::after { bottom:0; background:linear-gradient(to top, var(--surface-rail), transparent); }
  /* The rail's lists scroll themselves. The wrapper clips their bleed, scrollbar
     included, without becoming a scroller that a hover could slide sideways. */
  .rail-work > :global(.skeleton-region--fill > .skeleton-content) { overflow:clip; }
  /* The list's fills sit --rail-edge in from both sides of the column: its scrollbar is kept
     to the column's own edge, drawn only while the pointer is over the list, so the right
     inset matches the left with or without one. */
  .active-work-scroll { flex:1; min-height:0; overflow-x:hidden; overflow-y:auto; scrollbar-gutter:stable; scrollbar-width:auto; scrollbar-color:auto; padding:var(--rail-ring-room) max(0px, calc(var(--rail-edge) - var(--rail-scrollbar, 0px))) var(--rail-foot-clear) var(--rail-edge); scroll-padding-block:var(--rail-edge-fade); }
  .active-work-scroll::-webkit-scrollbar { width:var(--rail-scrollbar-width); }
  .active-work-scroll::-webkit-scrollbar-track { background:transparent; }
  .active-work-scroll::-webkit-scrollbar-thumb { border-radius:var(--radius-pill); background:transparent; }
  .active-work-scroll:is(:hover, :active)::-webkit-scrollbar-thumb { background:var(--border-hover); }
  @supports not selector(::-webkit-scrollbar) { .active-work-scroll { scrollbar-width:thin; scrollbar-color:var(--border-color) transparent; } }

  .settled-body { min-height:0; overflow-x:hidden; overflow-y:auto; scrollbar-width:thin; }
  .rail-grip { position:absolute; top:0; bottom:0; width:9px; z-index:20; transform:translateX(-4px); cursor:col-resize; background:transparent; border:0; padding:0; touch-action:none; }
  .rail-grip::after { content:""; position:absolute; top:50%; left:50%; width:var(--space-1); height:var(--space-6); transform:translate(-50%,-50%) scaleY(var(--rail-grip-rest-scale)); border-radius:var(--radius-pill); background:var(--border-color); opacity:0; pointer-events:none; transition:opacity var(--motion-base) var(--motion-ease),background var(--motion-base) var(--motion-ease),transform var(--motion-enter) var(--motion-ease); }
  .rail-grip:hover::after,.rail-grip:focus-visible::after,.resizing .rail-grip::after { opacity:1; transform:translate(-50%,-50%) scaleY(1); }
  .resizing .rail-grip::after { background:var(--text-strong); }
  .resizing { user-select:none; cursor:col-resize; }
  .rail-status { flex:none; padding:var(--space-2) var(--rail-edge) 0; }
  /* The rail's foot is a fixed band with one hairline edge; the list above ends at it. */
  .settled { position:relative; display:flex; flex-direction:column; flex:0 1 auto; min-height:0; max-height:var(--settled-max-height); margin-inline:var(--rail-edge); border-top:var(--border-hairline) solid var(--border-color); padding-top:var(--space-1); }
  /* The list ends at the foot's hairline and is clipped there cleanly: no fade leaves a row
     half legible. Its bottom padding lets the last row scroll fully clear of the edge. */
  /* Settled: one row in the rail's row voice, staging's 32px tall, its count a plain Small
     numeral after it. */
  .settled-head { flex:none; display:flex; align-items:center; justify-content:space-between; gap:var(--space-3); box-sizing:border-box; height:32px; min-height:0; padding:0 var(--rail-pad); margin-inline:0; border:0; border-radius:var(--radius-md); background:none; width:100%; font:inherit; font-size:var(--type-row); font-weight:var(--weight-body); line-height:20px; color:var(--rail-tone-rest); cursor:pointer; }
  .settled-head:hover { background:var(--wash-hover); color:var(--rail-tone-bright); }
  .settled-count { position:relative; display:inline-block; }
  .settled-chevron { width:14px; height:14px; flex:none; color:var(--rail-tone-label); transition:transform var(--motion-base) var(--motion-ease); }
  .settled--open .settled-chevron { transform:rotate(90deg); }
  .count { flex:none; margin-left:5px; font-size:var(--type-small); font-weight:var(--weight-small); color:var(--rail-tone-label); font-variant-numeric:tabular-nums; }
  .settled-item + .settled-item { margin-top:var(--rail-row-gap); }
  .settled-row { display:flex; align-items:center; gap:var(--space-2); box-sizing:border-box; min-height:var(--rail-row-height); padding:5px var(--rail-pad); margin:0; border-radius:var(--radius-md); font-size:var(--type-row); line-height:20px; }
  .settled-row:hover { color:var(--rail-tone-bright); }
  .settled-title,.reopen { border:0; background:none; font:inherit; cursor:pointer; color:var(--text-faintest); padding:0; }
  .settled-title { flex:1; display:flex; align-items:center; gap:var(--space-2); text-align:left; min-width:0; color:var(--rail-tone-rest); }
  .settled-title > span { flex:1; min-width:0; overflow-wrap:anywhere; }
  .reopen { opacity:0; margin-left:auto; color:var(--rail-tone-label); font-size:var(--type-small); }
  .settled-row:hover .reopen,.settled-row:focus-within .reopen { opacity:1; }
  button:focus-visible { outline:var(--focus-width) solid var(--focus-ring); outline-offset:2px; }
  @media(prefers-reduced-motion:reduce) {
    .rail-grip::after { transition:none; }
  }
  @media(max-width:720px) {
    /* Phone shows the card above the bottom bar instead. */
    .rail-status { display:none; }
    .approved-rail-shell { grid-template-columns:1fr; }
    .rail-grip { display:none; }
    .board-workspace-shell--chief .approved-rail,
    .board-workspace-shell--item .approved-rail,
    .board-workspace-shell--ticket .approved-rail,
    .board-workspace-shell--destination .approved-rail { display:none; }
    .board-workspace-right--destination { display:flex; padding:0; }
  }
</style>

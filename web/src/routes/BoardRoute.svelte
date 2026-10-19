<script lang="ts">
  import { untrack, tick, onDestroy } from "svelte";
  import { navigationApplication } from "../lib/navigation/application.svelte";
  import { type NavigationPresentation } from "../lib/navigation/presentation";
  import { stabilizeWorkspaceRail } from "../lib/workspaceRail";
  import { workspaceWorkerStageSentence } from "../lib/workspaceRail";
  import { lifecycleFor } from "../lib/lifecycle";
  import type { RenderObserver } from "../lib/navigation/visibility";
  import type { NavigationPublication } from "../lib/navigation/collection";
  import { railDomMatches, hoverDomMatches } from "../lib/workspaceRailDom";
  import { workspaceRailImpact, workspaceHoverImpact, orchestratorSettleImpact } from "../lib/workspaceNavigationImpact";
  import { createRailMotionController, type WorkspaceRailPresentation } from "../lib/workspaceRailMotion";
  import { onMount } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../lib/queryCatalogue";
  import { mutateJson } from "../lib/mutate";
  import { buildWorkspaceRail, workspaceListRecap, workspaceRailChildMessageConversations, type WorkspaceRailItem } from "../lib/workspaceRail";
  import { RAIL_MIN_WIDTH, RAIL_MAX_WIDTH, RAIL_DEFAULT_WIDTH, RAIL_SETTLED_MAX_HEIGHT_SHARE, railPreview, clampRailWidth, readRailWidth, saveRailWidth, railWidthForKey, createRailHoverController } from "../lib/workspaceRailInteraction";
  import { whatTheAddressOpens, workspaceAddress, type WorkspaceAddress } from "../lib/workspaceAddress";
  import type { ManagedFileTarget } from "../lib/filePreview";
  import type { WorkspaceMessageConversation } from "../lib/types";
  import type { WorkItemTicketFacts } from "../lib/workItemPresentation";
  import ConversationSignalMark from "../components/ConversationSignalMark.svelte";
  import WorkspaceMessages from "../components/WorkspaceMessages.svelte";
  import ChiefConversation from "../components/ChiefConversation.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import SprintItemWorkspace from "../components/SprintItemWorkspace.svelte";
  import WorkspaceRailCard from "../components/WorkspaceRailCard.svelte";
  import WorkspaceRailHover from "../components/WorkspaceRailHover.svelte";
  import TicketRoute from "./TicketRoute.svelte";
  import BackendsRoute from "./BackendsRoute.svelte";
  import SettingsRoute from "./SettingsRoute.svelte";
  import IdeasRoute from "./IdeasRoute.svelte";
  import FeedbackRoute from "./FeedbackRoute.svelte";

  let { address, onDismissed = () => undefined }: { address: WorkspaceAddress; onDismissed?: (noteId: string) => void } = $props();
  const navigation = (() => {
    const context = navigationApplication();
    if (!context) throw new Error("Workspace requires the application navigation owner");
    return context;
  })();
  const manifests = createQuery(() => queries.workerTypeManifests());
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
  const feedbackCount = createQuery(() => queries.feedbackCount());
  let opening = $derived(whatTheAddressOpens(address));
  const selectedItemWorkspace = createQuery(() => ({ ...queries.sprintItemWorkspaceContent(opening.openItemId ?? ""), enabled: opening.openItemId !== null }));
  let allCards = $derived(workspace?.tickets ?? []);
  let desiredRail = $derived(buildWorkspaceRail(allCards, workspace?.orchestrators ?? []));
  let presentation = $state<WorkspaceRailPresentation>({rail:buildWorkspaceRail([]), settledOpen:false, settledMessages:{}});
  let rail = $derived(presentation.rail);
  let settledOpen = $derived(presentation.settledOpen);
  let requestedSettledOpen = $state(false);
  let messages = $derived(workspace?.messages ?? []);
  let itemPane = $derived(opening.markedItemId !== null);
  let railElement = $state<HTMLElement | null>(null);
  const motion = createRailMotionController(() => railElement, () => presentation, (next) => presentation = next);
  $effect(() => {
    const next = {rail:desiredRail, settledOpen:requestedSettledOpen,
      settledMessages:Object.fromEntries(desiredRail.settledItems.map((item) =>
        [item.id, workspaceRailChildMessageConversations(messages, item, allCards)]))};
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
  let hoverConversationIdOverride = $state<string | null>(null);
  let hoverWorkerId = $state<string | null>(null);
  let hoverItemId = $state<string | null>(null);
  let hoverChief = $state(false);
  let hoverWorker = $derived.by(() => {
    const ticket = allCards.find((card) => card.id === hoverWorkerId)
      ?? [...desiredRail.items,...desiredRail.settledItems].flatMap(item => item.tickets).find(ticket => ticket.id === hoverWorkerId);
    if (!ticket) return null;
    return hoverConversationIdOverride === null ? ticket : {...ticket,conversation_id:hoverConversationIdOverride,
      conversation_signal:workspace?.conversationSignals.get(hoverConversationIdOverride)};
  });
  let hoveredItem = $derived([...desiredRail.items, ...desiredRail.settledItems].find((item) => item.id === hoverItemId));
  let hoverRecap = $derived(hoverChief ? workspaceListRecap(desiredRail.chiefGroups)
    : workspaceListRecap(hoveredItem?.groups ?? [], hoveredItem?.recap));
  let hoverSignal = $derived(hoverConversationIdOverride !== null ? workspace?.conversationSignals.get(hoverConversationIdOverride)
    : hoverChief ? workspace?.chief?.conversation_signal : hoveredItem?.conversationSignal);
  let hoverConversationId = $derived(hoverConversationIdOverride ?? (hoverChief ? workspace?.chief?.conversation_id : hoveredItem?.conversationId));
  let currentHoverTitle = $derived(hoverWorker?.title ?? (hoverChief ? workspace?.chief?.label : hoveredItem?.title) ?? hoverTitle);
  let currentHoverKicker = $derived.by(() => {
    if (!hoverWorker) return hoverKicker;
    const parent = [...desiredRail.items, ...desiredRail.settledItems]
      .find((item) => item.tickets.some((ticket) => ticket.id === hoverWorker.id));
    if (parent) return parent.title;
    const card = allCards.find((ticket) => ticket.id === hoverWorker.id);
    return card && (card.orchestrator?.kind === "chief" || (!card.orchestrator && !card.sprint_item_id))
      ? workspace?.chief?.label ?? "Chief of Staff" : "Worker";
  });
  const hover = createRailHoverController<{ target: HTMLElement; title: string; kicker: string; worker?: WorkItemTicketFacts; itemId?: string; chief?: boolean; conversationId?:string }>((value, instant) => {
    if (!value.target.isConnected) return;
    hoverTarget = value.target; hoverTitle = value.title; hoverKicker = value.kicker;
    hoverConversationIdOverride = value.conversationId ?? null;
    hoverWorkerId = value.worker?.id ?? null; hoverItemId = value.itemId ?? null; hoverChief = value.chief ?? false;
    hoverInstant = instant; hoverVisible = true;
    if (instant) requestAnimationFrame(() => requestAnimationFrame(() => hoverInstant = false));
  }, () => { hoverVisible = false; });

  onMount(() => {
    railWidth = readRailWidth(preferenceStorage());
    return () => { hover.dispose(); motion.dispose(); };
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
    window.location.hash = workspaceAddress(address.selection, address.view, file);
  }
  function selectCard(id: string, insideItemId: string | null): void {
    window.location.hash = workspaceAddress(insideItemId ? { kind: "ticket", id, openedFromItemId: insideItemId } : { kind: "ticket", id }, opening.view);
  }
  function selectItem(id: string): void { window.location.hash = workspaceAddress({ kind: "item", id }); }
  function hoverItem(target: HTMLElement, item: WorkspaceRailItem, immediate = false): void {
    hover.enter({ target, title:item.title, kicker:item.project ?? "Orchestrator", itemId:item.id }, immediate);
  }
  function hoverWorkerRow(target: HTMLElement, worker: WorkItemTicketFacts, immediate = false): void {
    hover.enter({ target, title:worker.title, kicker:"Worker", worker }, immediate);
  }
  function hoverMessageRow(target: HTMLElement, message: WorkspaceMessageConversation, immediate = false): void {
    if (message.kind === "chief") { hover.enter({target,title:message.title,kicker:"Chief of Staff",chief:true,conversationId:message.conversationId}, immediate); return; }
    if (message.kind === "sprint_item") { hover.enter({target,title:message.title,kicker:"Orchestrator",itemId:message.id,conversationId:message.conversationId}, immediate); return; }
    const worker = allCards.find((card) => card.id === message.id)
      ?? [...desiredRail.items, ...desiredRail.settledItems].flatMap((item) => item.tickets).find((ticket) => ticket.id === message.id);
    if (worker) {
        hover.enter({target,title:worker.title,kicker:"Worker",worker,conversationId:message.conversationId},immediate);
    }
    else hover.enter({target, title:message.title, kicker:"Worker"}, immediate);
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
      if (railDomMatches(root,target,chief,open,messages,allCards)) railObserver.acknowledgeRender(publication,observation);
    });
    return () => { obsolete = true; };
  });
  $effect(() => {
    if (!hoverVisible || !hoverElement) return;
    const observer = navigation.registerRenderBoundary(`workspace-hover:${crypto.randomUUID()}`,impact => {
      const kind=hoverWorkerId ? 'ticket' : hoverItemId ? 'orchestrator' : 'chief';
      const id=hoverWorkerId ?? hoverItemId ?? 'chief';
      const view=navigation.state.effective;
      return workspaceHoverImpact(impact,kind,id,hoverWorker?.conversation_id ?? hoverConversationId,view ? navigation.presentationFor(view) : undefined,
        kind==='chief' || kind==='orchestrator' && !hoveredItem?.recap.trim());
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
    const sourceKind = hoverWorkerId ? 'ticket' : hoverItemId ? 'orchestrator' : hoverChief ? 'chief' : null;
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
      await navigation.runCommand(orchestratorSettleImpact(item.id),responseMetadata =>
        mutateJson(`/api/items/${encodeURIComponent(item.id)}/settled`, {method:"PATCH",body:{settled},responseMetadata}),"set orchestrator settled");
    }
    catch (error) { settleError = error instanceof Error ? error.message : "The orchestrator could not be updated."; }
    finally { busyItem = null; }
  }
</script>

<section class="board-screen" data-screen="workspace">
  <ResourceState error={navigation.state.error} loading={!workspace} hasData={Boolean(workspace)} loadingText="Loading workspace...">
    <div class="board-workspace-wrap">
      <div bind:this={shellElement} class="board-workspace-shell approved-rail-shell" class:resizing class:board-workspace-shell--chief={opening.chiefMarked} class:board-workspace-shell--item={itemPane} class:board-workspace-shell--ticket={Boolean(opening.markedTicketId)} style:--rail-width={railWidth === null ? RAIL_DEFAULT_WIDTH : `${railWidth}px`}>
        <section bind:this={railElement} class="board-workspace-left approved-rail" aria-label="Workspace" style:--settled-max-height={`${RAIL_SETTLED_MAX_HEIGHT_SHARE * 100}%`}>
          <div class="rail-work">
            <div role="presentation" class="active-work-scroll" onpointerover={(event) => freezeRows(event,false)} onpointerout={(event) => freezeRows(event,true)} data-active-work-scroll onscroll={() => hover.leave()}>
            <WorkspaceRailCard id="chief" title={workspace?.chief?.label ?? "Chief of Staff"} chief signal={workspace?.chief?.conversation_signal} conversationId={workspace?.chief?.conversation_id} selected={opening.chiefMarked} selectedTicketId={opening.markedTicketId} groups={rail.chiefGroups} onSelect={() => window.location.hash = workspaceAddress({kind:"chief"}, opening.view)} onTicket={(id) => selectCard(id,null)} onSettle={() => undefined} onHover={(target, immediate) => hover.enter({target,title:workspace?.chief?.label ?? "Chief of Staff",kicker:"Chief of Staff",chief:true}, immediate)} onWorkerHover={hoverWorkerRow} onLeave={() => hover.leave()} />
            {#each rail.items as item (item.id)}
              <WorkspaceRailCard id={item.id} title={item.title} project={item.project} signal={item.conversationSignal} conversationId={item.conversationId} selected={opening.markedItemId === item.id} selectedTicketId={opening.markedTicketId} groups={item.groups} busy={busyItem === item.id} onSelect={() => selectItem(item.id)} onTicket={(id) => selectCard(id,item.id)} onSettle={() => void settle(item,true)} onHover={(target, immediate) => hoverItem(target,item,immediate)} onWorkerHover={hoverWorkerRow} onLeave={() => hover.leave()} />
            {/each}
            {#if settleError}<p role="alert">{settleError}</p>{/if}
            </div>
            <div class="settled" data-rail-motion="settled-section" class:settled--open={settledOpen}>
              <button type="button" class="settled-head" aria-expanded={settledOpen} onclick={() => requestedSettledOpen = !requestedSettledOpen}><span>Settled <span class="settled-count"><span class="count" data-settled-count>({rail.settledItems.length})</span></span></span><span class="rule"></span><svg class="settled-chevron" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m5 10 3-3 3 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg></button>
              {#if settledOpen}
                <div class="settled-body" data-settled-scroll onscroll={() => hover.leave()}>
                {#each rail.settledItems as item (item.id)}
                  <div class="settled-item" data-settled-item={item.id} data-rail-motion={`settled:${item.id}`}>
                  <div class="settled-row">
                    <button class="settled-title" type="button" onclick={() => selectItem(item.id)} use:railPreview={{show:(target, immediate) => hoverItem(target,item,immediate), leave:() => hover.leave()}}><span>{item.title}</span><ConversationSignalMark signal={item.conversationSignal} conversationId={item.conversationId} /></button>
                    <button class="reopen" type="button" disabled={busyItem === item.id} aria-label={`Reopen ${item.title}`} onclick={() => void settle(item,false)}>Reopen</button>
                  </div>
                  <WorkspaceMessages conversations={presentation.settledMessages[item.id] ?? []} motionPrefix={item.id} onHover={hoverMessageRow} onLeave={() => hover.leave()} />
                  </div>
                {/each}
                </div>
              {/if}
            </div>
          </div>
          <nav class="rail-utilities" aria-label="Workspace utilities">
            <a href={workspaceAddress({kind:"utility",utility:"usage"}, opening.view)} aria-current={opening.markedUtility === "usage" ? "page" : undefined} aria-label="Usage and Backends" title="Usage and Backends"><svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M4 15V9m6 6V4m6 11v-8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg></a>
            <a href={workspaceAddress({kind:"utility",utility:"settings"}, opening.view)} aria-current={opening.markedUtility === "settings" ? "page" : undefined} aria-label="Settings" title="Settings"><svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m8 3-.5 2-1.4.8-2-.5-2 3.4L3.5 10l.1 1.6-1.4 1.4 2 3.4 2-.5 1.4.8.5 2h4l.5-2 1.4-.8 2 .5 2-3.4-1.4-1.4.1-1.6 1.4-1.3-2-3.4-2 .5-1.4-.8-.5-2Z" transform="translate(0 -1)" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" /><circle cx="10" cy="10" r="2.5" stroke="currentColor" stroke-width="1.2" /></svg></a>
            <a href={workspaceAddress({kind:"utility",utility:"ideas"}, opening.view)} aria-current={opening.markedUtility === "ideas" ? "page" : undefined} aria-label="Ideas" title="Ideas"><svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M7 14h6m-5 3h4M6 10a5 5 0 1 1 8 0l-1 2H7Z" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /></svg></a>
            <a href={workspaceAddress({kind:"utility",utility:"feedback"}, opening.view)} aria-current={opening.markedUtility === "feedback" ? "page" : undefined} aria-label="Feedback" title="Feedback"><svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M3 4h14v10H8l-5 3Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" /></svg>{#if (feedbackCount.data?.open_count || 0) > 0}<span class="fb-count">{feedbackCount.data?.open_count}</span>{/if}</a>
          </nav>
        </section>
        <!-- A focusable separator implements the WAI-ARIA window splitter keyboard pattern. -->
        <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
        <div tabindex="0" class="rail-grip" role="separator" aria-label="Resize workspace panel" aria-orientation="vertical" aria-valuemin={RAIL_MIN_WIDTH} aria-valuemax={RAIL_MAX_WIDTH} aria-valuenow={Math.round(measuredRailWidth)} onpointerdown={resizePointer} onpointermove={resizeMove} onpointerup={() => resizing = false} onpointercancel={() => resizing = false} onlostpointercapture={() => resizing = false} onkeydown={resizeKey} ondblclick={() => setWidth(null)} style:left={`${measuredRailWidth}px`}></div>
        <section
          class:board-workspace-right--ticket={Boolean(opening.markedTicketId)}
          class:board-workspace-right--chief={opening.chiefMarked}
          class:board-workspace-right--item={itemPane}
          class:board-workspace-right--utility={opening.markedUtility !== null}
          class="board-workspace-right"
          aria-label={opening.chiefMarked ? "Chief of Staff conversation" : itemPane ? "Orchestrator workspace" : "Workspace inspector"}
        >
          {#if opening.chiefMarked}
            <ChiefConversation showRequestErrorLine={false} />
          {:else if opening.markedTicketId}
            {#key opening.markedTicketId}
              <TicketRoute
                navigationErrorHandledByParent
                id={opening.markedTicketId}
                openFile={address.openFile}
                onOpenFile={openFileOnTicket}
              />
            {/key}
          {:else if itemPane && opening.openItemId}
            {#key opening.openItemId}
              <SprintItemWorkspace
                navigationErrorHandledByParent
                itemId={opening.openItemId}
                sprintName="Workspace"
                backHref="#/workspace"
                workspaceResource={{
                  data: selectedItemWorkspace.data,
                  error: selectedItemWorkspace.error,
                  isLoading: selectedItemWorkspace.isLoading
                }}
              />
            {/key}
          {:else if opening.markedUtility === "usage"}
            <BackendsRoute />
          {:else if opening.markedUtility === "settings"}
            <SettingsRoute />
          {:else if opening.markedUtility === "ideas"}
            <IdeasRoute />
          {:else if opening.markedUtility === "feedback"}
            <FeedbackRoute {onDismissed} />
          {:else}
            <div class="board-workspace-empty-inspector">
              <span>Select an orchestrator or Worker to inspect it.</span>
            </div>
          {/if}
        </section>
      </div>
    </div>
  </ResourceState>
</section>

<WorkspaceRailHover bind:element={hoverElement} visible={hoverVisible} instant={hoverInstant} target={hoverTarget} title={currentHoverTitle} kicker={currentHoverKicker} signal={hoverSignal} conversationId={hoverConversationId} worker={hoverWorker} recap={hoverRecap} rail={railElement} />
<style>
  .approved-rail-shell { position:relative; isolation:isolate; grid-template-columns:var(--rail-width) minmax(0,1fr); }
  .approved-rail { display:flex; flex-direction:column; height:100%; min-height:0; overflow:hidden; font-size:var(--type-sm); line-height:normal; padding:var(--space-3); }
  .rail-work { display:flex; flex-direction:column; flex:1; min-height:0; }
  .active-work-scroll { flex:1; min-height:0; overflow:auto; scrollbar-gutter:stable; scrollbar-width:thin; margin-right:calc(0px - var(--space-3)); padding-right:var(--space-3); }
  .settled-body { min-height:0; overflow:auto; scrollbar-gutter:stable; scrollbar-width:thin; margin-right:calc(0px - var(--space-3)); padding-right:var(--space-3); }
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
  .rail-utilities { display:flex; flex:none; align-items:center; gap:var(--space-1); padding-top:var(--space-2); border-top:var(--border-hairline) solid var(--border-color); }
  .rail-utilities a { display:flex; align-items:center; justify-content:center; width:32px; height:32px; color:var(--text-faintest); border:var(--border-hairline) solid transparent; border-radius:var(--radius-sm); }
  .rail-utilities a[aria-current="page"], .rail-utilities a[aria-current="page"]:hover, .rail-utilities a[aria-current="page"]:focus-within { border-color:var(--accent-bright); }
  .rail-utilities a:hover { background-image:var(--interaction-hover); color:var(--text-strong); }
  .rail-utilities svg { width:18px; height:18px; }
  .rail-utilities a:focus-visible { outline:var(--border-strong) solid var(--accent-bright); outline-offset:2px; }
  .rule { flex:1; height:var(--border-hairline); background:var(--border-color); }
  .count { flex:none; font-size:var(--type-xs); color:var(--text-faintest); font-variant-numeric:tabular-nums; }
  .settled-item + .settled-item { margin-top:var(--space-3); }
  .settled-row { display:flex; align-items:center; gap:var(--space-2); padding:var(--space-1) var(--space-2); margin:0 calc(var(--space-2) * -1); border-radius:var(--radius-sm); }
  .settled-row:hover { background-image:var(--interaction-hover); }
  .settled-title,.reopen { border:0; background:none; font:inherit; cursor:pointer; color:var(--text-faintest); padding:0; }
  .settled-title { flex:1; display:flex; align-items:center; gap:var(--space-2); text-align:left; min-width:0; }
  .settled-title > span { flex:1; }
  .reopen { opacity:0; margin-left:auto; color:var(--text-faint); font-size:12px; }
  .settled-row:hover .reopen,.settled-row:focus-within .reopen { opacity:1; }
  button:focus-visible { outline:var(--border-strong) solid var(--accent-bright); outline-offset:2px; }
  @media(max-width:960px) {
    .approved-rail-shell { grid-template-columns:1fr; }
    .rail-grip { display:none; }
    .board-workspace-shell--chief .approved-rail,
    .board-workspace-shell--item .approved-rail,
    .board-workspace-shell--ticket .approved-rail { display:none; }
  }
</style>

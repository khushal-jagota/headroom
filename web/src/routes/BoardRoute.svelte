<script lang="ts">
  import { onMount } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../lib/queryCatalogue";
  import { mutateJson } from "../lib/mutate";
  import { buildWorkspaceRail, type WorkspaceRailItem, type WorkspaceTicketGroup } from "../lib/workspaceRail";
  import { RAIL_MIN_WIDTH, RAIL_MAX_WIDTH, RAIL_DEFAULT_WIDTH, RAIL_SETTLED_MAX_HEIGHT_SHARE, clampRailWidth, readRailWidth, saveRailWidth, railWidthForKey, createRailHoverController } from "../lib/workspaceRailInteraction";
  import { whatTheAddressOpens, workspaceAddress, type WorkspaceAddress } from "../lib/workspaceAddress";
  import type { ManagedFileTarget } from "../lib/filePreview";
  import type { WorkspaceMessageConversation } from "../lib/types";
  import type { WorkItemTicketFacts } from "../lib/workItemPresentation";
  import { workspaceChildMessageConversations, workspaceMessageConversations } from "../lib/workspaceMessageDiscovery";
  import StageMark from "../components/StageMark.svelte";
  import WorkspaceMessages from "../components/WorkspaceMessages.svelte";
  import ChiefConversation from "../components/ChiefConversation.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import SprintItemWorkspace from "../components/SprintItemWorkspace.svelte";
  import WorkspaceRailCard from "../components/WorkspaceRailCard.svelte";
  import WorkspaceRailHover from "../components/WorkspaceRailHover.svelte";
  import TicketRoute from "./TicketRoute.svelte";

  let { address }: { address: WorkspaceAddress } = $props();
  const board = createQuery(() => queries.board());
  const workers = createQuery(() => queries.workers());
  let opening = $derived(whatTheAddressOpens(address));
  const selectedItemWorkspace = createQuery(() => ({ ...queries.sprintItemWorkspace(opening.openItemId ?? ""), enabled: opening.openItemId !== null }));
  let allCards = $derived((board.data?.columns ?? []).flatMap((column) => column.cards));
  let rail = $derived(buildWorkspaceRail(allCards, board.data?.sprint_items ?? []));
  let messages = $derived(workspaceMessageConversations(allCards, board.data?.sprint_items ?? [], workers.data?.chief_of_staff));
  let itemPane = $derived(opening.markedItemId !== null);
  let railElement = $state<HTMLElement | null>(null);
  let shellElement = $state<HTMLDivElement | null>(null);
  let railWidth = $state<number | null>(null);
  let measuredRailWidth = $state(272);
  let resizing = $state(false);
  let settledOpen = $state(false);
  let busyItem = $state<string | null>(null);
  let settleError = $state("");
  let hoverVisible = $state(false);
  let hoverInstant = $state(false);
  let hoverTarget = $state<HTMLElement | null>(null);
  let hoverTitle = $state("");
  let hoverKicker = $state("");
  let hoverWorker = $state<WorkItemTicketFacts | null>(null);
  let hoverGroups = $state<WorkspaceTicketGroup[] | null>(null);
  const hover = createRailHoverController<{ target: HTMLElement; title: string; kicker: string; worker?: WorkItemTicketFacts; groups?: WorkspaceTicketGroup[] }>((value, instant) => {
    hoverTarget = value.target; hoverTitle = value.title; hoverKicker = value.kicker;
    hoverWorker = value.worker ?? null; hoverGroups = value.groups ?? null;
    hoverInstant = instant; hoverVisible = true;
    if (instant) requestAnimationFrame(() => requestAnimationFrame(() => hoverInstant = false));
  }, () => hoverVisible = false);

  onMount(() => {
    railWidth = readRailWidth(preferenceStorage());
    return () => hover.dispose();
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
  function hoverItem(target: HTMLElement, item: WorkspaceRailItem): void {
    hover.enter({ target, title:item.title, kicker:item.project ?? "Orchestrator", groups:item.hoverGroups });
  }
  function hoverWorkerRow(target: HTMLElement, worker: WorkItemTicketFacts): void {
    if (worker.stage === "cancelled") hover.enter({target, title:worker.title, kicker:"Cancelled Worker"});
    else hover.enter({ target, title:worker.title, kicker:"Worker", worker });
  }
  function hoverMessageRow(target: HTMLElement, message: WorkspaceMessageConversation): void {
    const worker = allCards.find((card) => card.id === message.id)
      ?? rail.settledItems.flatMap((item) => item.tickets).find((ticket) => ticket.id === message.id);
    if (worker) hoverWorkerRow(target, worker);
    else hover.enter({target, title:message.title, kicker:"Worker"});
  }
  async function settle(item: WorkspaceRailItem, settled: boolean): Promise<void> {
    busyItem = item.id; settleError = ""; hoverVisible = false; hover.leave();
    try { await mutateJson(`/api/items/${encodeURIComponent(item.id)}/settled`, { method:"PATCH", body:{settled} }); }
    catch (error) { settleError = error instanceof Error ? error.message : "The orchestrator could not be updated."; }
    finally { busyItem = null; }
  }
</script>

<section class="board-screen" data-screen="workspace">
  <ResourceState error={board.error} loading={board.isFetching} hasData={Boolean(board.data)} loadingText="Loading workspace...">
    <div class="board-workspace-wrap">
      <div bind:this={shellElement} class="board-workspace-shell approved-rail-shell" class:resizing class:board-workspace-shell--chief={opening.chiefMarked} class:board-workspace-shell--item={itemPane} class:board-workspace-shell--ticket={Boolean(opening.markedTicketId)} style:--rail-width={railWidth === null ? RAIL_DEFAULT_WIDTH : `${railWidth}px`}>
        <section bind:this={railElement} class="board-workspace-left approved-rail" aria-label="Workspace" style:--settled-max-height={`${RAIL_SETTLED_MAX_HEIGHT_SHARE * 100}%`}>
          <div class="rail-work">
            <div class="active-work-scroll" data-active-work-scroll onscroll={() => hover.leave()}>
            <WorkspaceRailCard id="chief" title={workers.data?.chief_of_staff.label ?? "Chief of Staff"} chief ownUnread={workers.data?.chief_of_staff.awaiting_reply ?? false} selected={opening.chiefMarked} selectedTicketId={opening.markedTicketId} groups={rail.chiefGroups} onSelect={() => window.location.hash = workspaceAddress({kind:"chief"}, opening.view)} onTicket={(id) => selectCard(id,null)} onSettle={() => undefined} onHover={(target) => hover.enter({target,title:workers.data?.chief_of_staff.label ?? "Chief of Staff",kicker:"Chief of Staff",groups:rail.chiefHoverGroups})} onWorkerHover={hoverWorkerRow} onLeave={() => hover.leave()} />
            {#each rail.items as item (item.id)}
              <WorkspaceRailCard id={item.id} title={item.title} project={item.project} ownUnread={item.mark === "reply"} selected={opening.markedItemId === item.id} selectedTicketId={opening.markedTicketId} groups={item.groups} busy={busyItem === item.id} onSelect={() => selectItem(item.id)} onTicket={(id) => selectCard(id,item.id)} onSettle={() => void settle(item,true)} onHover={(target) => hoverItem(target,item)} onWorkerHover={hoverWorkerRow} onLeave={() => hover.leave()} />
            {/each}
            {#if settleError}<p role="alert">{settleError}</p>{/if}
            </div>
            <div class="settled" class:settled--open={settledOpen}>
              <button type="button" class="settled-head" aria-expanded={settledOpen} onclick={() => settledOpen = !settledOpen}><span>Settled <span class="count" data-settled-count>({rail.settledItems.length})</span></span><span class="rule"></span><svg class="settled-chevron" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m5 10 3-3 3 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg></button>
              {#if settledOpen}
                <div class="settled-body" data-settled-scroll onscroll={() => hover.leave()}>
                {#each rail.settledItems as item (item.id)}
                  <div class="settled-item" data-settled-item={item.id}>
                  <div class="settled-row">
                    <button class="settled-title" type="button" onclick={() => selectItem(item.id)}><span>{item.title}</span>{#if item.mark === "reply"}<StageMark state="current-awaiting-approval" aria-label="Unread reply" data-workspace-mark="reply" />{/if}</button>
                    <button class="reopen" type="button" disabled={busyItem === item.id} aria-label={`Reopen ${item.title}`} onclick={() => void settle(item,false)}>Reopen</button>
                  </div>
                  <WorkspaceMessages conversations={workspaceChildMessageConversations(messages, item, allCards)} onHover={hoverMessageRow} onLeave={() => hover.leave()} />
                  </div>
                {/each}
                </div>
              {/if}
            </div>
          </div>
          <nav class="rail-utilities" aria-label="Workspace utilities">
            <a href="#/backends" aria-label="Usage and Backends" title="Usage and Backends"><svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M4 15V9m6 6V4m6 11v-8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg></a>
            <a href="#/config" aria-label="Config" title="Config"><svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m8 3-.5 2-1.4.8-2-.5-2 3.4L3.5 10l.1 1.6-1.4 1.4 2 3.4 2-.5 1.4.8.5 2h4l.5-2 1.4-.8 2 .5 2-3.4-1.4-1.4.1-1.6 1.4-1.3-2-3.4-2 .5-1.4-.8-.5-2Z" transform="translate(0 -1)" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" /><circle cx="10" cy="10" r="2.5" stroke="currentColor" stroke-width="1.2" /></svg></a>
          </nav>
        </section>
        <!-- A focusable separator implements the WAI-ARIA window splitter keyboard pattern. -->
        <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
        <div tabindex="0" class="rail-grip" role="separator" aria-label="Resize workspace panel" aria-orientation="vertical" aria-valuemin={RAIL_MIN_WIDTH} aria-valuemax={RAIL_MAX_WIDTH} aria-valuenow={Math.round(measuredRailWidth)} onpointerdown={resizePointer} onpointermove={resizeMove} onpointerup={() => resizing = false} onpointercancel={() => resizing = false} onlostpointercapture={() => resizing = false} onkeydown={resizeKey} ondblclick={() => setWidth(null)} style:left={`${measuredRailWidth}px`}></div>
        <section
          class:board-workspace-right--ticket={Boolean(opening.markedTicketId)}
          class:board-workspace-right--chief={opening.chiefMarked}
          class:board-workspace-right--item={itemPane}
          class="board-workspace-right"
          aria-label={opening.chiefMarked ? "Chief of Staff conversation" : itemPane ? "Orchestrator workspace" : "Workspace inspector"}
        >
          {#if opening.chiefMarked}
            <div class="board-workspace-chief-conversation">
              <ChiefConversation />
            </div>
          {:else if opening.markedTicketId}
            {#key opening.markedTicketId}
              <TicketRoute
                id={opening.markedTicketId}
                openFile={address.openFile}
                onOpenFile={openFileOnTicket}
              />
            {/key}
          {:else if itemPane && opening.openItemId}
            {#key opening.openItemId}
              <SprintItemWorkspace
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

<WorkspaceRailHover visible={hoverVisible} instant={hoverInstant} target={hoverTarget} title={hoverTitle} kicker={hoverKicker} worker={hoverWorker} groups={hoverGroups} cards={allCards} rail={railElement} />
<style>
  .approved-rail-shell { position:relative; isolation:isolate; grid-template-columns:var(--rail-width) minmax(0,1fr); }
  .approved-rail { display:flex; flex-direction:column; height:100%; min-height:0; overflow:hidden; font-size:var(--type-sm); line-height:normal; padding:var(--space-3); }
  .rail-work { display:flex; flex-direction:column; flex:1; min-height:0; }
  .active-work-scroll { flex:1; min-height:0; overflow:auto; scrollbar-gutter:stable; scrollbar-width:thin; }
  .settled-body { min-height:0; overflow:auto; scrollbar-gutter:stable; scrollbar-width:thin; }
  .rail-grip { position:absolute; top:0; bottom:0; width:9px; z-index:20; transform:translateX(-4px); cursor:col-resize; background:transparent; border:0; padding:0; touch-action:none; }
  .rail-grip::after { content:""; position:absolute; inset:0 4px; background:transparent; transition:background var(--motion-fast) var(--motion-ease); }
  .rail-grip:hover::after,.rail-grip:focus-visible::after,.resizing .rail-grip::after { background:var(--accent-bright); }
  .resizing { user-select:none; cursor:col-resize; }
  .settled { display:flex; flex-direction:column; flex:0 1 auto; min-height:0; max-height:var(--settled-max-height); }
  .settled-head { flex:none; display:flex; align-items:center; gap:var(--space-3); padding:var(--space-2) 0; border:0; background:none; width:100%; font:inherit; color:var(--text-faint); cursor:pointer; }
  .settled-head:hover { color:var(--text-strong); }
  .settled-chevron { width:16px; height:16px; flex:none; }
  .settled--open .settled-chevron { transform:rotate(180deg); }
  .rail-utilities { display:flex; flex:none; align-items:center; gap:var(--space-1); padding-top:var(--space-2); border-top:var(--border-hairline) solid var(--border-color); }
  .rail-utilities a { display:flex; align-items:center; justify-content:center; width:32px; height:32px; color:var(--text-faintest); border-radius:var(--radius-sm); }
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

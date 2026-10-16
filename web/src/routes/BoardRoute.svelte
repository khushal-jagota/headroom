<script lang="ts">
  import { onMount } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../lib/queryCatalogue";
  import { mutateJson } from "../lib/mutate";
  import { buildWorkspaceRail, type WorkspaceRailItem, type WorkspaceTicketGroup } from "../lib/workspaceRail";
  import { RAIL_MIN_WIDTH, RAIL_MAX_WIDTH, RAIL_DEFAULT_WIDTH, clampRailWidth, readRailWidth, saveRailWidth, railWidthForKey, createRailHoverController } from "../lib/workspaceRailInteraction";
  import { whatTheAddressOpens, workspaceAddress, type WorkspaceAddress } from "../lib/workspaceAddress";
  import type { ManagedFileTarget } from "../lib/filePreview";
  import type { WorkItemTicketFacts } from "../lib/workItemPresentation";
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
    hover.enter({ target, title:worker.title, kicker:"Worker", worker });
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
        <section bind:this={railElement} class="board-workspace-left approved-rail" aria-label="Workspace" onscroll={() => hover.leave()}>
          <WorkspaceRailCard id="chief" title={workers.data?.chief_of_staff.label ?? "Chief of Staff"} chief ownUnread={workers.data?.chief_of_staff.awaiting_reply ?? false} selected={opening.chiefMarked} selectedTicketId={opening.markedTicketId} groups={rail.chiefGroups} onSelect={() => window.location.hash = workspaceAddress({kind:"chief"}, opening.view)} onTicket={(id) => selectCard(id,null)} onSettle={() => undefined} onHover={(target) => hover.enter({target,title:workers.data?.chief_of_staff.label ?? "Chief of Staff",kicker:"Chief of Staff",groups:rail.chiefHoverGroups})} onWorkerHover={hoverWorkerRow} onLeave={() => hover.leave()} />
          {#each rail.items as item (item.id)}
            <WorkspaceRailCard id={item.id} title={item.title} project={item.project} ownUnread={item.mark === "reply"} selected={opening.markedItemId === item.id} selectedTicketId={opening.markedTicketId} groups={item.groups} busy={busyItem === item.id} onSelect={() => selectItem(item.id)} onTicket={(id) => selectCard(id,item.id)} onSettle={() => void settle(item,true)} onHover={(target) => hoverItem(target,item)} onWorkerHover={hoverWorkerRow} onLeave={() => hover.leave()} />
          {/each}
          <div class="settled">
            <button type="button" class="settled-head" aria-expanded={settledOpen} onclick={() => settledOpen = !settledOpen}><span>Settled</span><span class="rule"></span><span class="count">{rail.settledItems.length}</span></button>
            {#if settledOpen}
              {#each rail.settledItems as item (item.id)}
                <div class="settled-row" data-settled-item={item.id}>
                  <button class="settled-title" type="button" onclick={() => selectItem(item.id)}>{item.title}</button>
                  <button class="reopen" type="button" disabled={busyItem === item.id} aria-label={`Reopen ${item.title}`} onclick={() => void settle(item,false)}>Reopen</button>
                </div>
              {/each}
            {/if}
          </div>
          {#if settleError}<p role="alert">{settleError}</p>{/if}
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
            <a class="board-workspace-back" href={workspaceAddress({ kind: "none" }, opening.view)}>&lsaquo; Workspace</a>
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
  .approved-rail-shell { position:relative; grid-template-columns:var(--rail-width) minmax(0,1fr); }
  .approved-rail { padding:var(--space-3); scrollbar-gutter:stable; scrollbar-width:thin; }
  .rail-grip { position:absolute; top:0; bottom:0; width:9px; z-index:20; transform:translateX(-4px); cursor:col-resize; background:transparent; border:0; padding:0; touch-action:none; }
  .rail-grip::after { content:""; position:absolute; inset:0 4px; background:transparent; transition:background var(--motion-fast) var(--motion-ease); }
  .rail-grip:hover::after,.rail-grip:focus-visible::after,.resizing .rail-grip::after { background:var(--accent-bright); }
  .resizing { user-select:none; cursor:col-resize; }
  .settled { margin-top:var(--space-5); }
  .settled-head { display:flex; align-items:center; gap:var(--space-3); padding:var(--space-2) 0; border:0; background:none; width:100%; font:inherit; color:var(--text-faint); cursor:pointer; }
  .settled-head:hover { color:var(--text-strong); }
  .rule { flex:1; height:var(--border-hairline); background:var(--border-color); }
  .count { flex:none; font-size:var(--type-xs); color:var(--text-faintest); font-variant-numeric:tabular-nums; }
  .settled-row { display:flex; align-items:center; gap:var(--space-2); padding:var(--space-1) var(--space-2); margin:0 calc(var(--space-2) * -1); border-radius:var(--radius-sm); }
  .settled-row:hover { background-image:var(--interaction-hover); }
  .settled-title,.reopen { border:0; background:none; font:inherit; cursor:pointer; color:var(--text-faintest); padding:0; }
  .settled-title { flex:1; text-align:left; min-width:0; }
  .reopen { opacity:0; margin-left:auto; color:var(--text-faint); font-size:12px; }
  .settled-row:hover .reopen,.settled-row:focus-within .reopen { opacity:1; }
  button:focus-visible { outline:var(--border-strong) solid var(--accent-bright); outline-offset:2px; }
  @media(max-width:960px) { .approved-rail-shell { grid-template-columns:1fr; } .rail-grip { display:none; } }
</style>

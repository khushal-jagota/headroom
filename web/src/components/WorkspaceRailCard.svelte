<script lang="ts">
  import { navigationApplication } from "../lib/navigation/application.svelte";
  const navigation=navigationApplication();
  import { navigationScrollWindow } from "../lib/navigation/views/scrollWindow";
  import type { ViewWindow } from "../lib/navigation/contracts";
  import type { ScrollWindowCoordinator } from "../lib/navigation/views/scrollWindowRestoration";
  import type { Snippet } from "svelte";
  import { onDestroy } from "svelte";
  import { createRailPreviewInput } from "../lib/workspaceRailInteraction";
  import type { WorkspaceTicketGroup } from "../lib/workspaceRail";
  import type { WorkItemTicketFacts } from "../lib/workItemPresentation";
  import ConversationSignalMark from "./ConversationSignalMark.svelte";
  import type { ConversationSignal } from "../lib/conversationSignal";
  import WorkerRow from "./WorkerRow.svelte";
  import ManagerEyebrow from "./ManagerEyebrow.svelte";
  import chiefProfile from "../assets/chief-of-staff-profile.webp";
  let { beforeGroups, suppressBackgroundPreview=false, scrollCoordinator=undefined, scrollPublicationId=null, navigationWindow=undefined, onWindow=()=>undefined, onVisibility=()=>undefined, id, title, project = null, chief = false, signal = undefined, conversationId = null, selected, selectedTicketId, groups, busy = false, onSelect, onTicket, onSettle, onHover, onWorkerHover, onLeave }: {
    beforeGroups?: Snippet;
    suppressBackgroundPreview?: boolean;
    scrollCoordinator?:ScrollWindowCoordinator;scrollPublicationId?:number|null;
    navigationWindow?:ViewWindow;onWindow?:(anchor:string,direction:'before'|'after')=>void;onVisibility?:(id:string,visible:boolean)=>void;
    id: string; title: string; project?: string | null; chief?: boolean; signal?: Partial<ConversationSignal>; conversationId?: string | null; selected: boolean;
    selectedTicketId: string | null; groups: WorkspaceTicketGroup[]; busy?: boolean;
    onSelect: () => void; onTicket: (id: string) => void; onSettle: () => void;
    onHover: (target: HTMLElement, immediate?: boolean) => void; onWorkerHover: (target: HTMLElement, ticket: WorkItemTicketFacts, immediate?: boolean) => void; onLeave: () => void;
  } = $props();
  function visibility(node:HTMLElement){const observer=new IntersectionObserver(entries=>onVisibility(id,entries[0].isIntersecting),{root:node.closest('.active-work-scroll'),rootMargin:'200px'});observer.observe(node);return{destroy(){observer.disconnect();onVisibility(id,false);}};}
  const inputs = new Map<string, ReturnType<typeof createRailPreviewInput>>();
  function input(ticket?: WorkItemTicketFacts) {
    const key = ticket?.id ?? "head";
    let controller = inputs.get(key);
    if (!controller) {
      controller = createRailPreviewInput((target, immediate) => { const fresh = ticket && groups.flatMap((group) => group.cards).find((row) => row.id === ticket.id); if (fresh) onWorkerHover(target, fresh, immediate); else if (!ticket) onHover(target, immediate); }, onLeave);
      inputs.set(key, controller);
    }
    return controller;
  }
  $effect(() => {
    const present = new Set(groups.flatMap((group) => group.cards.map((ticket) => ticket.id)));
    for (const [key, controller] of inputs) {
      if (key !== "head" && !present.has(key)) { controller.dispose(); inputs.delete(key); }
    }
  });
  onDestroy(() => { for (const controller of inputs.values()) controller.dispose(); });
</script>
<div class="manager-card-slot">
<section use:visibility use:navigationScrollWindow={{window:navigationWindow,pageRestoration:scrollCoordinator ? {coordinator:scrollCoordinator,priority:'card',publicationId:scrollPublicationId,members:groups.flatMap(group=>group.cards.map(ticket=>ticket.id))} : undefined,activate:()=>navigation?.owner.prioritizeWindow(navigationWindow?.request),selector:'[data-ticket-id]',key:node=>node.dataset.ticketId ?? '',move:onWindow}} role="presentation" data-rail-motion={`card:${id}`} class="manager-card" class:chief class:selected class:has-tickets={groups.length > 0} data-manager={chief ? undefined : id}>
  <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
  <div class="identity" onclick={(event) => { if (!(event.target as HTMLElement).closest('button, a')) { const head = event.currentTarget.querySelector<HTMLButtonElement>('.item-head'); head?.click(); } }} onpointerenter={(event) => { if (!suppressBackgroundPreview) input().pointerEnter(event); }} onpointerleave={(event) => input().leave(event)} onpointerdown={(event) => { if (!(event.target as HTMLElement).closest('button')) input().down(event); }} onpointermove={(event) => input().move(event)} onpointerup={() => input().end()} onpointercancel={() => input().cancel()}>
    {#if !chief}<ManagerEyebrow {project} {title} rail {busy} {onSettle} />{/if}
    <button type="button" class="item-head" data-unread={signal?.message_for_owner === true} onclick={(event) => { if (input().click(event)) onSelect(); }} aria-current={selected ? "page" : undefined} data-chief-destination={chief ? "" : undefined} onpointerenter={(event) => input().pointerEnter(event)} onpointerleave={(event) => input().leave(event)} onpointerdown={(event) => input().down(event)} onpointermove={(event) => input().move(event)} onpointerup={() => input().end()} onpointercancel={() => input().cancel()} onfocus={(event) => input().focus(event)} onblur={(event) => input().leave(event)}>
      {#if chief}<img src={chiefProfile} class="chief-icon" alt="" />{/if}
      <span class="item-title">{title}</span>
      <ConversationSignalMark {signal} {conversationId} />
    </button>
  </div>
  {#if beforeGroups}{@render beforeGroups()}{/if}
  {#each groups as group (group.key)}
    <div class="bucket workspace-ticket-bucket" data-bucket-key={group.key} data-tone={["awaiting_approval", "awaiting_answer", "assigned", "errored"].includes(group.key) ? "attention" : "quiet"}>
      <div class="bucket-label" data-rail-motion={`heading:${id}:${group.key}`}>{group.label}</div>
      {#each group.cards as ticket (ticket.id)}
        <WorkerRow machine={false} locale={ticket.locale} title={ticket.title} signal={ticket.conversation_signal} conversationId={ticket.conversation_id} class="worker-row" active={ticket.id === selectedTicketId} data-ticket-id={ticket.id} data-unread={ticket.conversation_signal?.message_for_owner === true} data-ticket-stage={ticket.stage} data-ticket-status={ticket.ticket_status} aria-current={ticket.id === selectedTicketId ? "page" : undefined} data-rail-motion={`row:${ticket.id}`} onclick={() => { if (input(ticket).click()) onTicket(ticket.id); }} onpointerenter={(event: PointerEvent) => {event.stopPropagation(); input(ticket).pointerEnter(event);}} onpointerleave={(event: PointerEvent) => input(ticket).leave(event)} onpointerdown={(event: PointerEvent) => input(ticket).down(event)} onpointermove={(event: PointerEvent) => input(ticket).move(event)} onpointerup={() => input(ticket).end()} onpointercancel={() => input(ticket).cancel()} onfocus={(event: FocusEvent) => input(ticket).focus(event)} onblur={(event: FocusEvent) => input(ticket).leave(event)} />
      {/each}
    </div>
  {/each}
</section>
</div>
<style>
  .manager-card-slot { min-width:0; }
  :global(.manager-card-slot) + .manager-card-slot { margin-top:var(--space-4); }
  .manager-card { box-sizing:border-box; position:relative; border:0; border-radius:var(--radius-lg); padding:var(--space-2) var(--space-3); isolation:isolate; z-index:1; }
  .manager-card.has-tickets { padding-bottom:var(--space-1); }
  .identity { cursor:pointer; }
  .item-head { display:flex; align-items:center; gap:var(--space-2); margin-top:2px; width:100%; padding:0; border:0; background:none; text-align:left; font:inherit; cursor:pointer; }
  .item-title { flex:1; min-width:0; font-size:var(--type-ui); font-weight:600; line-height:20px; letter-spacing:-0.006em; color:var(--text-default); overflow-wrap:anywhere; }
  .item-head[data-unread="true"] .item-title { color:var(--text-strong); }
  .identity:hover .item-title, .item-head[aria-current="page"] .item-title, .manager-card:has([aria-current="page"]) .item-title { color:var(--text-strong); }
  .chief .item-head { margin-top:0; gap:var(--space-2); }
  .chief .item-title { font-size:var(--type-md); }
  .chief-icon { flex:none; width:22px; height:22px; border-radius:6px; object-fit:cover; }
  /* A Manager's goals and Tickets sit one step in from its title, so the title reads as their parent. */
  .manager-card > .bucket, .manager-card :global(.goals-rail), .manager-card :global(.sole-status) { padding-left:var(--space-3); }
  .bucket :global(.worker-row) { color:var(--text-faint); }
  .bucket[data-bucket-key="awaiting_approval"] :global(.worker-row),
  .bucket[data-bucket-key="awaiting_answer"] :global(.worker-row),
  .bucket[data-bucket-key="errored"] :global(.worker-row),
  .bucket :global(.worker-row[data-unread="true"]) { color:var(--text-default); }
  .bucket :global(.worker-row:is(:hover, .ticket-row--active)) { background:none; color:var(--text-strong); }
  .item-head :global(.stage-mark--current-running),
  .bucket[data-tone="quiet"] :global(.stage-mark--current-running) { border-color:var(--border-color); border-top-color:var(--text-faintest); border-right-color:var(--text-faintest); }
  .bucket :global(.worker-row:focus-visible) { outline:none; }
  :global(.approved-rail .workspace-ticket-bucket .worker-row.rail-moving), :global(.approved-rail .bucket-label.rail-moving), :global(.approved-rail .manager-card.rail-moving) { position:relative; z-index:1; transition:none; background:none; outline:none; }
  :global(.approved-rail .manager-card.rail-moving:where(.selected)) { z-index:2; }
  :global(.approved-rail .rail-travelling) { z-index:2; }
  :global(.approved-rail .manager-card.rail-travelling-card) { z-index:3; }
  :global(.approved-rail .stage-mark.rail-moving) { background:transparent; outline:none; }
  @media (prefers-reduced-motion:reduce) { :global(.approved-rail *) { transition:none!important; animation:none!important; } }
  button:focus-visible { outline:none; }
</style>

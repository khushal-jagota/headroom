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
    <!-- The Chief belongs to no project, so its header is its name alone. -->
    {#if !chief}<ManagerEyebrow {project} {title} rail {busy} {onSettle} />{/if}
    <button type="button" class="item-head" data-unread={signal?.message_for_owner === true} onclick={(event) => { if (input().click(event)) onSelect(); }} aria-current={selected ? "page" : undefined} data-chief-destination={chief ? "" : undefined} onpointerenter={(event) => input().pointerEnter(event)} onpointerleave={(event) => input().leave(event)} onpointerdown={(event) => input().down(event)} onpointermove={(event) => input().move(event)} onpointerup={() => input().end()} onpointercancel={() => input().cancel()} onfocus={(event) => input().focus(event)} onblur={(event) => input().leave(event)}>
      <span class="item-title">{title}</span>
      <ConversationSignalMark {signal} {conversationId} />
    </button>
  </div>
  {#if beforeGroups}{@render beforeGroups()}{/if}
  {#each groups as group (group.key)}
    <!-- Every group is named over its rows in one quiet heading (no count in the rail).
         A heading never outranks its rows: the rows that need the person are the bright ones. -->
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
  /* Blocks are separated by space alone. */
  :global(.manager-card-slot) + .manager-card-slot { margin-top:var(--rail-block-gap); }
  /* Every Manager, the Chief included, is one block with the same parts in the same order:
     its header (the project eyebrow over the name, as one fill), its goals one row each, then
     its Tickets in headed groups. Every fill spans the block, which spans the column less
     --rail-edge on each side; every text stands --rail-pad inside its fill. */
  .manager-card { box-sizing:border-box; position:relative; display:flex; flex-direction:column; padding:0; border:0; isolation:isolate; z-index:1; }
  .identity { box-sizing:border-box; display:flex; flex-direction:column; justify-content:center; min-height:var(--rail-row-height); padding:var(--rail-head-pad) var(--rail-pad); border-radius:var(--radius-md); cursor:pointer; }
  .item-head { display:grid; grid-template-columns:minmax(0,1fr); align-items:center; width:100%; min-height:22px; padding:0; border:0; background:none; text-align:left; font:inherit; cursor:pointer; }
  .item-head:has(> :global(.conversation-signal-marks)) { grid-template-columns:minmax(0,1fr) var(--rail-mark-column); }
  .item-head > :global(.conversation-signal-marks) { grid-column:2; justify-self:end; }
  /* A name is dimmed unless it needs the person (an unread message for them); the pointer or
     the open page on it brings it to full brightness. It keeps one line and ends in an
     ellipsis at the same edge as every row's title. */
  .item-title { grid-column:1; min-width:0; overflow:hidden; white-space:nowrap; text-overflow:clip; font-size:var(--type-rail-title); font-weight:var(--weight-title); line-height:22px; letter-spacing:-0.012em; color:var(--rail-tone-title); transition:color var(--motion-fast) var(--motion-ease); }
  .item-head[data-unread="true"] .item-title { color:var(--rail-tone-title-bright); }
  .identity:hover .item-title, .item-head[aria-current="page"] .item-title { color:var(--rail-tone-title-bright); }
  /* The Manager you are inside: while its page or one of its goals or Tickets is open, its
     header (the eyebrow and the name together) goes to full brightness. A header takes a fill
     only as a row does: 14% when it is the open page, 5% under the pointer. A name that needs
     you is bright for one reason only, an unread message for you, and so always carries the
     accent dot; the header of the place you are in never does, and its eyebrow lifts with it. */
  .manager-card:has(:global([aria-current="page"])) .item-title,
  .manager-card:has(:global([aria-current="page"])) .identity :global(.manager-eyebrow > span) { color:var(--rail-tone-title-bright); }
  .item-head :global(.stage-mark--current-running),
  .bucket[data-tone="quiet"] :global(.stage-mark--current-running) { border-color:var(--border-color); border-top-color:var(--tone-tertiary); border-right-color:var(--tone-tertiary); }
  .bucket :global(.worker-row:focus-visible) { outline:none; }
  :global(.approved-rail .workspace-ticket-bucket .worker-row.rail-moving), :global(.approved-rail .bucket-label.rail-moving), :global(.approved-rail .manager-card.rail-moving) { position:relative; z-index:1; transition:none; background:none; outline:none; }
  :global(.approved-rail .manager-card.rail-moving:where(.selected)) { z-index:2; }
  :global(.approved-rail .rail-travelling) { z-index:2; }
  :global(.approved-rail .manager-card.rail-travelling-card) { z-index:3; }
  :global(.approved-rail .stage-mark.rail-moving) { background:transparent; outline:none; }
  @media (prefers-reduced-motion:reduce) { :global(.approved-rail *) { transition:none!important; animation:none!important; } }
  button:focus-visible { outline:none; }
</style>

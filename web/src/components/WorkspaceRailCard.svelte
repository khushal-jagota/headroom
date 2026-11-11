<script lang="ts">
  import { railFocus, focusVisibleGroups, focusSlideMs } from "../lib/railFocus.svelte";
  import { slide } from "svelte/transition";
  import { cubicOut } from "svelte/easing";
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
  import type {GoalResponse} from "../lib/goals/contracts";
  import {worstGoal} from "../lib/goals/railPresentation";
  import {workspaceAddress} from "../lib/workspaceAddress";
  import ManagerEyebrow from "./ManagerEyebrow.svelte";
  let { goals = [], showChiefEyebrow = true, onGoalNavigate, beforeGroups, suppressBackgroundPreview=false, scrollCoordinator=undefined, scrollPublicationId=null, navigationWindow=undefined, onWindow=()=>undefined, onVisibility=()=>undefined, id, title, project = null, chief = false, signal = undefined, conversationId = null, selected, selectedTicketId, groups, busy = false, onSelect, onTicket, onSettle, onHover, onWorkerHover, onLeave }: {
    beforeGroups?: Snippet; goals?: GoalResponse[]; showChiefEyebrow?: boolean; onGoalNavigate?: () => void;
    suppressBackgroundPreview?: boolean;
    scrollCoordinator?:ScrollWindowCoordinator;scrollPublicationId?:number|null;
    navigationWindow?:ViewWindow;onWindow?:(anchor:string,direction:'before'|'after')=>void;onVisibility?:(id:string,visible:boolean)=>void;
    id: string; title: string; project?: string | null; chief?: boolean; signal?: Partial<ConversationSignal>; conversationId?: string | null; selected: boolean;
    selectedTicketId: string | null; groups: WorkspaceTicketGroup[]; busy?: boolean;
    onSelect: () => void; onTicket: (id: string) => void; onSettle: () => void;
    onHover: (target: HTMLElement, immediate?: boolean, focusTarget?: HTMLElement) => void; onWorkerHover: (target: HTMLElement, ticket: WorkItemTicketFacts, immediate?: boolean) => void; onLeave: () => void;
  } = $props();
  let tagGoal = $derived(worstGoal(goals));
  // Hidden rails have no measurable height. Svelte's slide reads "auto" as NaN there.
  function groupSlide(node: HTMLElement) {
    if (!node.getClientRects().length || !Number.isFinite(parseFloat(getComputedStyle(node).height))) return { duration: 0 };
    return slide(node, { duration: focusSlideMs(), easing: cubicOut });
  }
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
<section use:visibility use:navigationScrollWindow={{window:navigationWindow,pageRestoration:scrollCoordinator ? {coordinator:scrollCoordinator,priority:'card',publicationId:scrollPublicationId,members:groups.flatMap(group=>group.cards.map(ticket=>ticket.id))} : undefined,activate:()=>navigation?.owner.prioritizeWindow(navigationWindow?.request),selector:'[data-ticket-id]',key:node=>node.dataset.ticketId ?? '',move:onWindow}} role="presentation" data-rail-motion={`card:${id}`} class="manager-card" data-goal-status={tagGoal?.status} class:chief class:selected class:has-tickets={groups.length > 0} data-manager={chief ? undefined : id}>
  <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
  <div class="identity" onclick={(event) => { if (!(event.target as HTMLElement).closest('button, a')) { const head = event.currentTarget.querySelector<HTMLButtonElement>('.item-head'); head?.click(); } }} onpointerenter={(event) => { if (!suppressBackgroundPreview) input().pointerEnter(event); }} onpointerleave={(event) => input().leave(event)} onpointerdown={(event) => { if (!(event.target as HTMLElement).closest('button')) input().down(event); }} onpointermove={(event) => input().move(event)} onpointerup={() => input().end()} onpointercancel={() => input().cancel()}>
    {#if !chief || showChiefEyebrow}<ManagerEyebrow {project} {title} {chief} rail {busy} {onSettle} goal={tagGoal} onGoalFocus={(event)=>onHover((event.currentTarget as HTMLElement).closest('.identity') as HTMLElement,true,event.currentTarget as HTMLElement)} onGoalBlur={(event)=>{if(!(event.relatedTarget instanceof Node)||!(event.currentTarget as HTMLElement).closest('.identity')?.contains(event.relatedTarget))onLeave();}} onGoal={()=>{if(tagGoal){onGoalNavigate?.();window.location.hash=workspaceAddress({kind:'goal',id:tagGoal.id});}}}/>{/if}
    <button type="button" class="item-head" data-unread={signal?.message_for_owner === true} onclick={(event) => { if (input().click(event)) onSelect(); }} aria-current={selected ? "page" : undefined} data-chief-destination={chief ? "" : undefined} onpointerenter={(event) => input().pointerEnter(event)} onpointerleave={(event) => {if (!(event.relatedTarget instanceof Node) || !event.currentTarget.closest(".identity")?.contains(event.relatedTarget)) input().leave(event);}} onpointerdown={(event) => input().down(event)} onpointermove={(event) => input().move(event)} onpointerup={() => input().end()} onpointercancel={() => input().cancel()} onfocus={(event) => input().focus(event)} onblur={(event) => input().leave(event)}>
      <span class="item-title">{title}</span>
      <ConversationSignalMark {signal} {conversationId} />
    </button>
  </div>
  {#if beforeGroups}{@render beforeGroups()}{/if}
  {#each focusVisibleGroups(groups, railFocus.on) as group (group.key)}
    <!-- Every group is named over its rows in one quiet heading (no count in the rail).
         A heading never outranks its rows: the rows that need the person are the bright ones. -->
    <div class="bucket workspace-ticket-bucket" transition:groupSlide data-bucket-key={group.key} data-tone={["awaiting_approval", "awaiting_answer", "assigned", "errored"].includes(group.key) ? "attention" : "quiet"}>
      <div class="bucket-label" data-rail-motion={`heading:${id}:${group.key}`}>{group.label}</div>
      {#each group.cards as ticket (ticket.id)}
        <WorkerRow machine={false} agentHold={ticket} approval={ticket.awaiting_approval ? "owner" : ticket.awaiting_agent_approval ? "agent" : null} locale={ticket.locale} title={ticket.title} signal={ticket.conversation_signal} conversationId={ticket.conversation_id} class="worker-row" active={ticket.id === selectedTicketId} data-ticket-id={ticket.id} data-unread={ticket.conversation_signal?.message_for_owner === true} data-ticket-stage={ticket.stage} data-ticket-status={ticket.ticket_status} aria-current={ticket.id === selectedTicketId ? "page" : undefined} data-rail-motion={`row:${ticket.id}`} onclick={() => { if (input(ticket).click()) onTicket(ticket.id); }} onpointerenter={(event: PointerEvent) => {event.stopPropagation(); input(ticket).pointerEnter(event);}} onpointerleave={(event: PointerEvent) => input(ticket).leave(event)} onpointerdown={(event: PointerEvent) => input(ticket).down(event)} onpointermove={(event: PointerEvent) => input(ticket).move(event)} onpointerup={() => input(ticket).end()} onpointercancel={() => input(ticket).cancel()} onfocus={(event: FocusEvent) => input(ticket).focus(event)} onblur={(event: FocusEvent) => input(ticket).leave(event)} />
      {/each}
    </div>
  {/each}
</section>
</div>
<style>
  /* Each Manager is a bubble: the old sidebar colour, 12px corners, 8px from the next. The fill
     is drawn under the rail's selection and hover fills (which live in the list's own layer),
     so they show on it. The card owns both layers so its motion carries the fill. */
  .manager-card-slot { min-width:0; }
  .manager-card::before { content:""; position:absolute; inset:0; z-index:-2; border-radius:var(--rail-bubble-radius); background:var(--rail-bubble-fill); pointer-events:none; }
  /* Only the status haze leaves during header interaction or selection. The bubble stays
     below it, and the rail's existing fills stay above it. */
  .manager-card[data-goal-status]::after { content:""; position:absolute; inset:0; z-index:-1; border-radius:var(--rail-bubble-radius); pointer-events:none; opacity:1; transition:opacity var(--motion-base) var(--motion-ease); }
  .manager-card[data-goal-status=bad]::after { background:linear-gradient(color-mix(in srgb,var(--accent-error) 9%,transparent),transparent 70px); }
  .manager-card[data-goal-status=attention]::after { background:linear-gradient(color-mix(in srgb,var(--accent-attention) 9%,transparent),transparent 70px); }
  .manager-card[data-goal-status=good]::after { background:linear-gradient(color-mix(in srgb,var(--accent-done) 7%,transparent),transparent 70px); }
  .manager-card:is(:has(.identity:hover, .identity:focus-within), .selected)::after { opacity:0; }
  :global(.manager-card-slot) + .manager-card-slot { margin-top:var(--rail-block-gap); }
  /* Every Manager, the Chief included, is one block with the same parts in the same order:
     its header (the project eyebrow directly over the name, as one fill), its goals, then its
     Tickets in headed groups. Every fill spans the block, which spans the column less
     --rail-edge on each side; every text stands --rail-pad inside its fill. */
  /* The bubble holds its words the same distance from all four edges (about 14px of clear space,
     owner: the last rows sat 8px from the bottom while the sides and top had 14): the bottom
     keeps 6px more than the fills' 4px inset, and a header with a project eyebrow 3px more on
     top, where the eyebrow's small line sits higher than the Chief's name. */
  .manager-card { box-sizing:border-box; position:relative; display:flex; flex-direction:column; padding:var(--rail-bubble-pad) var(--rail-bubble-pad) calc(var(--rail-bubble-pad) + 6px); border:0; isolation:isolate; z-index:1; }
  /* A header with nothing under it (the Chief with no goals or Tickets) ends at the same 4px as it begins. */
  .manager-card:not(.has-tickets):not(:has(:global(.goals-rail))) { padding-bottom:var(--rail-bubble-pad); }
  .identity, .bucket, :global(.manager-card > .goals-rail) { position:relative; z-index:1; }
  .identity { box-sizing:border-box; display:flex; flex-direction:column; padding:var(--rail-head-pad) var(--rail-pad); border-radius:var(--radius-md); cursor:pointer; }
  /* The eyebrow's small line sits higher than a name, so its 3px of room is inside the fill: the fill keeps 4px from the card's top and the words stay where they were. */
  .identity:has(:global(.manager-eyebrow)) { padding-top:calc(var(--rail-head-pad) + 3px); }
  /* The Chief's name alone: the 2px grid nudge puts capitals 11px under the fill's top and the baseline 7px over its bottom. With no nudge both are 9px. */
  .manager-card.chief .identity:not(:has(:global(.manager-eyebrow))) .item-head { top:0; }
  .item-head { position:relative; top:var(--rail-title-nudge); display:grid; grid-template-columns:minmax(0,1fr) var(--rail-mark-column); align-items:start; width:100%; min-height:var(--rail-title-line); padding:0; border:0; background:none; text-align:left; font:inherit; cursor:pointer; }
  .item-head > :global(.conversation-signal-marks) { grid-column:2; justify-self:end; align-self:start; height:var(--rail-title-line); }
  /* The name leads the block by size and weight (18/600 on a 24px line) and wraps in full.
     It rests dim unless it needs the person (an unread message for them); the pointer on its
     header or its own page open brings it to full brightness. */
  .item-title { grid-column:1; min-width:0; overflow-wrap:anywhere; font-size:var(--type-rail-title); font-weight:var(--weight-title); line-height:var(--rail-title-line); letter-spacing:-0.014em; color:var(--rail-tone-title); transition:color var(--motion-fast) var(--motion-ease); }
  .item-head[data-unread="true"] .item-title { color:var(--rail-tone-title-bright); }
  .identity:hover .item-title, .item-head[aria-current="page"] .item-title { color:var(--rail-tone-title-bright); }
  /* A Manager's name is bright under staging's condition alone: an unread message for you, the
     pointer on its header, or its own page open. Being inside it (one of its items open) has
     no mark of its own: only the open item is lit (owner). */
  .item-head :global(.stage-mark--current-running),
  .bucket[data-tone="quiet"] :global(.stage-mark--current-running) { border-color:var(--border-color); border-top-color:var(--tone-tertiary); border-right-color:var(--tone-tertiary); }
  .bucket :global(.worker-row:focus-visible) { outline:none; }
  :global(.approved-rail .workspace-ticket-bucket .worker-row.rail-moving), :global(.approved-rail .bucket-label.rail-moving), :global(.approved-rail .manager-card.rail-moving) { position:relative; z-index:1; transition:none; background:none; outline:none; }
  :global(.approved-rail .manager-card.rail-moving:where(.selected)) { z-index:2; }
  :global(.approved-rail .rail-travelling) { z-index:2; }
  :global(.approved-rail .manager-card.rail-travelling-card) { z-index:3; }
  /* A fixed goal preview stays inside this card's isolated layer. Raise its card above
     the dock only while the preview is open, so native taps reach its controls. */
  .manager-card:has(:global([data-goal-preview])) { z-index:5; }
  :global(.approved-rail .stage-mark.rail-moving) { background:transparent; outline:none; }
  @media (prefers-reduced-motion:reduce) { :global(.approved-rail *) { transition:none!important; animation:none!important; } .manager-card[data-goal-status]::after { transition:none; } }
  button:focus-visible { outline:none; }
</style>

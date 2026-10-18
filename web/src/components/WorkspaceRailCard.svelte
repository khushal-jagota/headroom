<script lang="ts">
  import { onDestroy } from "svelte";
  import { createRailPreviewInput } from "../lib/workspaceRailInteraction";
  import type { WorkspaceTicketGroup } from "../lib/workspaceRail";
  import type { WorkItemTicketFacts } from "../lib/workItemPresentation";
  import ConversationSignalMark from "./ConversationSignalMark.svelte";
  import type { ConversationSignal } from "../lib/conversationSignal";
  import SprintTicketRow from "./SprintTicketRow.svelte";
  import OrchestratorEyebrow from "./OrchestratorEyebrow.svelte";
  import chiefProfile from "../assets/chief-of-staff-profile.webp";
  let { id, title, project = null, chief = false, signal = undefined, conversationId = null, selected, selectedTicketId, groups, busy = false, onSelect, onTicket, onSettle, onHover, onWorkerHover, onLeave }: {
    id: string; title: string; project?: string | null; chief?: boolean; signal?: ConversationSignal; conversationId?: string | null; selected: boolean;
    selectedTicketId: string | null; groups: WorkspaceTicketGroup[]; busy?: boolean;
    onSelect: () => void; onTicket: (id: string) => void; onSettle: () => void;
    onHover: (target: HTMLElement, immediate?: boolean) => void; onWorkerHover: (target: HTMLElement, ticket: WorkItemTicketFacts, immediate?: boolean) => void; onLeave: () => void;
  } = $props();
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
<section role="presentation" onclick={(event) => { if (!(event.target as HTMLElement).closest("button")) onSelect(); }} data-rail-motion={`card:${id}`} class="orchestrator-card" class:chief class:selected onpointerenter={(event) => input().pointerEnter(event)} onpointerleave={onLeave} data-sprint-item={chief ? undefined : id}>
  <div class="identity">
    {#if !chief}<OrchestratorEyebrow {project} {title} rail {busy} {onSettle} />{/if}
    <button type="button" class="item-head" onclick={(event) => { if (input().click(event)) onSelect(); }} aria-current={selected ? "page" : undefined} data-chief-destination={chief ? "" : undefined} onpointerenter={(event) => input().pointerEnter(event)} onpointerleave={onLeave} onpointerdown={(event) => input().down(event)} onpointermove={(event) => input().move(event)} onpointerup={() => input().end()} onpointercancel={() => input().cancel()} onfocus={(event) => input().focus(event)} onblur={onLeave}>
      {#if chief}<img src={chiefProfile} class="chief-icon" alt="" />{/if}
      <span class="item-title">{title}</span>
      <ConversationSignalMark {signal} {conversationId} />
    </button>
  </div>
  {#if !groups.length}<div class="board-workspace-quiet-line">Nothing in this list</div>{/if}
  {#each groups as group (group.key)}
    <div class="bucket workspace-ticket-bucket" data-bucket-key={group.key} data-tone={["awaiting_approval", "awaiting_answer", "assigned", "errored"].includes(group.key) ? "attention" : "quiet"}>
      <div class="bucket-label" data-rail-motion={`heading:${id}:${group.key}`}>{group.label}</div>
      {#each group.cards as ticket (ticket.id)}
        <SprintTicketRow title={ticket.title} signal={ticket.conversation_signal} conversationId={ticket.conversation_id} class="worker-row" active={ticket.id === selectedTicketId} data-ticket-id={ticket.id} data-ticket-stage={ticket.stage} data-ticket-status={ticket.ticket_status} aria-current={ticket.id === selectedTicketId ? "page" : undefined} data-rail-motion={`row:${ticket.id}`} onclick={() => { if (input(ticket).click()) onTicket(ticket.id); }} onpointerenter={(event: PointerEvent) => {event.stopPropagation(); input(ticket).pointerEnter(event);}} onpointerleave={onLeave} onpointerdown={(event: PointerEvent) => input(ticket).down(event)} onpointermove={(event: PointerEvent) => input(ticket).move(event)} onpointerup={() => input(ticket).end()} onpointercancel={() => input(ticket).cancel()} onfocus={(event: FocusEvent) => input(ticket).focus(event)} onblur={onLeave} />
      {/each}
    </div>
  {/each}
</section>
<style>
  .orchestrator-card { position:relative; border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-md); padding:11px var(--space-3) var(--space-3); transition:border-color var(--motion-fast) var(--motion-ease); }
  :global(.orchestrator-card) + .orchestrator-card { margin-top:var(--space-3); }
  .orchestrator-card:hover, .orchestrator-card:focus-within { border-color:rgb(from var(--border-color) r g b / .26); }
  .orchestrator-card:hover .item-title { color:var(--accent-needs-me); }
  /* Selection uses the existing surface; pointer exit must not reveal an accent edge. */
  .selected { background:var(--surface-2); }
  .item-head { display:flex; align-items:flex-start; gap:var(--space-2); margin-top:var(--space-1); width:100%; padding:0; border:0; background:none; text-align:left; font:inherit; cursor:pointer; }
  .item-title { flex:1; min-width:0; font-size:16px; font-weight:600; line-height:1.35; letter-spacing:-0.002em; color:var(--text-strong); overflow-wrap:anywhere; }
  .chief .item-head { align-items:center; margin-top:0; gap:10px; }
  .chief .item-title { font-size:18px; }
  .chief-icon { flex:none; width:26px; height:26px; border-radius:var(--radius-sm); object-fit:cover; }
  .bucket :global(.worker-row) { color:var(--text-faintest); }
  .bucket[data-tone="attention"] :global(.worker-row) { color:var(--text-strong); }
  .bucket :global(.worker-row.ticket-row--active) { background:var(--surface-2); }
  .bucket[data-tone="quiet"] :global(.stage-mark--current-running) { border-color:var(--border-color); border-top-color:var(--text-faintest); border-right-color:var(--text-faintest); }
  .bucket :global(.worker-row:focus-visible) { outline:var(--border-strong) solid var(--accent-bright); outline-offset:1px; }
  :global(.approved-rail .workspace-ticket-bucket .worker-row.rail-moving), :global(.approved-rail .bucket-label.rail-moving), :global(.approved-rail .orchestrator-card.rail-moving) { position:relative; z-index:1; transition:none; background:var(--surface-1); outline:4px solid var(--surface-1); }
  :global(.approved-rail .rail-travelling) { z-index:2; }
  :global(.approved-rail .orchestrator-card.rail-travelling-card) { z-index:3; }
  :global(.approved-rail .stage-mark.rail-moving) { background:transparent; outline:none; }
  @media (prefers-reduced-motion:reduce) { :global(.approved-rail *) { transition:none!important; animation:none!important; } }
  button:focus-visible { outline:var(--border-strong) solid var(--accent-bright); outline-offset:1px; }
</style>

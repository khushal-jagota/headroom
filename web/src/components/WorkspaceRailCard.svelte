<script lang="ts">
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
    onHover: (target: HTMLElement) => void; onWorkerHover: (target: HTMLElement, ticket: WorkItemTicketFacts) => void; onLeave: () => void;
  } = $props();
</script>
<section role="presentation" onclick={(event) => { if (!(event.target as HTMLElement).closest("button")) onSelect(); }} class="orchestrator-card" class:chief class:selected data-sprint-item={chief ? undefined : id} onmouseenter={(event) => onHover(event.currentTarget)} onmouseleave={onLeave}>
  <div class="identity" role="presentation" onmouseenter={(event) => onHover(event.currentTarget.closest(".orchestrator-card") as HTMLElement)}>
    {#if !chief}<OrchestratorEyebrow {project} {title} rail {busy} {onSettle} />{/if}
    <button type="button" class="item-head" onclick={onSelect} aria-current={selected ? "page" : undefined} data-chief-destination={chief ? "" : undefined} onfocus={(event) => onHover(event.currentTarget)} onblur={onLeave}>
      {#if chief}<img src={chiefProfile} class="chief-icon" alt="" />{/if}
      <span class="item-title">{title}</span>
      <ConversationSignalMark {signal} {conversationId} />
    </button>
  </div>
  {#each groups as group (group.key)}
    <div class="bucket workspace-ticket-bucket" data-bucket-key={group.key}>
      <div class="bucket-label">{group.label}</div>
      {#each group.cards as ticket (ticket.id)}
        <SprintTicketRow priority={null} title={ticket.title} signal={ticket.conversation_signal} conversationId={ticket.conversation_id} class="worker-row" active={ticket.id === selectedTicketId} data-ticket-id={ticket.id} data-ticket-stage={ticket.stage} data-ticket-status={ticket.ticket_status} aria-current={ticket.id === selectedTicketId ? "page" : undefined} onclick={() => onTicket(ticket.id)} onmouseenter={(event: MouseEvent) => {event.stopPropagation(); onWorkerHover(event.currentTarget as HTMLElement, ticket);}} onmouseleave={onLeave} onfocus={(event: FocusEvent) => onWorkerHover(event.currentTarget as HTMLElement, ticket)} onblur={onLeave} />
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
  .bucket :global(.worker-row.ticket-row--active) { background:var(--surface-recessed); color:var(--text-strong); }
  button:focus-visible { outline:var(--border-strong) solid var(--accent-bright); outline-offset:1px; }
</style>

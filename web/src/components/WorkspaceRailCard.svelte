<script lang="ts">
  import type { WorkspaceTicketGroup } from "../lib/workspaceRail";
  import { workspaceTicketRowMark, workspaceRowMarkPresentation } from "../lib/workspaceRail";
  import type { WorkItemTicketFacts } from "../lib/workItemPresentation";
  import StageMark from "./StageMark.svelte";
  import SprintTicketRow from "./SprintTicketRow.svelte";
  import OrchestratorEyebrow from "./OrchestratorEyebrow.svelte";
  import chiefProfile from "../assets/chief-of-staff-profile.webp";
  let { id, title, project = null, chief = false, ownUnread = false, selected, selectedTicketId, groups, busy = false, onSelect, onTicket, onSettle, onHover, onWorkerHover, onLeave }: {
    id: string; title: string; project?: string | null; chief?: boolean; ownUnread?: boolean; selected: boolean;
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
      {#if ownUnread}<StageMark state="current-awaiting-approval" aria-label="Unread reply" data-workspace-mark="reply" />{/if}
    </button>
  </div>
  {#each groups as group (group.key)}
    <div class="bucket" data-bucket-key={group.key}>
      <div class="bucket-label">{group.label}</div>
      {#each group.cards as ticket (ticket.id)}
        {@const mark = workspaceTicketRowMark(ticket)}
        {@const presentation = workspaceRowMarkPresentation(mark)}
        <SprintTicketRow priority={null} title={ticket.title} state={presentation.state} ariaLabel={presentation.ariaLabel} class="worker-row" active={ticket.id === selectedTicketId} data-ticket-id={ticket.id} data-ticket-stage={ticket.stage} data-ticket-status={ticket.ticket_status} data-workspace-mark={mark ?? "none"} aria-current={ticket.id === selectedTicketId ? "page" : undefined} onclick={() => onTicket(ticket.id)} onmouseenter={(event: MouseEvent) => {event.stopPropagation(); onWorkerHover(event.currentTarget as HTMLElement, ticket);}} onmouseleave={onLeave} onfocus={(event: FocusEvent) => onWorkerHover(event.currentTarget as HTMLElement, ticket)} onblur={onLeave} stageMarkAttributes={{ "data-workspace-mark": mark ?? "none" }} />
      {/each}
    </div>
  {/each}
</section>
<style>
  .orchestrator-card { position:relative; border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-md); padding:11px var(--space-3) var(--space-3); transition:border-color var(--motion-fast) var(--motion-ease); }
  :global(.orchestrator-card) + .orchestrator-card { margin-top:var(--space-3); }
  .orchestrator-card:hover, .orchestrator-card:focus-within { border-color:rgb(from var(--border-color) r g b / .26); }
  .orchestrator-card:hover .item-title { color:var(--accent-needs-me); }
  .selected { border-color:var(--accent-bright); }
  .item-head { display:flex; align-items:flex-start; gap:var(--space-2); margin-top:var(--space-1); width:100%; padding:0; border:0; background:none; text-align:left; font:inherit; cursor:pointer; }
  .item-title { flex:1; min-width:0; font-size:16px; font-weight:600; line-height:1.35; letter-spacing:-0.002em; color:var(--text-strong); overflow-wrap:anywhere; }
  .chief .item-head { align-items:center; margin-top:0; gap:10px; }
  .chief .item-title { font-size:18px; }
  .chief-icon { flex:none; width:26px; height:26px; border-radius:var(--radius-sm); object-fit:cover; }
  .bucket { margin-top:var(--space-4); }
  .bucket-label { font-size:10.5px; line-height:normal; font-weight:600; letter-spacing:0.08em; text-transform:uppercase; color:var(--text-faint); padding-bottom:6px; margin-bottom:var(--space-1); border-bottom:var(--border-hairline) solid var(--border-color); }
  .bucket :global(.worker-row) { width:100%; display:grid; grid-template-columns:minmax(0,1fr) 14px; align-items:center; gap:var(--space-2); padding:var(--space-1) var(--space-2); margin:0 calc(var(--space-2) * -1); border:0; border-radius:var(--radius-sm); background:transparent; text-align:left; color:var(--text-muted); font:inherit; font-size:var(--type-sm); line-height:1.5; cursor:pointer; transition:color var(--motion-fast) var(--motion-ease),background var(--motion-fast) var(--motion-ease); }
  .bucket :global(.worker-row:hover) { background-image:var(--interaction-hover); color:var(--text-strong); }
  .bucket :global(.worker-row.ticket-row--active) { background:var(--surface-recessed); color:var(--text-strong); }
  button:focus-visible,.bucket :global(.worker-row:focus-visible) { outline:var(--border-strong) solid var(--accent-bright); outline-offset:1px; }
  .bucket :global(.ticket-row-title) { flex:1; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .bucket :global(.worker-row .stage-mark) { justify-self:end; margin-left:0; margin-right:0; }
</style>

<script lang="ts">
  import type { OrchestratorPrincipal, OrchestratorChildFilter } from "../lib/types";
  import type { WorkItemTicketFacts } from "../lib/workItemPresentation";
  import OrchestratorChildrenSection from "./OrchestratorChildrenSection.svelte";
  let { principal, tickets, publication, navigationErrorHandledByParent = false, onHover = undefined, onLeave = () => undefined }: {
    principal: OrchestratorPrincipal;
    tickets?: readonly (WorkItemTicketFacts & {is_backlogged:boolean})[];
    publication?: import("../lib/navigation/collection").NavigationPublication;
    navigationErrorHandledByParent?: boolean;
    onHover?: (element: HTMLElement, ticketId: string, immediate?: boolean) => void;
    onLeave?: () => void;
  } = $props();
  const sections: readonly OrchestratorChildFilter[] = ["active", "backlogged", "done"];
  let scope = $derived(`${principal.kind}:${principal.id}`);
</script>

<section class="orchestrator-children" aria-label="Workers" data-orchestrator-children={scope}>
  {#key scope}
    {#each sections as filter}
      <OrchestratorChildrenSection {principal} {tickets} {publication} {navigationErrorHandledByParent} {filter} {onHover} {onLeave} />
    {/each}
  {/key}
</section>
<style>
  .orchestrator-children :global(.sprint-workspace-status + .sprint-workspace-status) { margin-top:0; }
  .orchestrator-children :global(.sprint-workspace-status > summary) { display:flex; align-items:center; gap:var(--space-3); padding:7px var(--space-3); margin:0; font-family:var(--font-ui); line-height:1.6; letter-spacing:normal; border:0; border-radius:var(--radius-md); background:var(--surface-2); font-size:13px; font-weight:600; color:var(--text-faint); list-style:none; }
  .orchestrator-children :global(.sprint-workspace-status > summary:hover) { background-image:var(--interaction-hover); color:var(--text-strong); }
  .orchestrator-children :global(.sprint-workspace-status-label) { font-size:13px; text-transform:none; letter-spacing:normal; color:inherit; }
  .orchestrator-children :global(.sprint-workspace-status[open] > summary > .sprint-workspace-count) { display:block; }
  .orchestrator-children :global(.sprint-workspace-count) { margin-left:auto; font-size:13px; font-weight:400; color:var(--text-faintest); }
  .orchestrator-children :global(.sprint-workspace-status-body) { padding:var(--space-1) 0; }
  .orchestrator-children :global(.stage-mark) { width:7px; height:7px; }
  .orchestrator-children :global(a:focus-visible), .orchestrator-children :global(button:focus-visible), .orchestrator-children :global(summary:focus-visible) { outline:var(--border-strong) solid var(--accent-bright); outline-offset:2px; }
  .orchestrator-children :global(.ticket-row) { width:100%; margin:0; padding:7px var(--space-3); gap:var(--space-3); border-radius:var(--radius-md); font-size:15px; line-height:1.6; align-items:center; color:var(--text-muted); }
  .orchestrator-children :global(.ticket-row:hover), .orchestrator-children :global(.ticket-row:focus-visible) { color:var(--text-strong); }
  .orchestrator-children :global(.ticket-row-title) { flex:1; min-width:0; display:block; overflow:visible; white-space:normal; overflow-wrap:anywhere; line-height:inherit; line-clamp:unset; -webkit-line-clamp:unset; -webkit-box-orient:initial; }
  .orchestrator-children :global(.child-pages) { display:flex; align-items:center; gap:var(--space-3); color:var(--text-faint); font-family:var(--font-ui); font-size:var(--type-sm); }
  .orchestrator-children :global(.child-pages button) { color:var(--text-default); background:var(--surface-2); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-sm); padding:var(--space-1) var(--space-2); font:inherit; }
  .orchestrator-children :global(.child-pages) { margin-top:var(--space-3); }
</style>

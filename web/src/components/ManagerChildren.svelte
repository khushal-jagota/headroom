<script lang="ts">
  import type { ManagerPrincipal, ManagerChildFilter } from "../lib/types";
  import type { WorkItemTicketFacts } from "../lib/workItemPresentation";
  import ManagerChildrenSection from "./ManagerChildrenSection.svelte";
  let { principal, tickets, publication, onHover = undefined, onLeave = () => undefined }: {
    principal: ManagerPrincipal;
    tickets?: readonly (WorkItemTicketFacts & {is_backlogged:boolean})[];
    publication?: import("../lib/navigation/collection").NavigationPublication;
    onHover?: (element: HTMLElement, ticketId: string, immediate?: boolean) => void;
    onLeave?: () => void;
  } = $props();
  const sections: readonly ManagerChildFilter[] = ["active", "backlogged", "done"];
  let scope = $derived(`${principal.kind}:${principal.id}`);
</script>

<section class="manager-children" aria-label="Workers" data-manager-children={scope}>
  {#key scope}
    {#each sections as filter}
      <ManagerChildrenSection {principal} {tickets} {publication} {filter} {onHover} {onLeave} />
    {/each}
  {/key}
</section>
<style>
  .manager-children :global(.manager-workspace-status + .manager-workspace-status) { margin-top:0; }
  .manager-children :global(.manager-workspace-status > summary) { display:flex; align-items:center; gap:var(--space-3); padding:7px var(--space-3); margin:0; font-family:var(--font-ui); line-height:1.6; letter-spacing:normal; border:0; border-radius:var(--radius-md); background:var(--surface-2); font-size:13px; font-weight:600; color:var(--text-faint); list-style:none; }
  .manager-children :global(.manager-workspace-status > summary:hover) { background-image:var(--interaction-hover); color:var(--text-strong); }
  .manager-children :global(.manager-workspace-status-label) { font-size:13px; text-transform:none; letter-spacing:normal; color:inherit; }
  .manager-children :global(.manager-workspace-status[open] > summary > .manager-workspace-count) { display:block; }
  .manager-children :global(.manager-workspace-count) { margin-left:auto; font-size:13px; font-weight:400; color:var(--text-faintest); }
  .manager-children :global(.manager-workspace-status-body) { padding:var(--space-1) 0; }
  .manager-children :global(.stage-mark) { width:7px; height:7px; }
  .manager-children :global(a:focus-visible), .manager-children :global(button:focus-visible), .manager-children :global(summary:focus-visible) { outline:var(--border-strong) solid var(--accent-bright); outline-offset:2px; }
  .manager-children :global(.ticket-row) { width:100%; margin:0; padding:7px var(--space-3); gap:var(--space-3); border-radius:var(--radius-md); font-size:15px; line-height:1.6; align-items:center; color:var(--text-muted); }
  .manager-children :global(.ticket-row:hover), .manager-children :global(.ticket-row:focus-visible) { color:var(--text-strong); }
  .manager-children :global(.ticket-row-title) { flex:1; min-width:0; display:block; overflow:visible; white-space:normal; overflow-wrap:anywhere; line-height:inherit; line-clamp:unset; -webkit-line-clamp:unset; -webkit-box-orient:initial; }
  .manager-children :global(.child-pages) { display:flex; align-items:center; gap:var(--space-3); color:var(--text-faint); font-family:var(--font-ui); font-size:var(--type-sm); }
  .manager-children :global(.child-pages button) { color:var(--text-default); background:var(--surface-2); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-sm); padding:var(--space-1) var(--space-2); font:inherit; }
  .manager-children :global(.child-pages) { margin-top:var(--space-3); }
</style>

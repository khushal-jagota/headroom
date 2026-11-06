<script lang="ts">
  import type { ManagerPrincipal, ManagerChildFilter } from "../lib/types";
  import ManagerChildrenSection from "./ManagerChildrenSection.svelte";
  let { principal, publication, onHover = undefined, onLeave = () => undefined }: {
    principal: ManagerPrincipal;
    publication?: import("../lib/navigation/publication").NavigationPublication;
    onHover?: (element: HTMLElement, ticketId: string, immediate?: boolean) => void;
    onLeave?: () => void;
  } = $props();
  const sections: readonly ManagerChildFilter[] = ["active", "backlogged", "done"];
  let scope = $derived(`${principal.kind}:${principal.id}`);
</script>

<section class="manager-children" aria-label="Workers" data-manager-children={scope}>
  {#key scope}
    {#each sections as filter}
      <ManagerChildrenSection {principal} {publication} {filter} {onHover} {onLeave} />
    {/each}
  {/key}
</section>
<style>
  /* Rows and headers keep their inset for the hover fill, so the list pulls out by that inset and its text stands on the column edge. */
  .manager-children { margin-inline:calc(var(--space-3) * -1); }
  /* Empty Backlogged and Done groups share one quiet line: "Backlogged 0 · Done 0". */
  .manager-children :global([data-worker-section][data-empty="true"]) { display:inline-flex; align-items:center; margin-top:var(--space-1); }
  .manager-children :global([data-worker-section][data-empty="true"] .manager-workspace-status) { margin-top:0; }
  .manager-children :global([data-worker-section][data-empty="true"] summary) { color:var(--text-faintest); font-weight:500; padding-inline:var(--space-3) var(--space-1); }
  .manager-children :global([data-worker-section][data-empty="true"] + [data-worker-section][data-empty="true"])::before { content:"·"; color:var(--text-faintest); }
  .manager-children :global([data-worker-section][data-empty="true"] + [data-worker-section][data-empty="true"] summary) { padding-left:var(--space-1); }
  .manager-children :global(.manager-workspace-status + .manager-workspace-status) { margin-top:var(--space-3); }
  .manager-children :global(.manager-workspace-status > summary) { display:flex; align-items:center; gap:var(--space-2); padding:6px var(--space-3); margin:0; font-family:var(--font-ui); line-height:20px; letter-spacing:normal; border:0; border-radius:var(--radius-md); background:none; font-size:var(--type-sm); font-weight:550; color:var(--text-faint); list-style:none; }
  .manager-children :global(.manager-workspace-status > summary:hover) { background:var(--wash-hover); color:var(--text-default); }
  .manager-children :global(.manager-workspace-status-label) { flex:none; font-size:var(--type-sm); text-transform:none; letter-spacing:normal; color:inherit; }
  .manager-children :global(.manager-workspace-status[open] > summary > .manager-workspace-count) { display:block; }
  .manager-children :global(.manager-workspace-count) { margin-left:0; min-width:0; font-size:var(--type-sm); font-weight:500; line-height:20px; color:var(--text-faintest); text-align:left; font-variant-numeric:tabular-nums; }
  .manager-children :global(.manager-workspace-status-body) { padding:2px 0 0; }
  .manager-children :global(.stage-mark) { width:7px; height:7px; }
  /* A group that needs the user names itself and its rows in fuller tones, and counts with the
     needs-you mark: the accent as text on its own quiet slate, the same mark as the rail's review count. */
  .manager-children :global([data-workspace-group="awaiting_approval"] .ticket-row), .manager-children :global([data-workspace-group="awaiting_answer"] .ticket-row) { color:var(--text-default); }
  .manager-children :global([data-workspace-group="awaiting_approval"] > summary), .manager-children :global([data-workspace-group="awaiting_answer"] > summary) { color:var(--text-default); }
  .manager-children :global([data-workspace-group="awaiting_approval"] .manager-workspace-count), .manager-children :global([data-workspace-group="awaiting_answer"] .manager-workspace-count) { display:inline-flex; align-items:center; justify-content:center; min-width:20px; height:18px; padding:0 6px; border-radius:var(--radius-pill); background:var(--accent-badge); color:var(--accent-bright); font-size:11px; font-weight:640; line-height:18px; text-align:center; }
  .manager-children :global(a:focus-visible), .manager-children :global(button:focus-visible), .manager-children :global(summary:focus-visible) { outline:var(--focus-width) solid var(--focus-ring); outline-offset:2px; }
  .manager-children :global(.ticket-row) { width:fit-content; max-width:100%; margin:0; padding:var(--space-2) var(--space-3); gap:var(--space-3); border-radius:var(--radius-md); font-size:var(--type-ui); line-height:20px; align-items:center; color:var(--text-muted); }
  .manager-children :global(.ticket-row:hover), .manager-children :global(.ticket-row:focus-visible) { background:var(--wash-hover); color:var(--text-strong); }
  .manager-children :global(.ticket-row-title) { flex:1; min-width:0; display:block; overflow:visible; white-space:normal; overflow-wrap:anywhere; line-height:inherit; line-clamp:unset; -webkit-line-clamp:unset; -webkit-box-orient:initial; }
  .manager-children :global(.child-pages) { display:flex; align-items:center; gap:var(--space-3); color:var(--text-faint); font-family:var(--font-ui); font-size:var(--type-sm); }
  .manager-children :global(.child-pages button) { color:var(--text-default); background:var(--surface-2); border:var(--border-hairline) solid var(--border-card); border-radius:var(--radius-md); padding:var(--space-1) var(--space-2); font:inherit; }
  .manager-children :global(.child-pages) { margin-top:var(--space-3); }
</style>

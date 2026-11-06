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
  /* One list grid: every fill (a group heading's, a row's) spans the page column edge to edge,
     and every label inside stands on one inset from it. The list never pulls outside the
     column, so no hover bleeds past the cards and headings around it. */
  .manager-children { --list-inset:var(--space-3); margin-inline:0; }
  /* Empty Backlogged and Done groups share one line, in the same voice as every heading. */
  .manager-children :global([data-worker-section][data-empty="true"]) { display:inline-flex; align-items:center; margin-top:var(--space-2); }
  .manager-children :global([data-worker-section][data-empty="true"] .manager-workspace-status) { margin-top:0; }
  .manager-children :global([data-worker-section][data-empty="true"] summary) { padding-right:var(--space-1); }
  .manager-children :global([data-worker-section][data-empty="true"] + [data-worker-section][data-empty="true"])::before { content:"·"; color:var(--text-faintest); font-size:var(--type-sm); }
  .manager-children :global([data-worker-section][data-empty="true"] + [data-worker-section][data-empty="true"] summary) { padding-left:var(--space-1); }
  .manager-children :global(.manager-workspace-status + .manager-workspace-status) { margin-top:var(--space-3); }
  .manager-children :global([data-worker-section] + [data-worker-section]:not([data-empty="true"])) { margin-top:var(--space-3); }
  /* Every group heading, whatever the group: the label and its count, small and quiet. */
  .manager-children :global(.manager-workspace-status > summary) { display:flex; align-items:center; gap:var(--space-2); min-height:32px; padding:0 var(--list-inset); margin:0; font-family:var(--font-ui); line-height:20px; letter-spacing:normal; border:0; border-radius:var(--radius-md); background:none; font-size:var(--type-sm); font-weight:550; color:var(--text-faint); list-style:none; cursor:pointer; transition:background-color var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease); }
  .manager-children :global(.manager-workspace-status > summary::-webkit-details-marker) { display:none; }
  .manager-children :global(.manager-workspace-status > summary:hover) { background-color:var(--wash-hover); color:var(--text-default); }
  .manager-children :global(.manager-workspace-status-label) { flex:none; font-size:var(--type-sm); text-transform:none; letter-spacing:normal; color:inherit; }
  .manager-children :global(.manager-workspace-status[open] > summary > .manager-workspace-count) { display:block; }
  /* A count is a plain muted number, except where it counts what needs the person. */
  .manager-children :global(.manager-workspace-count) { margin-left:0; min-width:0; font-size:var(--type-sm); font-weight:500; line-height:20px; color:var(--text-faintest); text-align:left; font-variant-numeric:tabular-nums; }
  .manager-children :global(.manager-workspace-status-body) { padding:0; }
  .manager-children :global(.stage-mark) { width:7px; height:7px; }
  /* A group that needs the person names itself and its rows in fuller tones and counts with
     the needs-you mark: the accent fill with dark ink. */
  .manager-children :global([data-workspace-group="awaiting_approval"] .ticket-row), .manager-children :global([data-workspace-group="awaiting_answer"] .ticket-row) { color:var(--text-strong); }
  .manager-children :global([data-workspace-group="awaiting_approval"] > summary), .manager-children :global([data-workspace-group="awaiting_answer"] > summary) { color:var(--text-default); }
  .manager-children :global([data-workspace-group="awaiting_approval"] .manager-workspace-count), .manager-children :global([data-workspace-group="awaiting_answer"] .manager-workspace-count) { display:inline-flex; align-items:center; justify-content:center; min-width:18px; height:18px; padding:0 5px; border-radius:var(--radius-pill); background:var(--count-needs-you-fill); color:var(--count-needs-you-ink); font-size:11px; font-weight:700; line-height:18px; text-align:center; }
  .manager-children :global(a:focus-visible), .manager-children :global(button:focus-visible), .manager-children :global(summary:focus-visible) { outline:var(--border-strong) solid var(--focus-ring); outline-offset:-2px; }
  .manager-children :global(.ticket-row) { display:flex; width:100%; max-width:100%; min-height:36px; margin:0; padding:var(--space-2) var(--list-inset); gap:var(--space-3); border-radius:var(--radius-md); font-size:var(--type-ui); line-height:20px; align-items:center; color:var(--text-muted); box-sizing:border-box; }
  .manager-children :global(.ticket-row:hover), .manager-children :global(.ticket-row:focus-visible) { background:var(--wash-hover); color:var(--text-strong); }
  .manager-children :global(.ticket-row-title) { flex:1; min-width:0; display:block; overflow:visible; white-space:normal; overflow-wrap:anywhere; line-height:inherit; line-clamp:unset; -webkit-line-clamp:unset; -webkit-box-orient:initial; }
  .manager-children :global(.child-pages) { display:flex; align-items:center; gap:var(--space-3); color:var(--text-faint); font-family:var(--font-ui); font-size:var(--type-sm); }
  .manager-children :global(.child-pages button) { height:var(--page-control-height); color:var(--text-default); background:var(--fill-secondary); border:0; border-radius:var(--radius-md); padding:0 var(--space-3); font:inherit; font-weight:550; }
  .manager-children :global(.child-pages) { margin-top:var(--space-3); padding-inline:var(--list-inset); }
</style>

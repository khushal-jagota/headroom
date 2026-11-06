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
  /* Staging's page list, in the page's tokens: every group is a full-width header row (a quiet
     fill, its label at the left and its count at the right, open or shut), and every Ticket is
     a full-width row (the machine's icon, the title, the unread dot in a fixed column at the
     right), the whole row its click target. */
  .manager-children :global(.manager-workspace-status + .manager-workspace-status) { margin-top:0; }
  .manager-children :global(.manager-workspace-status > summary) { display:flex; align-items:center; gap:var(--space-3); min-height:35px; padding:7px var(--space-3); margin:0; font-family:var(--font-ui); font-size:var(--type-small); font-weight:var(--weight-title); line-height:21px; letter-spacing:normal; border:0; border-radius:var(--radius-md); background:var(--surface-2); color:var(--tone-secondary); list-style:none; cursor:pointer; transition:color var(--motion-fast) var(--motion-ease); }
  .manager-children :global(.manager-workspace-status > summary::-webkit-details-marker) { display:none; }
  .manager-children :global(.manager-workspace-status:not([open]) > summary) { padding-bottom:7px; }
  .manager-children :global(.manager-workspace-status > summary:hover) { background-image:var(--interaction-hover); color:var(--tone-strong); }
  .manager-children :global(.manager-workspace-status-label) { flex:1; min-width:0; font-size:inherit; text-transform:none; letter-spacing:normal; color:inherit; }
  .manager-children :global(.manager-workspace-status > summary > .manager-workspace-count) { display:block; flex:none; min-width:var(--space-4); margin-left:auto; font-size:var(--type-small); font-weight:var(--weight-small); line-height:inherit; color:var(--tone-tertiary); text-align:right; font-variant-numeric:tabular-nums; }
  .manager-children :global(.manager-workspace-status-body) { padding:var(--space-1) 0; }
  .manager-children :global(.stage-mark) { width:7px; height:7px; }
  .manager-children :global(a:focus-visible), .manager-children :global(button:focus-visible), .manager-children :global(summary:focus-visible) { outline:var(--focus-width) solid var(--focus-ring); outline-offset:2px; }
  .manager-children :global(.ticket-row) { box-sizing:border-box; width:100%; margin:0; padding:7px var(--space-3); gap:var(--space-3); border-radius:var(--radius-md); font-size:var(--type-body); font-weight:var(--weight-body); line-height:24px; align-items:center; color:var(--tone-secondary); }
  .manager-children :global(.ticket-row .machine-identity) { width:15px; height:15px; color:inherit; }
  .manager-children :global(.ticket-row:hover), .manager-children :global(.ticket-row:focus-visible) { color:var(--tone-strong); }
  .manager-children :global(.ticket-row-title) { flex:1; min-width:0; display:block; overflow:visible; white-space:normal; overflow-wrap:anywhere; line-height:inherit; line-clamp:unset; -webkit-line-clamp:unset; -webkit-box-orient:initial; }
  .manager-children :global(.ticket-row .conversation-signal-marks) { flex:none; justify-content:flex-end; width:var(--rail-mark-column); }
  .manager-children :global(.manager-workspace-empty) { padding-block:var(--space-2); color:var(--tone-tertiary); font-size:var(--type-small); }
  .manager-children :global(.child-pages) { display:flex; align-items:center; gap:var(--space-3); color:var(--tone-tertiary); font-family:var(--font-ui); font-size:var(--type-small); }
  .manager-children :global(.child-pages button) { height:var(--page-control-height); color:var(--tone-primary); background:var(--fill-secondary); border:0; border-radius:var(--radius-md); padding:0 var(--space-3); font:inherit; font-size:var(--type-row); }
  .manager-children :global(.child-pages) { margin-top:var(--space-3); }
</style>

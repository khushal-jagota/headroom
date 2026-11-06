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
  /* A page's Ticket list uses the rail's list voice at the page's body size: quiet Small
     headings with a plain count, one row per Ticket on one pitch. Every fill (a heading's, a
     row's) reaches --list-outset past the page column on both sides, so the words inside
     stand on the column's own edge with the section head above them. */
  .manager-children { --list-outset:var(--space-2); --list-row-height:32px; --list-heading-gap:8px; margin:calc(var(--list-heading-gap) * -1) 0 0; display:flex; flex-direction:column; }
  /* Every group is its own heading over its rows, the empty ones included, so Backlogged and
     Done read the same way on every page. */
  /* A heading binds to its rows: 10px from the row above to its words, 2px from its words to
     the next row's fill, as in the rail. Its own fill keeps 2px of that on each side. */
  .manager-children :global(.manager-workspace-status > summary) { display:flex; align-items:center; gap:5px; min-height:0; height:20px; padding:0 var(--list-outset); margin:var(--list-heading-gap) calc(var(--list-outset) * -1) 0; font-family:var(--font-ui); font-size:var(--type-small); font-weight:var(--weight-small); line-height:16px; letter-spacing:normal; border:0; border-radius:var(--radius-md); background:none; color:var(--tone-tertiary); font-variant-numeric:tabular-nums; list-style:none; cursor:pointer; transition:background-color var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease); }
  .manager-children :global(.manager-workspace-status > summary::-webkit-details-marker) { display:none; }
  .manager-children :global(.manager-workspace-status:not([open]) > summary) { padding-bottom:0; }
  .manager-children :global(.manager-workspace-status > summary:hover) { background-color:var(--wash-hover); color:var(--tone-primary); }
  .manager-children :global(.manager-workspace-status-label) { flex:none; font-size:inherit; font-weight:inherit; text-transform:none; letter-spacing:normal; color:inherit; }
  /* A count is a plain number after its label, in the label's own tone, open or shut. */
  .manager-children :global(.manager-workspace-status > summary > .manager-workspace-count) { display:block; flex:none; margin-left:0; min-width:0; font-size:inherit; font-weight:inherit; line-height:inherit; color:inherit; text-align:left; font-variant-numeric:tabular-nums; }
  .manager-children :global(.manager-workspace-status-body) { display:flex; flex-direction:column; gap:var(--rail-row-gap); padding:0; }
  .manager-children :global(.stage-mark) { width:7px; height:7px; }
  .manager-children :global(a:focus-visible), .manager-children :global(button:focus-visible), .manager-children :global(summary:focus-visible) { outline:var(--focus-width) solid var(--focus-ring); outline-offset:2px; }
  /* A row hugs its words: its fill is the text and 8px either side (240px at least), so a
     short title never sits in a wide band. A row: the machine's mark at the left, the title in the body voice, the unread dot in a
     column of its own at the right. A row that needs the person is bright and a step
     heavier; every other row rests dim, as in the rail. */
  .manager-children :global(.ticket-row) { box-sizing:border-box; display:flex; align-items:center; gap:10px; width:fit-content; min-width:240px; max-width:calc(100% + var(--list-outset) * 2); min-height:var(--list-row-height); margin:0 calc(var(--list-outset) * -1); padding:6px var(--list-outset); border-radius:var(--radius-md); font-size:var(--type-body); font-weight:var(--weight-body); line-height:20px; color:var(--tone-secondary); transition:background-color var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease); }
  .manager-children :global(.ticket-row .machine-identity) { color:var(--tone-tertiary); }
  .manager-children :global(.ticket-row:hover), .manager-children :global(.ticket-row:focus-visible) { background:var(--wash-hover); color:var(--tone-strong); }
  .manager-children :global(:is([data-workspace-group="awaiting_approval"], [data-workspace-group="awaiting_answer"]) .ticket-row) { color:var(--tone-strong); font-weight:var(--weight-needs-you); }
  .manager-children :global(.ticket-row-title) { flex:0 1 auto; min-width:0; display:block; overflow:visible; white-space:normal; overflow-wrap:anywhere; line-height:inherit; line-clamp:unset; -webkit-line-clamp:unset; -webkit-box-orient:initial; }
  .manager-children :global(.ticket-row .conversation-signal-marks) { justify-content:flex-end; width:var(--rail-mark-column); }
  .manager-children :global(.manager-workspace-empty) { padding:0; color:var(--tone-tertiary); font-size:var(--type-small); }
  .manager-children :global(.child-pages) { display:flex; align-items:center; gap:var(--space-3); color:var(--tone-tertiary); font-family:var(--font-ui); font-size:var(--type-small); }
  .manager-children :global(.child-pages button) { height:var(--page-control-height); color:var(--tone-primary); background:var(--fill-secondary); border:0; border-radius:var(--radius-md); padding:0 var(--space-3); font:inherit; font-size:var(--type-row); }
  .manager-children :global(.child-pages) { margin-top:var(--space-3); }
</style>

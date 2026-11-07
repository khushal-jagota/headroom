<script lang="ts">
  import type { ManagerPrincipal, ManagerChildFilter } from "../lib/types";
  import ManagerChildrenSection from "./ManagerChildrenSection.svelte";
  let { principal, publication, title = undefined, onHover = undefined, onLeave = () => undefined }: {
    principal: ManagerPrincipal;
    title?: string;
    publication?: import("../lib/navigation/publication").NavigationPublication;
    onHover?: (element: HTMLElement, ticketId: string, immediate?: boolean) => void;
    onLeave?: () => void;
  } = $props();
  const sections: readonly ManagerChildFilter[] = ["active", "backlogged", "done"];
  let scope = $derived(`${principal.kind}:${principal.id}`);
</script>

<section class="manager-children" aria-label="Workers" data-manager-children={scope}>
  {#if title}<h2 class="manager-children-title">{title}</h2>{/if}
  {#key scope}
    {#each sections as filter}
      <ManagerChildrenSection {principal} {publication} {filter} {onHover} {onLeave} />
    {/each}
  {/key}
</section>
<style>
  /* The page's Ticket list is the sidebar's, set on the page (owner): one card in the sidebar
     bubble's colour (#1b1b1b, 12px corners), its groups under capital headings (11/600, 0.06em,
     as the sidebar) with their counts at the right, its Tickets in tight rows under them, each
     with its machine's icon. Every heading is still the control that opens and shuts its group,
     the whole row its target. A Ticket carries the sidebar's two levels under the sidebar's
     conditions: bright when it waits on your approval or answer, errored, or has a message for
     you; dim otherwise. Hover and the open row are the sidebar's fills. */
  .manager-children { box-sizing:border-box; padding:0 var(--space-2) var(--space-2); border-radius:var(--rail-bubble-radius); background:var(--surface-rail); }
  .manager-children :global(.manager-workspace-status) { margin-top:var(--space-3); }
  /* The card's own title, set and placed as a goal card's: 15/600, 20px from the card's top and
     left (the card's 8px plus a row's 12px). */
  .manager-children-title { margin:0; padding:calc(var(--page-card-pad) - 0px) var(--space-3) var(--space-1); font-size:var(--type-page-section); font-weight:var(--weight-page-section); line-height:20px; letter-spacing:0; color:var(--tone-primary); }
  .manager-children :global(.manager-workspace-status > summary) { display:flex; align-items:center; gap:var(--space-3); box-sizing:border-box; min-height:28px; padding:0 var(--space-3); margin:0; font-family:var(--font-ui); font-size:var(--rail-heading-size); font-weight:var(--rail-heading-weight); letter-spacing:var(--rail-heading-tracking); text-transform:uppercase; line-height:16px; border:0; border-radius:var(--radius-md); background:none; color:var(--rail-tone-heading); cursor:pointer; list-style:none; transition:color var(--motion-fast) var(--motion-ease); }
  .manager-children :global(.manager-workspace-status > summary::-webkit-details-marker) { display:none; }
  .manager-children :global(.manager-workspace-status > summary:hover) { background:var(--wash-hover); color:var(--tone-strong); }
  .manager-children :global(.manager-workspace-status-label) { flex:1; min-width:0; font-size:inherit; text-transform:inherit; letter-spacing:inherit; color:inherit; }
  .manager-children :global(.manager-workspace-status > summary > .manager-workspace-count) { display:block; flex:none; min-width:var(--space-4); margin-left:auto; font-size:var(--type-small); font-weight:var(--weight-small); letter-spacing:normal; line-height:inherit; color:var(--tone-tertiary); text-align:right; font-variant-numeric:tabular-nums; }
  .manager-children :global(.manager-workspace-status-body) { padding:2px 0 0; }
  .manager-children :global(.ticket-row) { box-sizing:border-box; width:100%; min-height:28px; margin:0; padding:2px var(--space-3); gap:var(--space-3); border-radius:var(--radius-md); font-size:var(--type-body); font-weight:var(--weight-body); line-height:24px; align-items:center; color:var(--rail-tone-rest); background:none; }
  .manager-children :global(.ticket-row:hover) { background:var(--wash-hover); color:var(--tone-strong); }
  .manager-children :global(.ticket-row:focus-visible) { color:var(--tone-strong); }
  .manager-children :global(.manager-workspace-status:is([data-workspace-group="awaiting_approval"], [data-workspace-group="awaiting_answer"], [data-workspace-group="errored"]) .ticket-row),
  .manager-children :global(.ticket-row[data-unread="true"]) { color:var(--rail-tone-attention); }
  .manager-children :global(.ticket-row-title) { flex:1; min-width:0; display:block; overflow:visible; white-space:normal; overflow-wrap:anywhere; line-height:inherit; line-clamp:unset; -webkit-line-clamp:unset; -webkit-box-orient:initial; }
  .manager-children :global(.stage-mark) { width:7px; height:7px; }
  .manager-children :global(a:focus-visible), .manager-children :global(button:focus-visible), .manager-children :global(summary:focus-visible) { outline:var(--focus-width) solid var(--focus-ring); outline-offset:2px; }
  .manager-children :global(.ticket-row .machine-identity) { width:15px; height:15px; color:inherit; }
  .manager-children :global(.ticket-row .conversation-signal-marks) { flex:none; justify-content:center; width:14px; }
  .manager-children :global(.manager-workspace-empty) { padding:var(--space-2) var(--space-3); color:var(--tone-tertiary); font-size:var(--type-small); }
  .manager-children :global(.child-pages) { display:flex; align-items:center; gap:var(--space-3); color:var(--tone-tertiary); font-family:var(--font-ui); font-size:var(--type-small); }
  .manager-children :global(.child-pages button) { height:var(--page-control-height); color:var(--tone-primary); background:var(--fill-secondary); border:0; border-radius:var(--radius-md); padding:0 var(--space-3); font:inherit; font-size:var(--type-row); }
  .manager-children :global(.child-pages) { margin-top:var(--space-3); }
</style>

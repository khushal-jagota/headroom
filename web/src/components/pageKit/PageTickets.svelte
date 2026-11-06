<script lang="ts">
 import ManagerChildren from '../ManagerChildren.svelte';
 import type {ManagerPrincipal} from '../../lib/types';
 let {principal,publication,onHover,onLeave}: {principal:ManagerPrincipal;publication?:import('../../lib/navigation/publication').NavigationPublication;onHover?:(element:HTMLElement,id:string,immediate?:boolean)=>void;onLeave?:()=>void}=$props();
</script>
<section class="page-tickets" aria-label="Tickets"><h2>Tickets</h2><ManagerChildren {principal} {publication} {onHover} {onLeave}/></section>
<style>
/* A row hugs its words: its fill is the text and the row's padding either side, 240px at least,
   so a short title never sits in a wide band and its preview can open beside its end. (The
   page's own list rule sets the full width; this one, the list's width owner, wins it.) */
.page-tickets.page-tickets.page-tickets :global(.manager-children .ticket-row){width:fit-content;min-width:240px;max-width:calc(100% + var(--page-row-outset, var(--space-2)) * 2)}
.page-tickets{margin-top:0}h2{margin:0 0 var(--space-2);font-size:var(--type-page-section);font-weight:var(--weight-page-section);letter-spacing:-.01em;line-height:24px;color:var(--text-strong)}</style>

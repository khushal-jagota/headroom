<script lang="ts">
 import ManagerChildren from '../ManagerChildren.svelte';
 import type {ManagerPrincipal} from '../../lib/types';
 let {principal,publication,onHover,onLeave}: {principal:ManagerPrincipal;publication?:import('../../lib/navigation/publication').NavigationPublication;onHover?:(element:HTMLElement,id:string,immediate?:boolean)=>void;onLeave?:()=>void}=$props();
</script>
<section class="page-tickets" aria-label="Tickets"><h2>Tickets</h2><ManagerChildren {principal} {publication} {onHover} {onLeave}/></section>
<style>
.page-tickets{margin-top:0}h2{margin:0 0 var(--space-2);font-size:var(--type-page-section);font-weight:var(--weight-page-section);letter-spacing:-.01em;line-height:24px;color:var(--text-strong)}</style>

<script lang="ts">
 import ManagerChildren from '../ManagerChildren.svelte';
 import type {ManagerPrincipal} from '../../lib/types';
 let {principal,publication,onHover,onLeave}: {principal:ManagerPrincipal;publication?:import('../../lib/navigation/publication').NavigationPublication;onHover?:(element:HTMLElement,id:string,immediate?:boolean)=>void;onLeave?:()=>void}=$props();
</script>
<section class="page-tickets" aria-label="Tickets"><h2>Tickets</h2><ManagerChildren {principal} {publication} {onHover} {onLeave}/></section>
<style>.page-tickets{margin-top:var(--space-7)}h2{margin:0 0 var(--space-3);font-size:var(--type-lg);font-weight:600;letter-spacing:-.011em;line-height:1.4;color:var(--text-strong)}</style>

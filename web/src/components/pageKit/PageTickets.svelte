<script lang="ts">
 import ManagerChildren from '../ManagerChildren.svelte';
 import type {ManagerPrincipal} from '../../lib/types';
 let {principal,publication,onHover,onLeave}: {principal:ManagerPrincipal;publication?:import('../../lib/navigation/publication').NavigationPublication;onHover?:(element:HTMLElement,id:string,immediate?:boolean)=>void;onLeave?:()=>void}=$props();
</script>
<section class="page-tickets" aria-label="Tickets"><h2>Tickets</h2><ManagerChildren {principal} {publication} {onHover} {onLeave}/></section>
<style>.page-tickets{margin-top:var(--space-6)}h2{margin:0 0 var(--space-3);font-size:var(--type-section);font-weight:700;letter-spacing:-.014em;line-height:1.35;color:var(--text-strong)}</style>

<script lang="ts">
 import ManagerChildren from '../ManagerChildren.svelte';
 import type {ManagerPrincipal} from '../../lib/types';
 let {principal,publication,onHover,onLeave}: {principal:ManagerPrincipal;publication?:import('../../lib/navigation/collection').NavigationPublication;onHover?:(element:HTMLElement,id:string,immediate?:boolean)=>void;onLeave?:()=>void}=$props();
</script>
<section class="page-tickets" aria-label="Tickets"><h2>Tickets</h2><ManagerChildren {principal} {publication} {onHover} {onLeave}/></section>
<style>.page-tickets{margin-top:40px}h2{margin:0 0 20px;font-size:22px;font-weight:600;letter-spacing:-.015em;line-height:1.3}</style>

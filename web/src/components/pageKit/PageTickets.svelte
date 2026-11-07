<script lang="ts">
 import ManagerChildren from '../ManagerChildren.svelte';
 import type {ManagerPrincipal} from '../../lib/types';
 let {principal,publication,onHover,onLeave}: {principal:ManagerPrincipal;publication?:import('../../lib/navigation/publication').NavigationPublication;onHover?:(element:HTMLElement,id:string,immediate?:boolean)=>void;onLeave?:()=>void}=$props();
</script>
<!-- "Tickets" is the card's own title, inside it (owner), as a goal card names itself. -->
<section class="page-tickets" aria-label="Tickets"><ManagerChildren title="Tickets" {principal} {publication} {onHover} {onLeave}/></section>
<style>
.page-tickets{margin-top:0}</style>

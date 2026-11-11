<script lang="ts">
 import type {GoalResponse} from '../../lib/goals/contracts';
 import {mutateJson} from '../../lib/mutate';
 import ErrorLine from '../ErrorLine.svelte';
 let {goal}:{goal:GoalResponse}=$props();
 let deleting=$state(false),error=$state<unknown>(null);
 async function remove(){
  if(deleting||!window.confirm(`Permanently delete “${goal.title}” and its page, files, and outcome history? Shared measurements and datasets will remain.`))return;
  const deletedId=goal.id,deletedAddress=window.location.hash;deleting=true;error=null;
  try{
   await mutateJson(`/api/goals/${encodeURIComponent(deletedId)}`,{method:'DELETE'});
   if(window.location.hash===deletedAddress)window.location.hash='#/goals';
  }catch(failure){error=failure;}finally{deleting=false;}
 }
</script>
<div class="delete-action">
 <button onclick={()=>void remove()} disabled={deleting}>{deleting?'Deleting…':'Delete goal'}</button>
 {#if error}<ErrorLine {error}/>{/if}
</div>
<style>
 .delete-action{display:flex;align-items:baseline;gap:var(--space-2)}
 button{border:0;background:none;padding:var(--space-1);font:inherit;color:var(--text-faint);cursor:pointer;border-radius:var(--radius-sm)}
 button:hover,button:focus-visible{color:var(--accent-danger,var(--text-strong))}
 button:disabled{opacity:.65;cursor:wait}
</style>

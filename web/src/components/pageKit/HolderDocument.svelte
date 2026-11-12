<script lang="ts">
 import {setContext} from 'svelte';
 import {createQuery} from '@tanstack/svelte-query';
 import type {GoalReadingsResponse} from '../../lib/goals/contracts';
 import type {ManagerPrincipal} from '../../lib/types';
 import {PAGE_HOLDER,PAGE_CONTEXT,type PageContext} from '../../lib/pageKit/context';
 import {holderPageQuery,holderPagePath,createHolderEditor} from '../../lib/pageKit/holderEditing';
 import {jsonQuery} from '../../lib/queryCatalogue';
 import {createGoalRequestSessions} from '../../lib/goals/requestSessions';
 import {createGoalPageContext} from '../../lib/goals/pageContext';
 import {refreshAtBoundary} from '../../lib/goals/presentation';
 import PageDocument from './PageDocument.svelte';
 import ErrorLine from '../ErrorLine.svelte';
 let {holder}:{holder:ManagerPrincipal}=$props();
 let width=$state(0);
 let narrow=$derived(width<=640);
 const page=createQuery(()=>holderPageQuery(holder));
 const readings=createQuery(()=>jsonQuery<GoalReadingsResponse>(['holder-readings',holder.kind,holder.id],`${holderPagePath(holder)}/readings`));
 let ownerId=$derived(`holder:${holder.kind}:${holder.id}`);
 let editor=$derived(createHolderEditor(holder));
 const sessions=createGoalRequestSessions();
 setContext(PAGE_HOLDER,()=>holder);
 $effect(()=>{if(readings.data)return refreshAtBoundary(readings.data.next_refresh_at,()=>void readings.refetch());});
 setContext<PageContext>(PAGE_CONTEXT,createGoalPageContext({
  goalId:()=>ownerId,
  goal:()=>page.data?{revision:page.data.page_revision,completed:false,status:'neutral',status_date:readings.data?.status_date}:undefined,
  readings:()=>readings.data,narrow:()=>narrow,sessions:()=>sessions,
 },{
  updateBlock:(id,update,revision)=>editor.updateBlock(id,update,revision),
  manualSession:measureId=>sessions.manual(ownerId,measureId)
 }));
</script>
<div bind:clientWidth={width}>
 {#if page.error}<ErrorLine error={page.error}/>{/if}
 {#if readings.error}<ErrorLine error={readings.error}/>{/if}
 {#if page.data?.page}<PageDocument document={page.data.page} showHeader={false}/>{/if}
</div>

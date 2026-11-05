import {fetchJson} from '../api';
import {queryClient} from '../queryClient';
import {jsonQuery} from '../queryCatalogue';
import {createPageEditor} from '../goals/editing';
import type {ManagerPrincipal} from '../types';
import type {PageDocument} from '../goals/contracts';
export type HolderPage={page:PageDocument|null;page_revision:number};
export const holderPagePath=(holder:ManagerPrincipal)=>holder.kind==='chief'?'/api/chief/page':`/api/managers/${encodeURIComponent(holder.id)}/page`;
export const holderPageQuery=(holder:ManagerPrincipal)=>jsonQuery<HolderPage>(['holder-page',holder.kind,holder.id],holderPagePath(holder));
export function createHolderEditor(holder:ManagerPrincipal){
 const path=holderPagePath(holder);
 return createPageEditor<HolderPage,{page:PageDocument}>(path,{
  read:async()=>(await queryClient.fetchQuery(holderPageQuery(holder))).body,
  write:(patch,revision)=>fetchJson<HolderPage>(path,{method:'PATCH',body:{...patch,expected_revision:revision}}),
  page:document=>document.page,
  revision:document=>document.page_revision,
  pagePatch:page=>({page}),
  refresh:()=>queryClient.invalidateQueries(),
 });
}

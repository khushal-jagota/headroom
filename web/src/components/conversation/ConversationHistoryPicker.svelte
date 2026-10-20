<script lang="ts">
  import { createQuery } from '@tanstack/svelte-query';
  import { jsonQuery } from '../../lib/queryCatalogue';
  import type { HistoryOwnerKind, ConversationHistoryPage } from '../../lib/catalogs/history';
  import { CATALOG_PAGE_SIZE, CATALOG_ROW_HEIGHT, CATALOG_WINDOW_STEP, CATALOG_OVERSCAN_ROWS } from '../../lib/catalogs/config';
  import ListboxPicker, { type ListboxPickerController, type ListboxPickerItem } from './ListboxPicker.svelte';
  let { owner, selected = $bindable(null), label, conversationExists }: {
    owner:{kind:HistoryOwnerKind;id:string}; selected?:string|null; label:string; conversationExists:boolean;
  }=$props();
  const CURRENT='__current__';
  let offset=$state(0);
  let selectedSeek=$state<string|null>(selected);
  let prefix=$state<string|null>(null);
  let after=$state(-1);
  let activeIndex=$state<number|undefined>(undefined);
  let total=$state(1);
  let selectedLabel=$state('');
  let controller=$state<ListboxPickerController>();
  const locale=Intl.DateTimeFormat().resolvedOptions().locale;
  const timezone=Intl.DateTimeFormat().resolvedOptions().timeZone;
  let path=$derived(`/api/conversation-history/${owner.kind}/${encodeURIComponent(owner.id)}?limit=${CATALOG_PAGE_SIZE}&offset=${offset}&locale=${encodeURIComponent(locale)}&timezone=${encodeURIComponent(timezone)}${selectedSeek ? `&selected=${encodeURIComponent(selectedSeek)}`:''}${prefix ? `&prefix=${encodeURIComponent(prefix)}&after=${after}`:''}`);
  const page=createQuery(()=>jsonQuery<ConversationHistoryPage>(['conversation-history',owner.kind,owner.id,path],path));
  let currentLabel=$derived(!conversationExists && selected===null ? 'Current · new conversation':'Current conversation');
  let items=$derived<ListboxPickerItem[]>([
    ...(page.data?.offset===0 ? [{value:CURRENT,name:currentLabel}]:[]),
    ...(page.data?.entries ?? []).map(entry=>({value:entry.conversation_id,name:entry.label}))
  ]);
  let itemOffset=$derived(page.data ? page.data.offset===0 ? 0 : page.data.offset+1 : 0);
  $effect(()=>{
    if(!page.data) return;
    total=page.data.total+1;
    if(page.data.active_index!==null) activeIndex=page.data.active_index+1;
    const entry=page.data.entries.find(entry=>entry.conversation_id===selected);
    if(entry) selectedLabel=entry.label;
  });
  function windowAt(index:number) {
    const next=Math.floor(Math.max(0,index-1-CATALOG_OVERSCAN_ROWS)/CATALOG_WINDOW_STEP)*CATALOG_WINDOW_STEP;
    if(next===offset && selectedSeek===null && prefix===null) return;
    offset=next; selectedSeek=null; prefix=null; activeIndex=undefined;
  }
  function seek(text:string,index:number) {
    if(currentLabel.toLocaleLowerCase().startsWith(text)) {
      offset=0; selectedSeek=null; prefix=null; activeIndex=0; return;
    }
    selectedSeek=null; prefix=text; after=index-1; activeIndex=undefined;
  }
</script>
<div data-conversation-history>
  <ListboxPicker {items} selectedValue={selected ?? CURRENT} {label} listLabel={label}
    bind:controller pageWindow={{offset:itemOffset,total,rowHeight:CATALOG_ROW_HEIGHT,activeIndex,onWindow:windowAt,onTypeahead:seek}}
    panelBusy={page.isFetching} onOpen={()=>{ selectedSeek=selected; prefix=null; }}
    onChoose={(value)=>{selected=value===CURRENT ? null:value; selectedLabel=items.find(item=>item.value===value)?.name ?? ''; controller?.close();}}
    panelAttributes={{'data-history-picker':'true'}}>
    {#snippet triggerContent()}<span>{selected===null ? currentLabel : selectedLabel || label}</span>{/snippet}
    {#snippet optionContent(item)}<span>{item.name}</span>{/snippet}
    {#snippet afterList()}{#if page.error}<p role="status">History could not load. <button onclick={()=>page.refetch()}>Retry</button></p>{/if}{/snippet}
  </ListboxPicker>
</div>
<style>
  div :global([data-history-picker]) { width:min(440px,calc(100vw - 32px)); }
  div :global(.listbox-picker-option > span:first-child) { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
</style>

import {writeFile} from "node:fs/promises";
import {join} from "node:path";
import {fileURLToPath} from "node:url";

export async function prepare({scratchRoot}) {
  const host = join(scratchRoot, `skeleton-host-${process.pid}.svelte`);
  const main = join(scratchRoot, `skeleton-main-${process.pid}.ts`);
  const index = join(scratchRoot, `skeleton-index-${process.pid}.html`);
  await writeFile(host, `<script lang="ts">
    import {createManagedMarkdownSurface, type ReadOnlyManagedMarkdownSurface} from "../src/lib/managedMarkdown";
    import {MARKDOWN_WORKER_THRESHOLD} from "../src/lib/markdown/config";
    import MarkdownBlock from "../src/components/MarkdownBlock.svelte";
    import {flushSync, setContext} from "svelte";
    import PageChecklist from "../src/components/pageKit/PageChecklist.svelte";
    import PageDocument from "../src/components/pageKit/PageDocument.svelte";
    import {PAGE_CONTEXT, type PageContext} from "../src/lib/pageKit/context";
    import type {ChecklistBlock} from "../src/lib/pageKit";
    let checklistShown = $state(false);
    let checklistNarrow = $state(false), checklistSupplementary = $state(false);
    let checklistPresentation = $state<'normal'|'small'>('normal');
    let nestedShown = $state(false);
    const nestedChecklist = (id: string): ChecklistBlock => ({type:'checklist',id,title:id,items:[{id:id+'-step',label:id+' item',state:'open',detail:'Saved detail'}]});
    const nestedDocument = {kind:'headroom.page' as const,version:1 as const,title:'Nested checklists',summary:'',blocks:[
      nestedChecklist('direct'),
      {type:'section' as const,id:'section',title:'Section',blocks:[nestedChecklist('section-list')]},
      {type:'grid' as const,id:'grid',blocks:[nestedChecklist('grid-list')]},
      {type:'segmented' as const,id:'views',views:[{id:'first',label:'First',blocks:[{type:'grid' as const,id:'nested-grid',blocks:[{type:'section' as const,id:'nested-section',title:'Nested',blocks:[nestedChecklist('segmented-list')]}]}]}]}
    ]};
    let checklist = $state<ChecklistBlock>({type:'checklist',id:'delivery',title:'Delivery',doneTitle:'Delivery complete',items:[
      {id:'draft',label:'Draft',state:'done'},{id:'publish',label:'Publish',state:'open'}
    ]});
    let editQueue: Promise<unknown> = Promise.resolve();
    setContext<PageContext>(PAGE_CONTEXT, {goalId:'fixture',revision:0,get narrow(){return checklistNarrow;},values:[],completed:false,status:'neutral',
      updateBlock(_id, update) {
        const operation = editQueue.catch(() => undefined).then(() => new Promise<void>((resolve, reject) => {
          (window as any).__checklistReply = (accept: boolean) => {
            (window as any).__checklistReply = null;
            if (accept) { checklist = update(checklist) as ChecklistBlock; resolve(); }
            else reject(new Error('Save rejected'));
          };
        }));
        editQueue = operation;
        return operation;
      }
    });
    (window as any).__checklist = (shown: boolean) => flushSync(() => checklistShown = shown);
    (window as any).__checklistOptions = (narrow: boolean, supplementary = false, presentation: 'normal'|'small' = 'normal') => flushSync(() => {
      checklistNarrow = narrow; checklistSupplementary = supplementary; checklistPresentation = presentation;
    });
    (window as any).__checklistVariant = (variant: 'empty'|'partial'|'complete') => flushSync(() => {
      checklist = {...checklist,items:variant === 'empty' ? [] : [{id:'draft',label:'Draft',state:'done'}, {id:'publish',label:'Publish',state:variant === 'complete' ? 'done' : 'open'}]};
    });
    (window as any).__nestedChecklists = (shown: boolean) => flushSync(() => nestedShown = shown);
    (window as any).__checklistSaved = (complete: boolean) => flushSync(() => {
      checklist = {...checklist, items:checklist.items.map(item => ({...item,state:item.id === 'draft' || complete ? 'done' : 'open'}))};
    });
    import FileDocument from "../src/components/FileDocument.svelte";
    import FilePreview from "../src/components/FilePreview.svelte";
    let htmlMode = $state("");
    let htmlPath = $state("site/index.html");
    (window as any).__html = (mode: string, path = "site/index.html") => flushSync(() => { htmlMode = mode; htmlPath = path; });
    import SkeletonRegion from "../src/components/SkeletonRegion.svelte";
    let markdownHost: HTMLElement;
    let markdownSurface: ReadOnlyManagedMarkdownSurface | undefined;
    (window as any).__markdown = (operation: string) => {
      if (operation === 'destroy') { markdownSurface?.destroy(); markdownSurface = undefined; return; }
      markdownSurface ||= createManagedMarkdownSurface(markdownHost, {mode:'read-only'});
      markdownSurface.update({source:operation === 'small' ? '# Replacement document' : operation.repeat(MARKDOWN_WORKER_THRESHOLD),emptyText:'',depth:0,visited:[]});
    };
    let loading = $state(true), long = $state(false), shown = $state(true), idle = $state(false), flat = $state(false);
    (window as any).__load = (pending: boolean, extended = false) => flushSync(() => {loading = pending; long = extended;});
    (window as any).__show = (value: boolean) => flushSync(() => shown = value);
    (window as any).__flush = () => flushSync();
    (window as any).__flat = (value: boolean) => flushSync(() => flat = value);
    (window as any).__idle = (value: boolean) => flushSync(() => idle = value);
  </script>
  {#if checklistShown}<section data-checklist-fixture><PageChecklist block={checklist} presentation={checklistPresentation} supplementary={checklistSupplementary}/></section>{/if}
  {#if nestedShown}<section data-nested-checklists style="width:360px"><PageDocument document={nestedDocument} supplementary showHeader={false}/></section>{/if}
  {#if htmlMode}<section data-html-fixture style="height:500px">
    {#if htmlMode === 'full'}<FileDocument target={{kind:'ticket-file',ticketId:'t_owner',path:htmlPath}} />
    {:else}<FilePreview target={{kind:'ticket-file',ticketId:'t_owner',path:htmlPath}} />{/if}
  </section>{/if}
  <header>Persistent heading</header>
  <section data-fixed style="height:400px;display:flex;flex-direction:column">
    {#if shown}<SkeletonRegion shape="document" layout="fill" {loading} loadingText="Loading document">
      <div style:height={long ? '900px' : '60px'} style="flex:none">Document content</div>
    </SkeletonRegion>{/if}
  </section>
  <button data-control>Persistent control</button>
  <section data-flow>{#if shown}<SkeletonRegion shape="block" {loading} loadingText="Loading card">
    <div style:height={long ? '420px' : '40px'}>Card content</div>
  </SkeletonRegion>{/if}</section>
  <footer>After intrinsic card</footer>
  <p data-inline>{#if shown}<SkeletonRegion shape="inline" {loading} loadingText="Loading value">42</SkeletonRegion>{/if}</p>
  {#if idle}<section data-idle><SkeletonRegion shape="block" loading={false} loadingText="Loading cached card"><div>Cached card</div></SkeletonRegion>
    <SkeletonRegion shape="inline" loading={false} loadingText="Loading cached value">Cached value</SkeletonRegion></section>{/if}
  {#if flat}<section data-flat style="display:flex;gap:24px;align-items:flex-start">
    <div>Before</div><SkeletonRegion shape="block" loading={false} loadingText="Loading layout">
      <div data-flat-first style="width:90px;height:30px;margin-top:20px">First</div>
      <div data-flat-second style="width:80px;height:40px;animation:proof-slide 600ms linear forwards">Second</div>
      <MarkdownBlock text="Layout **Markdown**" />
      <div data-flat-existing class="review-arrive review-arrive--4">Existing entrance</div>
    </SkeletonRegion><div data-flat-after>After</div>
  </section>{/if}
  <style>@keyframes -global-proof-slide {from {transform:translateY(12px)} to {transform:translateY(0)}}</style>
  <section data-conversation-thread data-markdown-fixture bind:this={markdownHost}></section>`);
  await writeFile(main, `import {mount} from 'svelte'; import Host from './${host.split('/').at(-1)}';
    import '../../assets/tokens.css'; import '../../assets/app.css';
    (window as any).__workers=[];
    (window as any).__mockMarkdownWorker=()=>{window.Worker=class {
      onmessage:any; onerror:any; calls:any[]=[]; terminated=false;
      constructor(){(window as any).__workers.push(this);}
      postMessage(request:any){this.calls.push(request);}
      terminate(){this.terminated=true;}
      reply(failed=false){this.onmessage?.({data:failed ? {id:this.calls[0].id,error:'Parse failed'} : {id:this.calls[0].id,document:{definitions:[],children:[{type:'element',tagName:'h2',properties:{},children:[{type:'text',value:'Worker document'}]}]}}});}
    } as any;};
    (window as any).__shifts=[];
    new PerformanceObserver(list => (window as any).__shifts.push(...list.getEntries().map((e:any)=>({value:e.value,recent:e.hadRecentInput})))).observe({type:'layout-shift',buffered:true});
    mount(Host,{target:document.getElementById('app')!});`);
  await writeFile(index, `<!doctype html><html><body><div id="app"></div><script type="module" src="./${main.split('/').at(-1)}"></script></body></html>`);
  return {inputs:[index], arguments:[index], python:fileURLToPath(new URL('./skeleton-regions.py',import.meta.url))};
}

import {writeFile} from "node:fs/promises";
import {join} from "node:path";
import {fileURLToPath} from "node:url";

export async function prepare({scratchRoot}) {
  const host = join(scratchRoot, `skeleton-host-${process.pid}.svelte`);
  const main = join(scratchRoot, `skeleton-main-${process.pid}.ts`);
  const index = join(scratchRoot, `skeleton-index-${process.pid}.html`);
  await writeFile(host, `<script lang="ts">
    import SkeletonRegion from "../src/components/SkeletonRegion.svelte";
    let loading = $state(true), long = $state(false), shown = $state(true);
    (window as any).__load = (pending: boolean, extended = false) => {loading = pending; long = extended;};
    (window as any).__show = (value: boolean) => shown = value;
  </script>
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
  <footer>After intrinsic card</footer>`);
  await writeFile(main, `import {mount} from 'svelte'; import Host from './${host.split('/').at(-1)}';
    import '../../assets/tokens.css'; import '../../assets/app.css';
    (window as any).__shifts=[];
    new PerformanceObserver(list => (window as any).__shifts.push(...list.getEntries().map((e:any)=>({value:e.value,recent:e.hadRecentInput})))).observe({type:'layout-shift',buffered:true});
    mount(Host,{target:document.getElementById('app')!});`);
  await writeFile(index, `<!doctype html><html><body><div id="app"></div><script type="module" src="./${main.split('/').at(-1)}"></script></body></html>`);
  return {inputs:[index], arguments:[index], python:fileURLToPath(new URL('./skeleton-regions.py',import.meta.url))};
}

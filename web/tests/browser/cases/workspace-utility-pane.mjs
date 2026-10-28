import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const host = `workspace-utility-pane-host-${process.pid}.svelte`;
  const main = `workspace-utility-pane-main-${process.pid}.ts`;
  const html = join(scratchRoot, `workspace-utility-pane-index-${process.pid}.html`);
  // The whole shell, so the address is read by the real router on every load and on a
  // reload. Network sources use the case fixture through the real navigation transport.
  await writeFile(join(scratchRoot, host), `<script lang="ts">
import AppWithQueryClient from '../src/AppWithQueryClient.svelte';
import { railDomMatches } from '../src/lib/workspaceRailDom';
import { buildWorkspaceRail } from '../src/lib/workspaceRail';
(globalThis as any).__railDomMatches = (cards, managers, chief) => railDomMatches(
  document.querySelector('.workspace-rail'), buildWorkspaceRail(cards, managers), chief, false,
  managers.filter(item=>item.settled).length);
import {installConversationViewReaderFixture} from '../tests/support/conversationViewReaderFixture';
const overlay = new URLSearchParams(location.search).has('overlay');
if (overlay) installConversationViewReaderFixture(location.hash.includes('t_overlay0') ? 'ticket-overlay' : 'bounded-reader');
const ReaderSource = globalThis.EventSource;
// Supply wire snapshots through the production EventSource transport. The optional
// fold fixture uses the same App host and the real navigation parser and projection.
const sources = new Set<any>();
let sourceIdentity = 0;
(globalThis as any).__navigationFrames = [];
(globalThis as any).__heldNavigation = [];
(globalThis as any).__heldNavigationFrames = [];
(globalThis as any).__navigationOrder = [];
(globalThis as any).__releaseNavigation = () => {
  const held = (globalThis as any).__heldNavigation.splice(0);
  for (const publish of held) publish();
};
class QuietEventSource {
  onopen = null; onmessage = null; onerror = null;
  listeners = new Map(); closed = false;
  url: string;
  id = ++sourceIdentity; generation = 0;
  constructor(url: string) {
    this.url = url;
    if (!url.startsWith('/api/navigation/view/events')) { if (overlay) return new ReaderSource(url); return; }
    sources.add(this);
    void this.refresh();
  }
  async refresh({signal}: {signal?: AbortSignal} = {}) {
    const refreshId = ++this.generation;
    const request = JSON.parse(new URL(this.url, location.href).searchParams.get('view')!);
    const receipt: any = {sourceId:this.id, refreshId, url:this.url, request, snapshot:null, dispatched:false};
    const response = await fetch(this.url.replace('/events?', '/snapshot?'), {signal});
    if (!response.ok) throw new Error('Navigation snapshot request failed: '+response.status);
    const data = await response.text();
    receipt.snapshot = JSON.parse(data);
    await new Promise<void>((resolve, reject) => {
      let timer: ReturnType<typeof setTimeout>;
      const cleanup = () => {
        clearTimeout(timer);
        signal?.removeEventListener('abort', abort);
        const held=(globalThis as any).__heldNavigation;
        const index=held.indexOf(publish); if(index>=0)held.splice(index,1);
      };
      const abort = () => { cleanup(); reject(new Error('Navigation refresh aborted')); };
      const publish = () => {
        cleanup();
        if (!signal?.aborted && !this.closed && refreshId===this.generation) {
          const frames = (globalThis as any).__navigationFrames;
          frames.push(receipt.snapshot); if (frames.length > 30) frames.shift();
          (globalThis as any).__navigationOrder.push({kind:'publication', ms:performance.now(), top:document.querySelector('[data-active-work-scroll]')?.scrollTop});
          const listener=this.listeners.get('snapshot');
          if(listener){listener({data});receipt.dispatched=true;}
        }
        resolve();
      };
      if(signal?.aborted){abort();return;}
      signal?.addEventListener('abort',abort,{once:true});
      timer=setTimeout(() => {
        if ((globalThis as any).__holdNavigation) {
          (globalThis as any).__heldNavigationFrames.push(receipt.snapshot);
          (globalThis as any).__heldNavigation.push(publish);
        } else publish();
      }, (globalThis as any).__navigationDelay ?? 0);
    });
    return receipt;
  }
  addEventListener(name: string, listener: any) { this.listeners.set(name, listener); }
  removeEventListener(name: string) { this.listeners.delete(name); }
  close() { this.closed = true; sources.delete(this); }
}
(globalThis as any).__navigationSources = () => [...sources];
(globalThis as any).__refreshNavigation = (options = {}) => Promise.all([...sources].map(source => source.refresh(options)));
(globalThis as any).EventSource = QuietEventSource;
</script>
<AppWithQueryClient />
<style>:global(html),:global(body),:global(#app){height:100%;margin:0;overflow:hidden;}</style>
`);
  await writeFile(join(scratchRoot, main), `import {mount,unmount} from 'svelte'; import Host from './${host}'; import '../../assets/tokens.css'; import '../../assets/app.css'; const app=mount(Host,{target:document.getElementById('app')!}); (globalThis as any).__unmountWorkspace=()=>unmount(app);`);
  await writeFile(html, `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="app"></div><script type="module" src="./${main}"></script></body></html>`);
  return {inputs: [html], arguments: [html], python: fileURLToPath(new URL("./workspace-utility-pane.py", import.meta.url))};
}

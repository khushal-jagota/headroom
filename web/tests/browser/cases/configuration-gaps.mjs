import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';

export async function prepareConfigurationGaps(scratchRoot) {
  const stem = `configuration-gaps-${process.pid}`;
  await writeFile(join(scratchRoot, `${stem}.svelte`), `<script lang="ts">
import { QueryClientProvider } from '@tanstack/svelte-query';
import StreamConsumer from './${stem}-consumer.svelte';
import BackendsRoute from '../src/routes/BackendsRoute.svelte';
import UsageChart from '../src/components/usage/UsageChart.svelte';
import { queryClient } from '../src/lib/queryClient';
import { startChangeStream, stopChangeStream } from '../src/lib/changeStream';
import type { UsageMetrics, UsageSummary } from '../src/lib/conversation/recordedUsageWire';
import { onMount } from 'svelte';
let shown = $state(true);
const mode = new URLSearchParams(location.search).get('mode');
(window as Window & { hideBackend?: () => void }).hideBackend = () => shown = false;
const metrics = (total: number): UsageMetrics => ({input_tokens:total,uncached_input_tokens:total,cached_input_tokens:0,cache_creation_tokens:0,output_tokens:0,reasoning_tokens:0,total_tokens:total,reported_cost_usd:null,cost_usd:null,estimated_cost_usd:null,provider_reported_cost_usd:null,category_cost_usd:null,speed_cost_usd:null,cache_savings_usd:null,speed_premium_usd:null,unpriced_records:1,model_priced_records:0,provider_reported_records:0,record_count:1,session_count:1,unknown_records:0,unknown_cost_records:1});
const summary: UsageSummary = {scope:'local_cli',days:1,timezone:'UTC',since:'2026-10-16T10:00:00Z',until:'2026-10-16T13:00:00Z',observed_at:null,pricing:{status:'unavailable',source:'fixture',fetched_at:null,known_models:0},sources:[],totals:metrics(666),providers:[],models:[],periods:[10,11,12].map((hour,index)=>({...metrics([111,222,333][index]),start:'2026-10-16T'+hour+':00:00Z',end:'2026-10-16T'+(hour+1)+':00:00Z',label:hour+':00',providers:[{...metrics([111,222,333][index]),provider:'codex'}]}))};
onMount(() => { if(mode !== 'stream') return; startChangeStream(); return stopChangeStream; });
</script>
<QueryClientProvider client={queryClient}>
{#if mode === 'chart'}<UsageChart {summary} providers={['codex']} metric="tokens" />
{:else if mode === 'stream'}<StreamConsumer />
{:else if shown}<BackendsRoute />{/if}
</QueryClientProvider>
`);
  // The mounted query must live below its actual provider.
  const consumer = join(scratchRoot, `${stem}-consumer.svelte`);
  await writeFile(consumer, `<script lang="ts">import {createQuery} from '@tanstack/svelte-query'; const data=createQuery(()=>({queryKey:['gap-visible'],queryFn:async()=>{const answer=await fetch('/api/gap-visible');return await answer.json() as {revision:number};}}));</script><output data-visible-revision>{data.data?.revision ?? 'loading'}</output>`);
  await writeFile(join(scratchRoot, `${stem}.ts`), `import {mount} from 'svelte'; import Host from './${stem}.svelte'; import '../../assets/tokens.css'; import '../../assets/app.css'; mount(Host,{target:document.getElementById('app')});`);
  const html = join(scratchRoot, `${stem}.html`);
  await writeFile(html, `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);
  return html;
}

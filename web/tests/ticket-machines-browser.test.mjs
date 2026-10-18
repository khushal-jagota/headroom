import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {writeFile,rm} from 'node:fs/promises';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {svelte} from '@sveltejs/vite-plugin-svelte';
import {scratchDirectory} from './support/scratch.mjs';
const root=join(dirname(fileURLToPath(import.meta.url)),'..');
const scratch=await scratchDirectory(), stem=`ticket-machines-${process.pid}`;
const files=['svelte','ts','html'].map(ext=>join(scratch,`${stem}.${ext}`));
let server;
try {
await writeFile(files[0],`<script lang="ts">
import {QueryClient,QueryClientProvider} from '@tanstack/svelte-query';
import SprintTicketRow from '../src/components/SprintTicketRow.svelte';
import MachineNotice from '../src/components/machines/MachineNotice.svelte';
import MachineKey from '../src/components/machines/MachineKey.svelte';
import TicketCreation from '../src/components/machines/TicketCreation.svelte';
const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
let share=$state(2),direct=$state(false),mac=$state(false),creating=$state(false);
(window as any).__fixture=(s:number,d=false,m=false,c=false)=>{share=s;direct=d;mac=m;creating=c;client.invalidateQueries();};
</script>
<QueryClientProvider {client}><MachineKey />
{#if creating}<TicketCreation onCreated={id=>(window as any).__created=id} />{:else}
<div data-scroll style="height:420px;overflow:auto;position:relative"><MachineNotice relevant={!direct || mac} />
{#each Array.from({length:direct?1:20},(_,i)=>i) as i}<SprintTicketRow locale={(direct?mac:i<share)?'macos':'vps'} ticketId={'t'+i} priority={null} title={'Ticket '+i} href={'#ticket'+i} signal={{working:true,message_for_owner:i===1}} quiet={i===19} />{/each}
<div style="height:600px">Saved completed work stays completed.</div></div>{/if}</QueryClientProvider>`);
await writeFile(files[1],`import {mount} from 'svelte';import Host from './${stem}.svelte';import '../../assets/tokens.css';import '../../assets/app.css';mount(Host,{target:document.getElementById('app')!});`);
await writeFile(files[2],`<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);
server=await createServer({root,configFile:false,plugins:[svelte()],server:{host:'127.0.0.1',port:0},logLevel:'error'}); await server.listen();
const port=server.httpServer.address().port;
const script=String.raw`
import sys,json,os
from playwright.sync_api import sync_playwright,expect
health={'mode':'remote','identity':{'executor_incarnation':'one','connection_generation':1},'connection_state':'disconnected','reconciliation_ready':False,'last_contact_at_ms':1792312320000,'last_contact_source':'vps_received_valid_executor_frame'}
moves=[]; contexts=[]; fail=False

def api(route):
 global fail
 u=route.request.url
 if '/executor/health' in u: data=health
 elif '/worker-types' in u: data={'worker_types':[{'worker_type':'coding','label':'Coding'}]}
 elif '/projects' in u: data={'projects':[]}
 elif '/creation-context' in u:
  b=route.request.post_data_json;contexts.append(b);loc=b.get('locale','vps');data={'resolved_locale':loc,'employee_backend':'codex','project_id':None,'locale_choices':[{'locale':'macos','compatible':True,'reason':None},{'locale':'vps','compatible':True,'reason':None}]}
 elif '/move' in u:
  moves.append(route.request.post_data_json)
  if fail: route.fulfill(status=409,content_type='application/json',body=json.dumps({'error':{'code':'conflict','message':'Cannot move while a turn runs.'}}));return
  data={'id':'t0'}
 elif '/tickets?detail=full&id=t0' in u: data={'id':'t0','title':'Ticket 0','locale':'macos','record_sha256':'a'*64}
 elif u.endswith('/api/tickets') and route.request.method=='POST': data={'id':'created'}
 else: raise AssertionError('Unexpected API '+u)
 route.fulfill(status=200,content_type='application/json',body=json.dumps(data))
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1100,'height':800});page.route('**/api/**',api);page.on('pageerror',lambda e: print('PAGE ERROR',str(e),flush=True));page.goto(sys.argv[1],wait_until='domcontentloaded',timeout=15000);page.set_default_timeout(7000)
 expect(page.locator('.machine-ticket-row')).to_have_count(20)
 expect(page.locator('[data-machine-notice]')).to_have_count(1)
 expect(page.locator('[data-conversation-signal="working"]')).to_have_count(17)
 expect(page.locator('[data-conversation-signal="message"]')).to_have_count(1)
 assert page.get_by_text('Last contact:',exact=False).inner_text().find(' · ')>=0
 for share in [2,10]:
  page.evaluate('(s)=>window.__fixture(s)',share)
  for width in [1100,390]:
   page.set_viewport_size({'width':width,'height':800});page.locator('[data-scroll]').evaluate('(e)=>e.scrollTop=300');page.wait_for_timeout(100)
   box=page.locator('[data-machine-notice]').bounding_box();assert box['y']>=0 and box['y']<150
   if os.environ.get('PANELS_MACHINE_ARTIFACTS'):
    page.screenshot(path=os.path.join(os.environ['PANELS_MACHINE_ARTIFACTS'],f'machines-{share}-{width}.png'),full_page=True)
  page.locator('[data-scroll]').evaluate('(e)=>e.scrollTop=0')
 page.set_viewport_size({'width':1100,'height':800});button=page.get_by_role('button',name='Runs on Mac. Show machine details').first
 assert button.bounding_box()['width']>=48 and button.bounding_box()['height']>=48
 button.focus();page.keyboard.press('Enter');expect(page.get_by_role('dialog',name='Runs on Mac')).to_be_visible();assert not moves
 page.get_by_role('button',name='Move Ticket…',exact=True).click();expect(page.get_by_role('dialog',name='Move Ticket',exact=True)).to_be_visible()
 expect(page.get_by_text('Only the machine choice changes.',exact=False)).to_be_visible();fail=True
 page.get_by_role('button',name='Move Ticket',exact=True).click();expect(page.get_by_role('alert')).to_have_text('Cannot move while a turn runs.')
 assert moves[-1]=={'locale':'vps','expected_record_sha256':'a'*64}
 expect(page.get_by_role('button',name='Move Ticket',exact=True)).to_be_disabled();page.get_by_role('button',name='Close',exact=True).click()
 page.evaluate('()=>window.__fixture(2,true,false)');expect(page.locator('[data-machine-notice]')).to_have_count(0)
 page.evaluate('()=>window.__fixture(2,true,true)');expect(page.locator('[data-machine-notice]')).to_have_count(1)
 health.update(connection_state='reconciling',reconciliation_ready=False);page.evaluate('()=>window.__fixture(2,true,true)');expect(page.locator('[data-machine-notice]')).to_have_count(1)
 health.update(connection_state='ready',reconciliation_ready=True);page.evaluate('()=>window.__fixture(2,true,true)');expect(page.locator('[data-machine-notice]')).to_have_count(0)
 page.evaluate('()=>window.__fixture(2,false,false,true)');page.get_by_label('Title',exact=True).fill('Preserved draft');page.get_by_label('Brief',exact=True).fill('Check it');page.get_by_label('Run on',exact=True).select_option('macos');expect(page.get_by_text('Resolved machine: Mac.',exact=True)).to_be_visible()
 health.update(connection_state='disconnected',reconciliation_ready=False);page.evaluate('()=>window.__fixture(2,false,false,true)');expect(page.get_by_label('Title',exact=True)).to_have_value('Preserved draft');expect(page.get_by_text('The Mac is unreachable',exact=False)).to_be_visible();page.get_by_role('button',name='Create Ticket',exact=True).click();expect.poll if False else None
 page.wait_for_function('window.__created === "created"');assert contexts[-1]['locale']=='macos'
 browser.close()
print('Machine surfaces browser checks passed')
`;
await new Promise((resolve,reject)=>{const child=spawn(process.env.PANELS_TEST_PYTHON||join(root,'..','.venv','bin','python'),['-c',script,`http://127.0.0.1:${port}/${scratch.split('/').at(-1)}/${stem}.html`],{cwd:root,stdio:'inherit'});child.on('error',reject);child.on('exit',code=>code===0?resolve():reject(Error(`browser checks ${code}`)));});
}finally{await server?.close();await Promise.all(files.map(file=>rm(file,{force:true})));}

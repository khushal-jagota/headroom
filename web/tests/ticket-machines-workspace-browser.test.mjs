import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {writeFile,rm} from 'node:fs/promises';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {svelte} from '@sveltejs/vite-plugin-svelte';
import {scratchDirectory} from './support/scratch.mjs';
const root=join(dirname(fileURLToPath(import.meta.url)),'..');
const scratch=await scratchDirectory(), stem=`ticket-machines-workspace-${process.pid}`;
const files=['svelte','ts','html'].map(ext=>join(scratch,`${stem}.${ext}`));
let server;
try {
await writeFile(files[0],`<script lang="ts">
import {QueryClient,QueryClientProvider} from '@tanstack/svelte-query';
import BoardRoute from '../src/routes/BoardRoute.svelte';
import TicketRoute from '../src/routes/TicketRoute.svelte';
const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
let direct=$state(false),id=$state('t19');
let address=$derived({selection:{kind:'ticket' as const,id},view:'tickets' as const,openFile:null});
(window as any).__fixture=(d=false,next='t19')=>{direct=d;id=next;client.invalidateQueries();};
</script>
<QueryClientProvider {client}>{#if direct}{#key id}<TicketRoute {id} />{/key}{:else}<BoardRoute {address} />{/if}</QueryClientProvider>
<style>:global(html),:global(body),:global(#app) {height:100%; margin:0;}</style>`);
await writeFile(files[1],`import {mount} from 'svelte';import Host from './${stem}.svelte';import '../../assets/tokens.css';import '../../assets/app.css';mount(Host,{target:document.getElementById('app')!});`);
await writeFile(files[2],`<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);
server=await createServer({root,configFile:false,plugins:[svelte()],server:{host:'127.0.0.1',port:0},logLevel:'error'}); await server.listen();
const port=server.httpServer.address().port;
const script=String.raw`
import sys,json,os
from urllib.parse import urlparse,parse_qs
from playwright.sync_api import sync_playwright,expect
share=2
hide_mac=False
health={'mode':'remote','identity':{'executor_incarnation':'one','connection_generation':1},'connection_state':'disconnected','reconciliation_ready':False,'last_contact_at_ms':1792312320000,'last_contact_source':'vps_received_valid_executor_frame'}
quiet=dict(awaiting_reply=False,awaiting_answer=False,awaiting_approval=False,awaiting_agent_approval=False,assigned=False,agent_state='idle')
def card(i):
 return dict(quiet,id='t'+str(i),title='Ticket '+str(i),locale='macos' if i<share else 'vps',priority='P2',deadline=None,project_id=None,project=None,group_project_id=None,group_project=None,activity_at=i,has_pending_proposal=False,ticket_status='assigned',worker_type='coding',employee_backend='codex',stage='done' if hide_mac and i<share else 'needs_brief',stage_label='Brief',gating_field='brief',gating_field_label='Brief',is_done=False,blocked=False,conversation_id=None,waiting_to_closeout=False,sprint_item_id=None,sprint_item_title=None,sprint_item_priority=None,orchestrator={'kind':'chief','id':'chief'},assigned=not (hide_mac and i<share),conversation_signal={'working':False,'message_for_owner':i==1 and not hide_mac})
def detail(i):
 r=card(i);r.update(employee_launch_model=None,employee_launch_reasoning_effort=None,employee_configuration_editable=False,ceiling='needs_brief',ceiling_holder={'kind':'owner','id':'owner'},resolved_priority_anchors={'sprint_item':None,'project':None},conversation_history=[],guidance='',field_values={'brief':'Saved brief.\n\n'+'Saved progress remains available.\n\n'*80},pending_proposal=None,artifacts=[],blocker_summary={'blocked_by':[]},record_sha256='a'*64,recap='Saved orientation')
 return r
manifest={'worker_type':'coding','label':'Coding','stages':[{'id':'needs_brief','label':'Brief','gating_field':'brief','is_terminal':False,'ownership_mode':'worker'},{'id':'done','label':'Done','gating_field':None,'is_terminal':True,'ownership_mode':None}],'fields':[{'id':'brief','label':'Brief'}],'advance':{'needs_brief':'done'},'ceiling_range':['needs_brief'],'default_ceiling':'needs_brief','worker_profile_id':'coding','default_backend':'codex','default_model':None,'default_reasoning_effort':None}
def api(route):
 u=urlparse(route.request.url);path=u.path
 if path=='/api/board': data={'columns':[{'stage':'needs_brief','cards':[card(i) for i in range(20)]}],'sprint_items':[]}
 elif path=='/api/workers': data={'workers':[],'chief_of_staff':{'label':'Chief of Staff','conversation_id':None}}
 elif path=='/api/executor/health': data=health
 elif path=='/api/worker-types': data={'worker_types':[manifest]}
 elif path=='/api/tickets': data=detail(int(parse_qs(u.query)['id'][0][1:]))
 elif path=='/api/conversation/backends': data={'backends':[]}
 elif path.endswith('/conversation/start-values'): data={'employee_backend':'codex','employee_launch_model':None,'employee_launch_reasoning_effort':None}
 else: data={}
 route.fulfill(status=200,content_type='application/json',body=json.dumps(data))
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1280,'height':900})
 errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/api/**',api);page.goto(sys.argv[1],wait_until='domcontentloaded');page.set_default_timeout(10000)
 expect(page.locator('[data-ticket-id="t19"][data-screen="ticket"]')).to_be_visible()
 for count in [2,10]:
  share=count;page.evaluate('()=>window.__fixture(false)')
  expect(page.locator('.approved-rail .machine-identity[title="Runs on Mac"]')).to_have_count(count)
  expect(page.locator('[data-machine-notice]')).to_have_count(1)
  if os.environ.get('PANELS_MACHINE_ARTIFACTS'):page.screenshot(path=os.path.join(os.environ['PANELS_MACHINE_ARTIFACTS'],f'workspace-machines-{count}-desktop-before.png'),full_page=True)
  rail=page.locator('[data-active-work-scroll]');rail.evaluate('(e)=>e.scrollTop=500');page.locator('.ticket-doc').evaluate('(e)=>e.scrollTop=400')
  expect(page.locator('[data-machine-notice]')).to_be_visible();assert page.locator('[data-machine-notice]').bounding_box()['y']<150
  if os.environ.get('PANELS_MACHINE_ARTIFACTS'):page.screenshot(path=os.path.join(os.environ['PANELS_MACHINE_ARTIFACTS'],f'workspace-machines-{count}-desktop.png'),full_page=True)
 hide_mac=True;page.evaluate('()=>window.__fixture(false)');expect(page.locator('[data-machine-notice]')).to_have_count(0)
 hide_mac=False;page.evaluate('()=>window.__fixture(false)');expect(page.locator('[data-machine-notice]')).to_have_count(1)
 page.set_viewport_size({'width':390,'height':844});expect(page.locator('.approved-rail')).not_to_be_visible();expect(page.locator('[data-machine-notice]')).to_have_count(0)
 page.evaluate('()=>window.__fixture(false,"t0")');expect(page.locator('[data-screen="ticket"][data-ticket-id="t0"]')).to_be_visible();expect(page.locator('[data-machine-notice]')).to_have_count(1)
 page.locator('.ticket-doc').evaluate('(e)=>e.scrollTop=400');expect(page.locator('[data-machine-notice]')).to_be_visible()
 if os.environ.get('PANELS_MACHINE_ARTIFACTS'):page.screenshot(path=os.path.join(os.environ['PANELS_MACHINE_ARTIFACTS'],'workspace-machine-mac-phone.png'),full_page=True)
 page.evaluate('()=>window.__fixture(true,"t19")');expect(page.locator('[data-machine-notice]')).to_have_count(0)
 page.evaluate('()=>window.__fixture(true,"t0")');expect(page.locator('[data-machine-notice]')).to_have_count(1)
 if os.environ.get('PANELS_MACHINE_ARTIFACTS'):page.screenshot(path=os.path.join(os.environ['PANELS_MACHINE_ARTIFACTS'],'direct-machine-mac-phone-before.png'),full_page=True)
 page.locator('.ticket-doc').evaluate('(e)=>e.scrollTop=400');expect(page.locator('[data-machine-notice]')).to_be_visible();assert page.locator('[data-machine-notice]').bounding_box()['y']<150;expect(page.locator('.ticket-doc .machine-heading')).to_be_visible();assert page.locator('.ticket-doc .machine-heading').bounding_box()['y']<100
 if os.environ.get('PANELS_MACHINE_ARTIFACTS'):page.screenshot(path=os.path.join(os.environ['PANELS_MACHINE_ARTIFACTS'],'direct-machine-mac-phone.png'),full_page=True)
 assert not errors,errors
 browser.close()
print('Workspace and direct Ticket machine browser checks passed')
`;
await new Promise((resolve,reject)=>{const child=spawn(process.env.PANELS_TEST_PYTHON||join(root,'..','.venv','bin','python'),['-c',script,`http://127.0.0.1:${port}/${scratch.split('/').at(-1)}/${stem}.html`],{cwd:root,stdio:'inherit'});child.on('error',reject);child.on('exit',code=>code===0?resolve():reject(Error(`browser checks ${code}`)));});
}finally{await server?.close();await Promise.all(files.map(file=>rm(file,{force:true})));}

import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const repositoryRoot = join(webRoot, "..");
const hostPath = join(scratchRoot, `unanswered-send-host-${process.pid}.svelte`);
const mainPath = join(scratchRoot, `unanswered-send-main-${process.pid}.ts`);
const indexPath = join(scratchRoot, `unanswered-send-index-${process.pid}.html`);

  await writeFile(
    hostPath,
    String.raw`<script lang="ts">
  import "../../assets/tokens.css";
  import "../../assets/app.css";
  import LiveConversation from "../src/components/conversation/LiveConversation.svelte";
  import { connectionStatus } from "../src/lib/changeStream";

  let shown = $state(true);
  (window as any).__showConversation = (value: boolean) => shown = value;
  let theServerAnswers = $state(false);
  let theSendHangs = $state(false);
  (window as any).__theServerAnswers = (answers: boolean) => (theServerAnswers = answers);
  (window as any).__theSendHangs = (hangs: boolean) => (theSendHangs = hangs);
  // What the app-wide connection says. In the real app the change stream sets this.
  (window as any).__setServerReachable = (reachable: boolean) =>
    connectionStatus.set(reachable ? "connected" : "reconnecting");

  async function sendMessage(body: any): Promise<any> {
    (window as any).__sends = [...((window as any).__sends ?? []), body];
    const delivery = (window as any).__delivery;
    if (delivery) {
      await new Promise<void>(resolve => {(window as any).__resolveDelivery = resolve;});
      if (delivery === 'unanswered') throw new Error('the connection went away');
      if (!['accepted','restored','cancelled','record-first','http-first','queued'].includes(delivery)) (window as any).__publishOutcome(body, delivery);
      return {conversation_id:'conversation-one',fate:['prompt','accepted','restored','cancelled','record-first','http-first'].includes(delivery)?'started':delivery,refusal_reason:'running_turn_changed_before_steer'};
    }
    if (theSendHangs) return new Promise(() => undefined);
    if (!theServerAnswers) throw new Error("the connection went away");
    return { conversation_id: "conversation-one", fate: "started" };
  }
</script>

<div style="height:650px;width:760px;margin:20px auto;display:flex;flex-direction:column">
{#if shown}<LiveConversation
  conversationId={new URLSearchParams(location.search).has('unstarted')?null:'conversation-one'}
  composerPlaceholder={new URLSearchParams(location.search).get('placeholder')??undefined}
  persistenceKey="unanswered-send-fixture"
  label="Worker"
  conversationState="opened"
  {sendMessage}
/>{/if}
</div>
`,
    "utf8"
  );
  await writeFile(
    mainPath,
    String.raw`import {viewSnapshot, viewChanges} from "../tests/support/conversation/view-fixture";
import type {ConversationViewLens} from "../src/lib/conversation/view/contracts";
const snapshot = {
  conversation_id: "conversation-one",
  backend_key: "codex",
  supports_steer: true,
  model: "gpt-test",
  reasoning_effort: null,
  workspace_folder: "/tmp",
  access: "full",
  role_text: null,
  identity_environment_variable_names: [],
  latest_sequence: 0,
  owner_read_through_sequence: 0,
  is_running: new URLSearchParams(location.search).has('running'),
  held_prompts: [],
  pending_permission_ask: null,
  pending_user_input: null,
  composer_catalog: []
};

const lifecycle = new URLSearchParams(location.search).has('lifecycle');
const history = lifecycle ? Array.from({length:new URLSearchParams(location.search).has('retirement')?32:8},(_,i)=>({conversation_id:'conversation-one',sequence:i+1,created_at:i+1,kind:i%2?'message_to_owner':'prompt',payload:{text:i%2?'Earlier answer with enough words to establish the reading history.':'Earlier owner message',sender_label:'owner'}})) : [];
if (lifecycle) {snapshot.latest_sequence=history.length; snapshot.owner_read_through_sequence=history.length;}
let incomplete = false;
(window as any).__pageReads = 0;
let withheldNewest:any=null;
(window as any).__setIncomplete = () => {
  incomplete=true;
  const sequence=++coverage;
  withheldNewest={kind:'row',key:'incomplete-newest',row_kind:'agent_message',order:{sequence,slot:0},source_sequence:sequence,sender_message_id:null,changed_through_sequence:sequence,created_at:sequence,turn_key:null,work_group_key:null,fold_rule:'never',body:{kind:'inline',value:{kind:'agent_message',content:[{piece:'text',text:Array.from({length:8},(_,i)=>'Actual newest admitted after history page. Paragraph '+i+'.').join('\n\n')}],to_owner:true}}};
  (window as any).__pulse();
};
let recorded:any[]=[];
let coverage=history.length;
let streamingText='';
let streamingVersion=0;
const sources:any[]=[];
function currentSnapshot(lens:ConversationViewLens, transcript=true) {
  const current=viewSnapshot('conversation-one',lens,history,transcript);
  current.page.items.push(...recorded, ...(withheldNewest&&!incomplete?[withheldNewest]:[]));
  if(incomplete)current.page.newer={projection_version:current.page.projection_version,lens,scope:current.page.scope,order:{sequence:coverage,slot:0}};
  current.coverage.through_sequence=coverage;
  current.status.revision=coverage;
  if(streamingText)current.status.live_output={runtime_epoch:'lifecycle',turn_key:'turn:lifecycle',version:streamingVersion,byte_count:streamingText.length,reference:{body_id:'live:lifecycle',version:String(streamingVersion),byte_count:streamingText.length,media_type:'text/markdown'}};
  return current;
}
(window as any).__publishOutcome=(sent:any,delivery:string)=>{
  const sequence=++coverage;
  const kind=delivery==='prompt'?'prompt':'prompt_'+delivery;
  const body:any={kind,content:sent.content,sender_label:'owner'};
  if(kind==='prompt')Object.assign(body,{mode:sent.mode,sent_at_unix_milliseconds:sent.sent_at_unix_milliseconds});
  if(kind==='prompt_refused')Object.assign(body,{reason:'running_turn_changed_before_steer',refusal_detail:'the turn had already ended'});
  recorded.push({kind:'row',key:'row:'+sequence,row_kind:kind,order:{sequence,slot:0},source_sequence:sequence,sender_message_id:sent.sender_message_id,changed_through_sequence:sequence,created_at:sequence,turn_key:null,work_group_key:null,fold_rule:'never',body:{kind:'inline',value:body}});
  for(const source of sources)source.emit('snapshot',currentSnapshot(source.lens));
};
// A no-op notification exercises the real view reader and observer reconciliation.
(window as any).__pulse=()=>{for(const source of sources)source.emit('snapshot',currentSnapshot(source.lens));};
(window as any).__publishAnswer=(text:string)=>{
  const previous=recorded.find(row=>row.key==='streamed-answer');
  const sequence=++coverage;
  const answer={kind:'row',key:'streamed-answer',row_kind:'agent_message',order:previous?.order??{sequence,slot:0},source_sequence:previous?.source_sequence??sequence,sender_message_id:null,changed_through_sequence:sequence,created_at:sequence,turn_key:null,work_group_key:null,fold_rule:'never',body:{kind:'inline',value:{kind:'agent_message',content:[{piece:'text',text}],to_owner:true}}};
  if(previous)recorded[recorded.indexOf(previous)]=answer;else recorded.push(answer);
  for(const source of sources)source.emit('snapshot',currentSnapshot(source.lens));
};
(window as any).__publishStream=(addition:string)=>{streamingText+=addition;streamingVersion++;coverage++;for(const source of sources)source.emit('snapshot',currentSnapshot(source.lens));};
(globalThis as any).__recordReads = 0;
let theServerIsDown = window.sessionStorage.getItem("server-is-down") === "yes";
(globalThis as any).__theServerIsDown = (down: boolean) => {
  theServerIsDown = down;
  window.sessionStorage.setItem("server-is-down", down ? "yes" : "no");
};
(globalThis as any).fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (theServerIsDown) throw new TypeError("Failed to fetch");
  if (/conversations\/conversation-one\/view\/body\/content/.test(url))return new Response(streamingText);
  if (/conversations\/conversation-one\/view\/receipts\?/.test(url)) {
    (globalThis as any).__recordReads += 1;
    return Response.json({conversation_id:'conversation-one',coverage:{through_sequence:coverage},receipts:recorded.map(row=>({sender_message_id:row.sender_message_id,source_sequence:row.source_sequence,fate:row.row_kind}))});
  }
  if (/conversations\/conversation-one\/events\?after=\d+$/.test(url)) {
    (globalThis as any).__recordReads += 1;
    return Response.json({ events: [] });
  }
  if (/conversations\/conversation-one$/.test(url)) return Response.json({...snapshot,latest_sequence:coverage});
  if (/owner-read$/.test(url)) { const through=JSON.parse(String(init?.body)).through_sequence; snapshot.owner_read_through_sequence=Math.max(snapshot.owner_read_through_sequence,through); return Response.json({owner_read_through_sequence:snapshot.owner_read_through_sequence}); }
  if (/conversations\/conversation-one\/view\/pages\?/.test(url)) { (window as any).__pageReads++; incomplete=false; return Response.json(currentSnapshot(new URL(url,location.origin).searchParams.get('lens') as ConversationViewLens).page); }
  throw new Error("unexpected fetch " + url);
};

class FakeEventSource {
  static opened = 0;
  listeners=new Map<string,Array<(event:MessageEvent<string>)=>void>>();
  lens:ConversationViewLens='focus';
  constructor(url: string) {
    if (/conversations\/conversation-one\/view\/tail/.test(url)) {
      FakeEventSource.opened += 1;
      const args=new URL(url,location.origin).searchParams;
      this.lens=args.get('lens') as ConversationViewLens; sources.push(this);
      queueMicrotask(()=>this.emit('snapshot',currentSnapshot(this.lens,args.get('transcript')==='true')));
    }
  }
  addEventListener(type:string,listener:(event:MessageEvent<string>)=>void): void {
    this.listeners.set(type,[...(this.listeners.get(type)??[]),listener]);
  }
  emit(type:string,data:unknown):void{for(const listener of this.listeners.get(type)??[])listener(new MessageEvent(type,{data:JSON.stringify(data)}));}
  close(): void {}
}

(globalThis as any).EventSource = FakeEventSource;
(globalThis as any).__tailOpened = () => FakeEventSource.opened;

async function start(): Promise<void> {
  const { mount } = await import("svelte");
  const { default: Host } = await import("./${hostPath.split("/").at(-1)}");
  mount(Host, { target: document.getElementById("app")! });
}
void start();
`,
    "utf8"
  );
  await writeFile(
    indexPath,
    `<!doctype html><html><body><div id="app"></div><script type="module" src="./${mainPath
      .split("/")
      .at(-1)}"></script></body></html>`,
    "utf8"
  );


  return {inputs: [indexPath], arguments: [indexPath], python: fileURLToPath(new URL("./live-conversation-unanswered-send.py", import.meta.url))};
}

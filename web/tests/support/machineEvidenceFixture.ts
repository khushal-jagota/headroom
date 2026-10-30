import { queryClient } from '../../src/lib/queryClient';
import { VIEW_PROJECTION_VERSION } from '../../src/lib/conversation/view/contracts';

/** Finite transport inside the existing conversation browser host. No backend or Mac starts. */
export function installMachineEvidenceFixture(): void {
  const originalFetch = globalThis.fetch;
  const child = {executor_incarnation:'fixture-mac',child_id:'child',conversation_id:'machine-conversation'};
  const publicId = 'public-turn:permission:native-ask';
  let kind:'permission'|'user_input' = 'permission';
  let requestGone = false;
  let answered = false;
  let applied = false;
  let connected = false;
  const posts:unknown[]=[];
  const creates:unknown[]=[];
  const drafts:unknown[]=[];
  const listeners:Array<(value:unknown)=>void>=[];
  const options=[{option_id:'yes',label:'Allow once',option_kind:'allow_once'}];
  const questions=[{question_id:'one',header:'First',question:'Choose one',options:[{label:'One',description:''}],multi_select:false,allow_other:true},
    {question_id:'two',header:'Second',question:'Choose two',options:[{label:'Two',description:''}],multi_select:false,allow_other:false}];
  const id=()=>kind==='permission'?publicId:'public-turn:user_input:native-questions';
  const status=()=>({diagnostics_expired:false,runtime_epoch:'fixture',revision:1,activity_at_unix_milliseconds:1,authority:'current',turn_key:'turn:1',turn_running:true,stopped_without_ending:false,
    pending_permission:kind==='permission'&&!answered?{kind,request_id:id(),row_key:'e2',source_sequence:2}:null,pending_user_input:kind==='user_input'&&!answered&&!requestGone?{kind,request_id:id(),row_key:'e2',source_sequence:2}:null,
    plan:null,live_output:null,live_tool_key:null,live_tool_progress:null,
    rest:{latest_happened:null,latest_turn:null,latest_tool:null,permission_title_excerpt:null,latest_owner_message_sequence:null,latest_owner_message_preview:null,last_boundary_failed:false,
      plan_progress:{total_count:0,completed_count:0,current_position:null,current_text_excerpt:null,has_unfinished_entry:false}}});
  const current=()=>({conversation_id:child.conversation_id,backend_key:'codex',supports_steer:false,model:null,reasoning_effort:null,workspace_folder:'/fixture',access:'full',latest_sequence:2,owner_read_through_sequence:0,is_running:true,held_prompts:[],
    pending_permission_ask:kind==='permission'&&!answered&&!requestGone?{ask_id:id(),title:'Allow fixture?',detail:null,options}:null,
    pending_user_input:kind==='user_input'&&!answered&&!requestGone?{request_id:id(),questions}:null});
  const page=(lens:string)=>({conversation_id:child.conversation_id,protocol_version:1,projection_version:VIEW_PROJECTION_VERSION,lens,scope:{kind:'timeline',key:null},older:null,newer:null,body_bytes:0,
    items:[{kind:'turn',key:'turn:1',order:{sequence:1,slot:1},changed_through_sequence:2,settled:false,stopped:false,started_at_unix_milliseconds:Date.now(),ending:null,is_latest:true,duration_seconds:null,tool_call_count:0,folded_message_count:0,latest_message_key:null},
      ...(answered?[{kind:'row',key:'e3',row_kind:kind==='permission'?'permission_ask':'user_input',source_sequence:3,sender_message_id:null,created_at:1,fold_rule:'never',order:{sequence:3,slot:0},changed_through_sequence:applied?4:3,turn_key:'turn:1',work_group_key:null,body:{kind:'inline',value:kind==='permission'?{kind:'permission_ask',ask_id:id(),title:'Allow fixture?',detail:null,options,state:'answered',dead_reason:null,answered_option_label:'Allow once',answer_fate:applied?'applied':'saved'}:{kind:'user_input',request_id:id(),questions,state:'answered',dead_reason:null,answers:[],failure_detail:null,answer_fate:applied?'applied':'saved'}}}]:[])]});
  const snapshot=(lens:string)=>({conversation_id:child.conversation_id,protocol_version:1,projection_version:VIEW_PROJECTION_VERSION,lens,coverage:{through_sequence:2},status:status(),page:page(lens),entry_target:null,message_receipts:[]});
  globalThis.fetch=async (input,init)=>{
    const url=new URL(String(input),location.origin);
    if(url.pathname==='/api/resource-body' && url.searchParams.get('key')==='/api/worker-types')return Response.json({key:'/api/worker-types',epoch:'fixture',revision:'1',body:{worker_types:[{worker_type:'coding',label:'Coding'}]}});
    if(url.pathname==='/api/worker-types')return Response.json({worker_types:[{worker_type:'coding',label:'Coding'}]});
    if(url.pathname==='/api/projects')return Response.json({projects:[]});
    if(url.pathname==='/api/managers')return Response.json({managers:[{id:'manager-fixture',title:'Manager fixture'}]});
    if(url.pathname==='/api/tickets/creation-context'){
      const draft=JSON.parse(String(init?.body));drafts.push(draft);
      return Response.json({resolved_locale:draft.locale??'vps',employee_backend:'codex',project_id:draft.manager.kind==='manager'?'inherited':null,
        locale_choices:[{locale:'vps',compatible:true,reason:null},{locale:'macos',compatible:true,reason:null}]});
    }
    if(url.pathname==='/api/tickets'){
      if(init?.method==='POST'){creates.push(JSON.parse(String(init.body)));return Response.json({id:'created-worker'});}
      return Response.json({id:'mac-worker',title:'Mac Worker',locale:'macos',record_sha256:'a'.repeat(64)});
    }
    if(url.pathname==='/api/tickets/mac-worker/move')return Response.json({error:{code:'conflict',message:'Source state unknown.'}},{status:409});
    if(url.pathname==='/api/executor/health')return Response.json({mode:'remote',identity:connected?{executor_incarnation:child.executor_incarnation,connection_generation:1}:null,connection_state:connected?'ready':'disconnected',reconciliation_ready:connected,last_contact_at_ms:1,last_contact_source:'vps_received_valid_executor_frame'});
    if(url.pathname==='/api/conversation/backends')return Response.json({backends:[]});
    if(url.pathname.includes('/composer-catalog'))return Response.json({entries:[]});
    if(url.pathname.includes('/conversations/machine-conversation')){
      if(url.pathname.endsWith('/permission-answers')||url.pathname.endsWith('/user-input-answers')){
        posts.push(JSON.parse(String(init?.body)));answered=true;queueMicrotask(()=>{for(const listener of listeners)listener(snapshot('focus'));});return Response.json({fate:'saved'});
      }
      if(url.pathname.endsWith('/view/pages'))return Response.json(page(url.searchParams.get('lens')??'focus'));
      if(url.pathname.endsWith('/owner-read'))return Response.json({owner_read_through_sequence:0});
      if(url.pathname.endsWith('/view/receipts'))return Response.json({conversation_id:child.conversation_id,coverage:{through_sequence:2},receipts:[]});
      return Response.json(current());
    }
    return originalFetch(input,init);
  };
  class FixtureSource {
    constructor(url:string){if(url.includes('/view/tail')){
      const listener=(value:unknown)=>this.emit('snapshot',value);listeners.push(listener);
      queueMicrotask(()=>listener(snapshot(new URL(url,location.origin).searchParams.get('lens')??'focus')));
    }}
    private callbacks=new Map<string,((event:MessageEvent)=>void)[]>();
    addEventListener(name:string,callback:(event:MessageEvent)=>void){this.callbacks.set(name,[...(this.callbacks.get(name)??[]),callback]);}
    emit(name:string,value:unknown){for(const callback of this.callbacks.get(name)??[])callback(new MessageEvent(name,{data:JSON.stringify(value)}));}
    close(){}
  }
  globalThis.EventSource=FixtureSource as unknown as typeof EventSource;
  (window as any).__machine={posts,creates,drafts,async change(value:{requestGone?:boolean;applied?:boolean;connected?:boolean;questions?:boolean}){
    if(value.requestGone!==undefined)requestGone=value.requestGone;
    if(value.applied!==undefined)applied=value.applied;
    if(value.connected!==undefined)connected=value.connected;
    if(value.questions){kind='user_input';answered=false;applied=false;requestGone=false;}
    for(const listener of listeners)listener(snapshot('focus'));
    await queryClient.invalidateQueries({queryKey:['executor-health']});
  }};
}

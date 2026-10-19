/** A finite indexed-page transport for browser lifecycle and scroll admission checks. */
import type {ConversationViewItem,ConversationViewPage,ConversationViewStatus,ViewPageCursor} from '../../src/lib/conversation/view/contracts';
export function installConversationViewReaderFixture():void {
  const id='bounded-reader';
  let sequence=2000;
  const calls:string[]=[];
  (window as any).__viewCalls=calls;
  const status:ConversationViewStatus={runtime_epoch:'fixture',revision:0,activity_at_unix_milliseconds:null,authority:'current',turn_key:null,turn_running:false,stopped_without_ending:false,pending_permission:null,pending_user_input:null,plan:null,live_output:null,live_tool_key:null,live_tool_progress:null,rest:{latest_happened:{kind:'agent_message',sender_label:null,excerpt:'Latest owner answer'},latest_turn:null,latest_tool:null,permission_title_excerpt:null,latest_owner_message_sequence:2000,last_boundary_failed:false,plan_progress:{total_count:0,completed_count:0,current_position:null,current_text_excerpt:null,has_unfinished_entry:false}}};
  function item(n:number):ConversationViewItem{return{kind:'row',key:'e'+n,row_kind:'agent_message',order:{sequence:n,slot:0},source_sequence:n,sender_message_id:null,changed_through_sequence:n,created_at:n,turn_key:null,work_group_key:null,fold_rule:'never',body:{kind:'inline',value:{kind:'agent_message',to_owner:true,content:[{piece:'text',text:`Owner answer ${n}. `+'The same message remains reachable without all previous bodies in memory. '.repeat(4)}]}}};}
  function page(url:URL):ConversationViewPage{
    const lens=url.searchParams.get('lens')==='full'?'full':'focus';
    const cursor=JSON.parse(url.searchParams.get('cursor')??'null') as ViewPageCursor|null;
    const direction=url.searchParams.get('direction')??'older';
    const end=cursor===null?sequence:direction==='older'?cursor.order.sequence-(cursor.order.slot>0?0:1):Math.min(sequence,cursor.order.sequence+100);
    const start=Math.max(1,end-99);
    const items=Array.from({length:end-start+1},(_,index)=>{
      const row=item(start+index);
      if(row.source_sequence>=1997 && row.source_sequence<=1999){
        row.row_kind='prompt';
        row.body={kind:'inline',value:{kind:'prompt',sender_label:row.source_sequence===1997?'Chief':'owner',mode:'queue',content:[{piece:'text',text:row.source_sequence===1999?'A long owner prompt retains its existing width limit. '.repeat(20):'Short prompt.'}]}};
      }
      return row;
    });
    const position=(n:number):ViewPageCursor=>({projection_version:1,lens,scope:{kind:'timeline',key:null},order:{sequence:n,slot:0}});
    return{conversation_id:id,protocol_version:1,projection_version:1,lens,scope:{kind:'timeline',key:null},items,older:start>1?position(start):null,newer:end<sequence?position(end):null,body_bytes:new TextEncoder().encode(JSON.stringify(items)).byteLength};
  }
  const originalFetch=window.fetch;
  window.fetch=async(input,init)=>{
    const value=String(input);if(!value.startsWith('/api/conversation/'))return originalFetch(input,init);
    calls.push(value);const url=new URL(value,location.origin);
    if(url.pathname.endsWith('/view/pages'))return Response.json(page(url));
    if(url.pathname.endsWith('/view/receipts'))return Response.json({conversation_id:id,coverage:{through_sequence:sequence},receipts:[]});
    if(url.pathname.endsWith('/owner-read'))return Response.json({owner_read_through_sequence:JSON.parse(String(init?.body)).through_sequence});
    return Response.json({conversation_id:id,backend_key:'codex',supports_steer:true,model:null,reasoning_effort:null,workspace_folder:'/fixture',access:'full',role_text:null,identity_environment_variable_names:[],latest_sequence:sequence,owner_read_through_sequence:sequence,is_running:false,held_prompts:[],pending_permission_ask:null,pending_user_input:null,composer_catalog:[]});
  };
  class Source extends EventTarget{
    onerror:null|(()=>void)=null;closed=false;
    constructor(raw:string){super();calls.push(raw);const url=new URL(raw,location.origin);queueMicrotask(()=>{
      if(this.closed)return;
      const result=page(url);if(url.searchParams.get('transcript')==='false')result.items=[];
      const after=url.searchParams.get('after');
      const value=after===null?{page:result,coverage:{through_sequence:sequence},status}:{kind:'changes',conversation_id:id,protocol_version:1,projection_version:1,lens:result.lens,from_sequence:Number(after),coverage:{through_sequence:sequence},upserts:[],scope_changes:[],removed_keys:[],invalidated_scopes:[],status};
      this.dispatchEvent(new MessageEvent(after===null?'snapshot':'changes',{data:JSON.stringify(value)}));
    });}
    close(){this.closed=true;}
  }
  window.EventSource=Source as unknown as typeof EventSource;
}

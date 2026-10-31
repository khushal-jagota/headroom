/** Small transport fixture, not a second semantic reducer. Backend parity tests own that proof. */
import type {ConversationViewLens, ConversationViewSnapshot, ConversationViewRow, ViewRowBody, ViewTailChanges} from '../../../src/lib/conversation/view/contracts';
import { VIEW_PROJECTION_VERSION } from '../../../src/lib/conversation/view/contracts';
export type FixtureEvent = {conversation_id:string;sequence:number;kind:string;created_at:number;
  payload:Record<string,unknown>};
export function viewSnapshot(id:string,lens:ConversationViewLens,rows:FixtureEvent[],transcript=true):ConversationViewSnapshot {
  const items:ConversationViewRow[]=[];
  for(const row of rows){
    let body:ViewRowBody;
    if(row.kind==='message_to_owner'||row.kind==='agent_message'){
      if(lens==='focus'&&row.kind==='agent_message')continue;
      body={kind:'agent_message',content:[{piece:'text',text:String(row.payload.text??'')}],to_owner:row.kind==='message_to_owner'};
    }else if(row.kind==='prompt')body={kind:'prompt',content:[{piece:'text',text:String(row.payload.text??'')}],sender_label:String(row.payload.sender_label??'owner'),mode:'queue',sent_at_unix_milliseconds:row.created_at};
    else if(row.kind==='tool_call_started'){
      if(lens==='focus')continue;
      body={kind:'tool_call',tool_call_id:String(row.payload.tool_call_id),title:String(row.payload.title),tool_kind:String(row.payload.tool_kind),detail:null,started_detail:null,capped_detail_sequence:null,status:'running'};
    }else throw new Error('Unsupported fixture event '+row.kind);
    items.push({kind:'row',key:'row:'+row.sequence,row_kind:body.kind,order:{sequence:row.sequence,slot:0},source_sequence:row.sequence,sender_message_id:null,changed_through_sequence:row.sequence,created_at:row.created_at,turn_key:null,work_group_key:null,fold_rule:'never',body:{kind:'inline',value:body}});
  }
  const latest=rows.at(-1);
  const coverage={through_sequence:Math.max(0,...rows.map(row=>row.sequence))};
  return {page:{conversation_id:id,protocol_version:1,projection_version:VIEW_PROJECTION_VERSION,lens,scope:{kind:'timeline',key:null},items:transcript?items:[],older:null,newer:null,body_bytes:JSON.stringify(items).length},coverage,
    status:{diagnostics_expired:false,runtime_epoch:'fixture',revision:coverage.through_sequence,activity_at_unix_milliseconds:null,authority:'current',turn_key:null,turn_running:false,stopped_without_ending:false,pending_permission:null,pending_user_input:null,plan:null,live_output:null,live_tool_key:null,live_tool_progress:null,
      rest:{latest_happened:latest?{kind:latest.kind==='prompt'?'prompt':'agent_message',sender_label:null,excerpt:String(latest.payload.text??latest.payload.title??'')}:null,latest_turn:null,latest_tool:null,permission_title_excerpt:null,latest_owner_message_preview:rows.filter(row=>row.kind==='message_to_owner').at(-1)?.payload.text as string | undefined ?? null,latest_owner_message_sequence:Math.max(0,...rows.filter(row=>row.kind==='message_to_owner').map(row=>row.sequence)),last_boundary_failed:false,plan_progress:{total_count:0,completed_count:0,current_position:null,current_text_excerpt:null,has_unfinished_entry:false}}}};
}
export function viewChanges(snapshot:ConversationViewSnapshot,from:number):ViewTailChanges {
  return {kind:'changes',conversation_id:snapshot.page.conversation_id,protocol_version:1,projection_version:VIEW_PROJECTION_VERSION,lens:snapshot.page.lens,from_sequence:from,coverage:snapshot.coverage,upserts:snapshot.page.items,scope_changes:[{scope:snapshot.page.scope,item_keys:snapshot.page.items.map(item=>item.key),removed_keys:[]}],removed_keys:[],invalidated_scopes:[],status:snapshot.status};
}

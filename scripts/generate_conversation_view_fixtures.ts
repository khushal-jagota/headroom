import { writeFileSync } from 'node:fs';
import { emptyConversationFeed, feedWithCommittedEvents } from '../web/src/lib/conversation/feed';
import { transcriptRows } from '../web/src/lib/conversation/transcript';
import { conversationFeedForLens, conversationRowsForLens, conversationThreadItemsForLens } from '../web/src/lib/conversation/lens';
import { restLineFrom } from '../web/src/lib/conversation/restLine';
import { taskProgressFrom } from '../web/src/lib/conversation/taskProgress';
import type { ConversationEvent } from '../web/src/lib/conversation/wire';
const owner = {kind: 'owner', id:'owner'};
const worker = {kind: 'ticket', id:'fixture-worker'};
const content = (text: string) => [{piece:'text',text}];
const prompt = (text:string, extra={}) => ({sender_label:'owner',content:content(text),mode:'queue',sender:owner,...extra});
const message = (text:string) => ({content:content(text),recipient:owner,sender:worker,sender_label:'worker'});
const end = (ending='completed') => ({ending,error_summary:null});
const cases: Array<{name:string; stopped?:boolean; events: Array<[string,unknown]>}> = [
 {name:'owner-visible-turn-work-and-final-answer',events:[
 ['prompt',prompt('Please work')],['agent_message',{content:content('Commentary')}],
 ['tool_call_started',{tool_call_id:'tool-a',title:'Read file',tool_kind:'read',detail:'a.txt'}],
 ['tool_call_finished',{tool_call_id:'tool-a',tool_call_status:'completed',detail:'File contents'}],
 ['message_to_owner',message('First message')],['message_to_owner',message('Final answer')],['turn_ended',end()]]},
 {name:'hidden-prompt-turn-and-owner-steer',events:[
 ['prompt',prompt('worker instruction',{sender:worker,sender_label:'worker'})],
 ['message_to_owner',message('An owner message from a hidden turn')],
 ['prompt',prompt('owner steer',{mode:'steer'})],['message_to_owner',message('The final answer')],['turn_ended',end()],
 ['prompt',prompt('Next owner turn')],['message_to_owner',message('Next reply')],['turn_ended',end()]]},
 {name:'legacy-owner-and-forged-label',events:[
 ['prompt',{content:content('Legacy owner'),sender_label:'owner',mode:'queue'}],['turn_ended',end()],
 ['prompt',prompt('Not owner',{sender:worker,sender_label:'owner'})],['turn_ended',end('failed')],
 ['prompt',{content:content('Explicit recipient is not legacy'),sender_label:'owner',mode:'queue',recipient:worker}],['turn_ended',end()]]},
 {name:'settlement-and-reconciliation',events:[
 ['prompt_delivery_uncertain',prompt('Uncertain',{sender_message_id:'same'})],
 ['prompt_delivery_uncertain',prompt('Another uncertainty')],
 ['prompt',prompt('Delivered',{sender_message_id:'same',reconciles_sequence:1})],
 ['prompt',prompt('Reconciled unminted',{mode:'steer',reconciles_sequence:2})],
 ['prompt_delivery_refused',{...prompt('Refused',{sender_message_id:'refused'}),refusal_reason:'model_resolution_failed',refusal_detail:'The selected model is disabled'}],
 ['prompt_discarded',prompt('Discarded',{sender_message_id:'refused'})],['turn_ended',end()]]},
 {name:'asks-answer-and-turn-expiry',events:[
 ['prompt',prompt('Work')],
 ['permission_asked',{ask_id:'ask-a',title:'Continue?',detail:null,options:[{option_id:'yes',label:'Yes',option_kind:'allow_once'}]}],
 ['permission_answered',{ask_id:'ask-a',option_id:'yes'}],
 ['user_input_requested',{request_id:'input-a',questions:[{question_id:'q',header:'Pick',question:'Which?',options:[{label:'A',description:'A option'}],multi_select:false,allow_other:true}]}],
 ['user_input_answered',{request_id:'input-a',answers:{q:{answers:['A']}}}],
 ['permission_asked',{ask_id:'ask-b',title:'Other?',detail:'Detail',options:[]}],
 ['user_input_requested',{request_id:'input-b',questions:[]}],['turn_ended',end('interrupted')]]},
 {name:'failed-input-without-request-and-missing-reply',events:[
 ['prompt',prompt('Start')],['user_input_failed',{request_id:'malformed',detail:'Invalid input request'}],
 ['explicit_reply_missing',{prompt_sender:owner}],['turn_ended',{ending:'failed',error_summary:'Failed work'}]]},
 {name:'stopped-without-ending',stopped:true,events:[
 ['prompt',prompt('Start')],['permission_asked',{ask_id:'pending',title:'Permission',detail:null,options:[]}],
 ['user_input_requested',{request_id:'pending-input',questions:[]}],['message_to_owner',message('Before stop')]]},
 {name:'tool-orphans-plan-and-work-run-boundaries',events:[
 ['tool_call_finished',{tool_call_id:'orphan',tool_call_status:'failed',detail:'No start'}],
 ['prompt',prompt('Start')],
 ['tool_call_started',{tool_call_id:'one',title:'One',tool_kind:'other',detail:'start'}],
 ['plan_updated',{entries:[{text:'A',status:'completed'},{text:'B',status:'in_progress'}]}],
 ['token_usage',{input_tokens:10,output_tokens:5,cached_input_tokens:2,cost_usd:0.01}],
 ['tool_call_started',{tool_call_id:'two',title:'Two',tool_kind:'other',detail:null}],
 ['agent_message',{content:content('Commentary between calls')}],
 ['tool_call_started',{tool_call_id:'three',title:'Three',tool_kind:'other',detail:null}],
 ['tool_call_finished',{tool_call_id:'one',tool_call_status:'completed',detail:'capped'}],
 ['context_compacted',{}],['model_changed',{model:'model',reasoning_effort:'high'}],
 ['message_to_owner',message('Answer')],['turn_ended',end()]]},
 {name:'simultaneous-pending-permission-and-input',events:[
 ['prompt',prompt('Work')],
 ['permission_asked',{ask_id:'permission',title:'Continue?',detail:null,options:[]}],
 ['user_input_requested',{request_id:'question',questions:[]}]]},
 {name:'running-multiple-owner-messages-and-time-skew',events:[
 ['prompt',prompt('Start',{sent_at_unix_milliseconds:9999999999999})],
 ['message_to_owner',message('First')],['message_to_owner',message('Second')],
 ['prompt',prompt('Steer',{mode:'steer',sent_at_unix_milliseconds:0})]]}
];
const fixtures=cases.map(c=>{
 const events=c.events.map(([kind,payload],i)=>({conversation_id:'fixture',sequence:i+1,created_at:100+i,kind,payload})) as ConversationEvent[];
 const feed=feedWithCommittedEvents(emptyConversationFeed(),events);
 const rows=transcriptRows(feed,{turnStoppedWithoutAnEnding:c.stopped===true});
 return {name:c.name,stopped:c.stopped===true,owner_sender_label:'owner',events,expected:Object.fromEntries((['focus','full'] as const).map(lens=>{
 const visible=conversationRowsForLens(transcriptRows(conversationFeedForLens(feed,lens,'owner'),{turnStoppedWithoutAnEnding:c.stopped===true}),lens);
 return [lens,{rows:visible,items:conversationThreadItemsForLens(rows,visible,lens),rest:restLineFrom(rows,'owner',taskProgressFrom(rows),{visibleRows:visible,lens},0)}];
 }))};
});
writeFileSync(new URL('../tests/fixtures/conversation_views.json', import.meta.url),JSON.stringify(fixtures,null,2)+'\n');
console.log(JSON.stringify(fixtures.map(f=>({name:f.name,focus:f.expected.focus.items.length,full:f.expected.full.items.length}))));

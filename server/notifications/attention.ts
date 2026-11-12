import type { TransactionSql } from '../core/database/contracts.ts';
import { capture_gate } from '../attention/history.ts';
import { workflow_from_row } from '../worker_types/store.ts';
import { readAllDurableFacts } from '../conversation/publication/durable.ts';
export interface ConversationAttention {unread_message:boolean;pending_ask:boolean;errored:boolean;latest_sequence:number;occurred_at:number}
export function capture_ticket_attention(sql:TransactionSql,id:string,now:number,options:{conversation_result?:ConversationAttention;worker_type_definition?:ReturnType<typeof workflow_from_row>}={}):void {
 const row=sql.get<{id:string;stage:string;workflow_json:string;worker_step_claim:string;pending_proposal:string|null;ceiling_holder:string;conversation_id:string|null}>('SELECT * FROM tickets WHERE id=?',[id]);if(!row)return;
 const workflow=options.worker_type_definition??workflow_from_row(row);
 const durable=readAllDurableFacts(sql).find(r=>r.conversation_id===row.conversation_id)?.durable;
 // Notification asks are durable pending requests, unlike runtime navigation attention.
 const pending=row.conversation_id ? !!sql.get(`SELECT 1 FROM conversation_events asked WHERE asked.conversation_id=? AND asked.kind IN ('permission_asked','user_input_requested') AND NOT EXISTS (SELECT 1 FROM conversation_events answered WHERE answered.conversation_id=asked.conversation_id AND answered.sequence>asked.sequence AND ((asked.kind='permission_asked' AND answered.kind='permission_answered' AND json_extract(answered.payload,'$.ask_id')=json_extract(asked.payload,'$.ask_id')) OR (asked.kind='user_input_requested' AND answered.kind IN ('user_input_answered','user_input_failed') AND json_extract(answered.payload,'$.request_id')=json_extract(asked.payload,'$.request_id')) OR answered.kind='turn_ended')) LIMIT 1`,[row.conversation_id]):false;
 const conversation=options.conversation_result;
 const flags={awaiting_reply:conversation?.unread_message??durable?.message_for_owner??false,awaiting_answer:conversation?.pending_ask??pending,awaiting_approval:row.worker_step_claim!=='errored' && row.pending_proposal!==null && JSON.parse(row.ceiling_holder).kind==='owner',assigned:workflow.stages.find(s=>s.id===row.stage)?.ownership_mode==='user',errored:row.worker_step_claim==='errored'||(conversation?.errored??durable?.last_turn_failed??false)};
 for(const [kind,active] of Object.entries(flags)) {
 const prior=sql.get<{active:number;generation:number}>('SELECT active,generation FROM notification_attention_state WHERE subject_kind=? AND subject_id=? AND notification_type=?',['ticket',id,kind]);
 if(prior && !!prior.active===active)continue;
 let generation=prior?.generation??0;
 if(kind==='awaiting_approval')capture_gate(sql,{ticket_id:id,active,now});
 if(active){generation++;sql.run('INSERT INTO notification_attention_edges(subject_kind,subject_id,notification_type,generation,occurred_at) VALUES (?,?,?,?,?)',['ticket',id,kind,generation,now]);}
 sql.run('INSERT INTO notification_attention_state(subject_kind,subject_id,notification_type,active,generation) VALUES (?,?,?,?,?) ON CONFLICT(subject_kind,subject_id,notification_type) DO UPDATE SET active=excluded.active,generation=excluded.generation',['ticket',id,kind,Number(active),generation]);
 }
}

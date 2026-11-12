import type { TransactionSql } from '../core/database/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import { python_json_dumps } from '../worker_types/store.ts';
function installed(sql:TransactionSql):boolean {return !!sql.get("SELECT 1 FROM sqlite_master WHERE type='table' AND name='attention_history_metadata'");}
function destination(sql:TransactionSql,id:string):unknown {
 const row=sql.get<{id:string;title:string;project_id:string|null;manager_id:string|null}>("SELECT t.id,t.title,CASE WHEN t.manager_kind='manager' THEN m.project_id ELSE t.project_id END AS project_id,t.manager_id FROM tickets t LEFT JOIN managers m ON m.id=t.manager_id WHERE t.id=?",[id]);
 return row ? {kind:'ticket',id:row.id,title:row.title,url:`/#/workspace/${id}`,project_id:row.project_id,manager_id:row.manager_id,goal_id:null,attribution_basis:'recorded'}:null;
}
export function capture_gate(sql:TransactionSql,{ticket_id,active,now}:{ticket_id:string;active:boolean;now:number}):void {
 if(!installed(sql))return;
 const opened=sql.get<{id:number}>('SELECT id FROM attention_gate_intervals WHERE ticket_id=? AND closed_at IS NULL',[ticket_id]);
 if(!active){if(opened)sql.run('UPDATE attention_gate_intervals SET closed_at=? WHERE id=?',[now,opened.id]);return;}
 if(opened)return;
 const target=destination(sql,ticket_id);if(!target)return;
 sql.run('INSERT INTO attention_gate_intervals(ticket_id,destination_json,opened_at,observed_since) VALUES (?,?,?,?)',[ticket_id,python_json_dumps(target,false,true),now,now]);
}
export function record_approval(sql:TransactionSql,{ticket_id,field,principal,method,now}:{ticket_id:string;field:string;principal:Principal;method:string;now:number}):void {
 if(!installed(sql))return;const target=destination(sql,ticket_id);if(!target)return;
 sql.run('INSERT INTO attention_approval_events(ticket_id,field,actor_kind,actor_id,method,occurred_at,destination_json) VALUES (?,?,?,?,?,?,?)',[ticket_id,field,principal.kind,principal.id,method,now,python_json_dumps(target,false,true)]);
}
